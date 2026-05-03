#!/usr/bin/env python3
"""
Cross-Model Harness: Code constraint experiment across frontier models via OpenRouter.

Uses OpenRouter API (OpenAI-compatible) to test diagonal cost predictions on:
- GPT-4o (OpenAI)
- Gemini 1.5 Pro (Google)
- Llama 3.1 70B (Meta, open-weight large)
- DeepSeek-V3 (DeepSeek, open-weight frontier)

Same deterministic verifiers as main experiment. Shows the cliff structure
persists across providers---the bound is model-agnostic.

Setup:
  1. pip install openai
  2. Get key from https://openrouter.ai/keys
  3. set OPENROUTER_API_KEY=sk-or-...

Use --dry-run to validate setup without API calls.

Base: supplementary/experiments/code_constraint_experiment.py
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Task & verifier definitions (same 12 tasks as main experiment)
# ---------------------------------------------------------------------------

TASKS = [
    {
        "id": "factorial",
        "description": "Write a Python function factorial(n) that returns the factorial of n using recursion or iteration.",
        "test": "assert factorial(0)==1 and factorial(5)==120 and factorial(1)==1",
    },
    {
        "id": "fibonacci",
        "description": "Write a Python function fibonacci(n) that returns the nth Fibonacci number (0-indexed: fib(0)=0, fib(1)=1).",
        "test": "assert fibonacci(0)==0 and fibonacci(1)==1 and fibonacci(10)==55",
    },
    {
        "id": "fizzbuzz",
        "description": "Write a Python function fizzbuzz(n) that returns a list of strings for 1..n: 'FizzBuzz' if divisible by 15, 'Fizz' if by 3, 'Buzz' if by 5, else the number as string.",
        "test": "assert fizzbuzz(15)[-1]=='FizzBuzz' and fizzbuzz(5)==['1','2','Fizz','4','Buzz']",
    },
    {
        "id": "flatten",
        "description": "Write a Python function flatten(lst) that recursively flattens a nested list. E.g. flatten([1,[2,[3]],4]) == [1,2,3,4].",
        "test": "assert flatten([1,[2,[3]],4])==[1,2,3,4] and flatten([])==[]",
    },
    {
        "id": "caesar",
        "description": "Write a Python function caesar(text, shift) that applies Caesar cipher shifting each letter by 'shift' positions (wrapping). Non-letters unchanged.",
        "test": "assert caesar('abc',1)=='bcd' and caesar('xyz',3)=='abc' and caesar('Hello!',0)=='Hello!'",
    },
    {
        "id": "find_max",
        "description": "Write a Python function find_max(lst) that returns the maximum value in a list without using built-in max().",
        "test": "assert find_max([3,1,4,1,5,9])==9 and find_max([-1,-5,-2])==-1 and find_max([42])==42",
    },
    {
        "id": "count_vowels",
        "description": "Write a Python function count_vowels(s) that returns the number of vowels (a,e,i,o,u, case-insensitive) in string s.",
        "test": "assert count_vowels('hello')==2 and count_vowels('AEIOU')==5 and count_vowels('xyz')==0",
    },
    {
        "id": "is_palindrome",
        "description": "Write a Python function is_palindrome(s) that returns True if s is a palindrome (ignoring case and spaces).",
        "test": "assert is_palindrome('racecar')==True and is_palindrome('hello')==False and is_palindrome('A man a plan a canal Panama'.replace(' ',''))==True",
    },
    {
        "id": "count_occurrences",
        "description": "Write a Python function count_occurrences(lst) that returns a dict mapping each element to its count.",
        "test": "assert count_occurrences([1,2,2,3,3,3])=={1:1,2:2,3:3} and count_occurrences([])=={}",
    },
    {
        "id": "binary_search",
        "description": "Write a Python function binary_search(lst, target) that returns the index of target in sorted list lst, or -1 if not found.",
        "test": "assert binary_search([1,3,5,7,9],5)==2 and binary_search([1,3,5,7,9],4)==-1 and binary_search([],1)==-1",
    },
    {
        "id": "merge_sorted",
        "description": "Write a Python function merge_sorted(a, b) that merges two sorted lists into one sorted list.",
        "test": "assert merge_sorted([1,3,5],[2,4,6])==[1,2,3,4,5,6] and merge_sorted([],[1])==[1]",
    },
    {
        "id": "matrix_multiply",
        "description": "Write a Python function matrix_multiply(A, B) that multiplies two 2D matrices (lists of lists) and returns the result.",
        "test": "assert matrix_multiply([[1,2],[3,4]],[[5,6],[7,8]])==[[19,22],[43,50]]",
    },
]

FORMAT_TIERS = {
    "control": [],
    "low": ["All variable names must use snake_case"],
    "moderate": [
        "All variable names must use snake_case",
        "Function must have a docstring",
        "No more than 3 functions total",
    ],
    "high": [
        "All variable names must use snake_case",
        "Function must have a docstring",
        "No more than 3 functions total",
        "No loops (use recursion or comprehensions)",
        "Every function must have type hints",
    ],
}

# Approximate rho values per tier (for analysis)
TIER_RHO = {"control": 0.05, "low": 0.15, "moderate": 0.35, "high": 0.55}


# ---------------------------------------------------------------------------
# OpenRouter models
# ---------------------------------------------------------------------------

MODELS = {
    "gpt-4o": "openai/gpt-4o",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "llama-3.1-70b": "meta-llama/llama-3.1-70b-instruct",
    "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",
    "deepseek-v3": "deepseek/deepseek-chat-v3-0324",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    # Blinded external benchmark additions (4 distinctively new providers)
    "mistral-large": "mistralai/mistral-large-2411",
    "command-r-plus": "cohere/command-r-plus-08-2024",
    "qwen-2.5-72b": "qwen/qwen-2.5-72b-instruct",
    "deepseek-r1": "deepseek/deepseek-r1",
}

DEFAULT_MODELS = ["gpt-4o", "gemini-2.5-flash", "llama-3.1-70b", "llama-3.3-70b", "deepseek-v3"]
BLINDED_EXTERNAL = ["mistral-large", "command-r-plus", "qwen-2.5-72b", "deepseek-r1"]


# ---------------------------------------------------------------------------
# Verifiers (same as main experiment, simplified)
# ---------------------------------------------------------------------------

def extract_code(response: str) -> Optional[str]:
    """Extract Python code block from response."""
    patterns = [
        r'```python\s*(.*?)\s*```',
        r'```\s*(.*?)\s*```',
    ]
    for pat in patterns:
        match = re.search(pat, response, re.DOTALL)
        if match:
            return match.group(1).strip()
    if response.strip().startswith("def "):
        return response.strip()
    return None


def verify_functional(code: str, test_code: str) -> Tuple[bool, str]:
    """Run functional test on extracted code."""
    if not code:
        return False, "no code extracted"
    try:
        namespace = {}
        exec(code, namespace)
        exec(test_code, namespace)
        return True, "pass"
    except Exception as e:
        return False, str(e)[:200]


def verify_format(code: str, rules: List[str]) -> Tuple[bool, str]:
    """Check format rules."""
    if not code or not rules:
        return True, "no rules" if not rules else "no code"

    violations = []
    code_lower = code.lower()

    for rule in rules:
        rl = rule.lower()
        if "snake_case" in rl:
            # Check for camelCase (but not in strings)
            if re.search(r'\b[a-z]+[A-Z][a-z]+\b', code):
                violations.append("camelCase detected")
        elif "docstring" in rl:
            if '"""' not in code and "'''" not in code:
                violations.append("missing docstring")
        elif "no more than 3 functions" in rl:
            if code.count("\ndef ") + (1 if code.startswith("def ") else 0) > 3:
                violations.append("too many functions")
        elif "no loops" in rl:
            if re.search(r'\b(for|while)\b', code_lower):
                violations.append("loop detected")
        elif "type hints" in rl:
            defs = re.findall(r'def \w+\([^)]*\)', code)
            for d in defs:
                params = d.split('(')[1].rstrip(')')
                if params.strip() and ':' not in params:
                    violations.append("missing type hints")
                    break

    if violations:
        return False, "; ".join(violations)
    return True, "pass"


# ---------------------------------------------------------------------------
# API client (OpenRouter, OpenAI-compatible)
# ---------------------------------------------------------------------------

def call_openrouter(
    prompt: str,
    model: str,
    client,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Call OpenRouter API."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content


def build_prompt(task: dict, tier: str) -> str:
    """Build one-shot prompt."""
    rules = FORMAT_TIERS[tier]
    rules_text = "\n".join(f"- {r}" for r in rules) if rules else "(none)"
    return f"""Write a Python function that solves the following task.

TASK: {task['description']}

FORMAT REQUIREMENTS:
{rules_text}

Provide your solution in a single ```python code block.
"""


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

@dataclass
class TrialResult:
    model: str
    model_id: str
    task_id: str
    tier: str
    tier_rho: float
    trial: int
    pass_functional: bool
    pass_format: bool
    pass_both: bool
    msg_functional: str
    msg_format: str
    response_length: int
    error: str = ""


def run_experiment(
    model_names: List[str],
    n_trials: int = 5,
    dry_run: bool = False,
    rate_limit_ms: int = 500,
) -> List[TrialResult]:
    """Run cross-model experiment via OpenRouter."""
    results = []

    client = None
    if not dry_run:
        try:
            from openai import OpenAI
        except ImportError:
            print("ERROR: pip install openai")
            sys.exit(1)

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            print("ERROR: set OPENROUTER_API_KEY")
            sys.exit(1)

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )

    for model_name in model_names:
        model_id = MODELS.get(model_name, model_name)
        print(f"\n{'='*55}")
        print(f"Model: {model_name} ({model_id})")
        print(f"{'='*55}")

        n_tasks = len(TASKS)
        n_tiers = len(FORMAT_TIERS)
        total = n_tasks * n_tiers * n_trials
        count = 0

        for task in TASKS:
            for tier in FORMAT_TIERS:
                for trial in range(n_trials):
                    count += 1
                    prompt = build_prompt(task, tier)

                    if dry_run:
                        print(f"  [{count}/{total}] {task['id']}/{tier}/t{trial}: "
                              f"DRY-RUN ({len(prompt)} chars)")
                        results.append(TrialResult(
                            model=model_name, model_id=model_id,
                            task_id=task["id"], tier=tier,
                            tier_rho=TIER_RHO[tier], trial=trial,
                            pass_functional=False, pass_format=False,
                            pass_both=False,
                            msg_functional="dry-run", msg_format="dry-run",
                            response_length=0,
                        ))
                        continue

                    try:
                        response = call_openrouter(prompt, model_id, client)
                        code = extract_code(response)
                        pf, mf = verify_functional(code, task["test"])
                        pfmt, mfmt = verify_format(code, FORMAT_TIERS[tier])

                        result = TrialResult(
                            model=model_name, model_id=model_id,
                            task_id=task["id"], tier=tier,
                            tier_rho=TIER_RHO[tier], trial=trial,
                            pass_functional=pf, pass_format=pfmt,
                            pass_both=pf and pfmt,
                            msg_functional=mf, msg_format=mfmt,
                            response_length=len(response),
                        )

                        status = "OK" if result.pass_both else "FAIL"
                        print(f"  [{count}/{total}] {task['id']}/{tier}/t{trial}: {status}")

                    except Exception as e:
                        print(f"  [{count}/{total}] {task['id']}/{tier}/t{trial}: ERROR {e}")
                        result = TrialResult(
                            model=model_name, model_id=model_id,
                            task_id=task["id"], tier=tier,
                            tier_rho=TIER_RHO[tier], trial=trial,
                            pass_functional=False, pass_format=False,
                            pass_both=False,
                            msg_functional="error", msg_format="error",
                            response_length=0, error=str(e)[:200],
                        )

                    results.append(result)

                    if not dry_run:
                        time.sleep(rate_limit_ms / 1000.0)

    return results


# ---------------------------------------------------------------------------
# Summary & plotting
# ---------------------------------------------------------------------------

def print_summary(results: List[TrialResult]):
    """Print cliff summary per model."""
    from collections import defaultdict

    by_model_tier = defaultdict(lambda: {"n": 0, "pass": 0})
    for r in results:
        by_model_tier[(r.model, r.tier)]["n"] += 1
        by_model_tier[(r.model, r.tier)]["pass"] += int(r.pass_both)

    models = sorted(set(r.model for r in results))
    tiers = ["control", "low", "moderate", "high"]

    print(f"\n{'Model':<22} ", end="")
    for t in tiers:
        print(f"{'  ' + t:>12}", end="")
    print()
    print("-" * 72)

    for model in models:
        print(f"{model:<22} ", end="")
        for tier in tiers:
            key = (model, tier)
            counts = by_model_tier.get(key, {"n": 0, "pass": 0})
            if counts["n"] > 0:
                rate = counts["pass"] / counts["n"]
                print(f"{rate:>11.0%}", end=" ")
            else:
                print(f"{'N/A':>12}", end="")
        print()

    # Also print for comparison with main experiment
    print(f"\n{'(main experiment)':>22} ", end="")
    main_rates = {"control": 0.77, "low": 0.58, "moderate": 0.23, "high": 0.02}
    for tier in tiers:
        print(f"{main_rates[tier]:>11.0%}", end=" ")
    print("  (1-3B models)")


def plot_cliff(results: List[TrialResult], output_dir: Path):
    """Plot cliff comparison across models."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available, skipping plots")
        return

    from collections import defaultdict

    by_model_tier = defaultdict(lambda: {"n": 0, "pass": 0})
    for r in results:
        by_model_tier[(r.model, r.tier)]["n"] += 1
        by_model_tier[(r.model, r.tier)]["pass"] += int(r.pass_both)

    models = sorted(set(r.model for r in results))
    tiers = ["control", "low", "moderate", "high"]
    tier_k = [1, 2, 3, 5]

    fig, ax = plt.subplots(1, 1, figsize=(8, 5))

    colors = ["#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#e67e22"]

    for i, model in enumerate(models):
        rates = []
        for tier in tiers:
            key = (model, tier)
            counts = by_model_tier.get(key, {"n": 0, "pass": 0})
            rate = counts["pass"] / counts["n"] if counts["n"] > 0 else 0
            rates.append(rate)
        ax.plot(tier_k, rates, "o-", color=colors[i % len(colors)],
                linewidth=2, markersize=8, label=model)

    # Main experiment baseline
    main_rates = [0.77, 0.58, 0.23, 0.02]
    ax.plot(tier_k, main_rates, "s--", color="gray", linewidth=1.5,
            markersize=6, alpha=0.7, label="Main experiment (1-3B)")

    ax.set_xlabel("Format constraints (k)")
    ax.set_ylabel("Pass rate (A AND B)")
    ax.set_title("Feasibility Cliff Across Model Providers")
    ax.set_ylim(0, 1.05)
    ax.set_xticks(tier_k)
    ax.set_xticklabels([f"k={k}\n({t})" for k, t in zip(tier_k, tiers)], fontsize=9)
    ax.legend(fontsize=8)

    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ["pdf", "png"]:
        path = output_dir / f"cross_model_cliff.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=150)
        print(f"Figure saved: {path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Cross-model code constraint experiment via OpenRouter")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="Comma-separated model names (see MODELS dict)")
    parser.add_argument("--list-models", action="store_true",
                        help="List available models and exit")
    parser.add_argument("--n-trials", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate setup without API calls")
    parser.add_argument("--rate-limit-ms", type=int, default=500,
                        help="Delay between API calls in ms")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent.parent / "figures")
    args = parser.parse_args()

    if args.list_models:
        print("Available models:")
        for name, model_id in MODELS.items():
            print(f"  {name:<20} -> {model_id}")
        return

    models = [m.strip() for m in args.models.split(",")]

    total_calls = len(models) * len(TASKS) * len(FORMAT_TIERS) * args.n_trials
    print("=" * 60)
    print("CROSS-MODEL CODE CONSTRAINT EXPERIMENT")
    print("via OpenRouter (single API, all providers)")
    print("=" * 60)
    print(f"Models:     {models}")
    print(f"Tasks:      {len(TASKS)}")
    print(f"Tiers:      {len(FORMAT_TIERS)}")
    print(f"Trials:     {args.n_trials}")
    print(f"Total API calls: {total_calls}")
    print(f"Dry run:    {args.dry_run}")

    if not args.dry_run and not os.environ.get("OPENROUTER_API_KEY"):
        print("\nERROR: Set OPENROUTER_API_KEY environment variable")
        print("  Get a key at: https://openrouter.ai/keys")
        print("  Or use --dry-run to test without API calls")
        sys.exit(1)

    results = run_experiment(models, args.n_trials, args.dry_run, args.rate_limit_ms)
    print_summary(results)

    # Save (append mode: merge with existing results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "cross_model_results.json"

    existing_results = []
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            existing_results = existing_data.get("results", [])
            # Remove old results for models we just ran (replace, don't duplicate)
            ran_models = set(models)
            existing_results = [r for r in existing_results
                                if r.get("model") not in ran_models]
            n_kept = len(existing_results)
            print(f"\n[Append mode] Kept {n_kept} existing results, replacing {models}")
        except (json.JSONDecodeError, IOError):
            print("\n[Fresh start] Could not load existing results")

    all_results = existing_results + [asdict(r) for r in results]

    # Also strip any results with errors (e.g., wrong model IDs)
    clean_results = [r for r in all_results if r.get("error", "") == ""]
    n_stripped = len(all_results) - len(clean_results)
    if n_stripped > 0:
        print(f"[Cleanup] Stripped {n_stripped} error results")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "models": list(set(r["model"] for r in clean_results)),
            "n_trials": args.n_trials,
            "dry_run": args.dry_run,
            "total_results": len(clean_results),
            "results": clean_results,
        }, f, indent=2)
    print(f"Results saved: {json_path} ({len(clean_results)} results)")

    if not args.dry_run:
        # Plot from MERGED clean_results so re-runs of a single model
        # don't lose the cross-model comparison. plot_cliff uses
        # attribute access (.model/.tier/.pass_both); wrap dicts in
        # SimpleNamespace to satisfy that interface without changing
        # the plot function.
        from types import SimpleNamespace
        plot_cliff([SimpleNamespace(**r) for r in clean_results], args.output_dir)


if __name__ == "__main__":
    main()
