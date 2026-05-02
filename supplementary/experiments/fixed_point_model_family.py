#!/usr/bin/env python3
"""
FIXED-POINT FLOOR: Claude Family Analysis
ICML 2026 - Diagonal Cost Bounds

Extends the fixed-point experiment across the Claude model family
to create a unified dataset with graded metrics.

MODELS (7 available):
  Current:  haiku-4.5, sonnet-4.5, opus-4.5
  Previous: sonnet-4, opus-4, opus-4.1
  Legacy:   haiku-3

METRICS (graded, not binary):
  - |delta|: distance from fixed point (lower = better)
  - Improvement ratio: oneshot_delta / staged_delta
  - Winner: which protocol gets closer

RUN:
  python fixed_point_model_family.py --dry-run
  python fixed_point_model_family.py --api-key KEY
  python fixed_point_model_family.py --api-key KEY --models sonnet-4.5,opus-4.5
"""

import argparse
import json
import re
import sys
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Dict, Optional, Callable
import random


# =============================================================================
# MODEL DEFINITIONS
# =============================================================================

CLAUDE_FAMILY = {
    # Current generation (4.5)
    "haiku-4.5": "claude-haiku-4-5-20251001",
    "sonnet-4.5": "claude-sonnet-4-5-20250929",
    "opus-4.5": "claude-opus-4-5-20251101",
    # Previous generation (4.x)
    "sonnet-4": "claude-sonnet-4-20250514",
    "opus-4": "claude-opus-4-20250514",
    "opus-4.1": "claude-opus-4-1-20250805",
    # Legacy (3.x)
    "haiku-3": "claude-3-haiku-20240307",
}

# Cost per 1M tokens (input, output)
MODEL_COSTS = {
    "haiku-4.5": (1.0, 5.0),
    "sonnet-4.5": (3.0, 15.0),
    "opus-4.5": (5.0, 25.0),
    "sonnet-4": (3.0, 15.0),
    "opus-4": (15.0, 75.0),
    "opus-4.1": (15.0, 75.0),
    "haiku-3": (0.25, 1.25),
}


# =============================================================================
# FIXED-POINT TASK DEFINITIONS
# =============================================================================

@dataclass
class FixedPointTask:
    """A self-referential constraint task."""
    task_id: str
    base_prompt: str
    target_fn: Callable[[str], int]
    actual_fn: Callable[[str], int]
    target_fn_desc: str
    difficulty: int


def count_digits(text: str) -> int:
    return sum(1 for c in text if c.isdigit())

def count_vowels(text: str) -> int:
    return sum(1 for c in text.lower() if c in 'aeiou')

def sum_digits(text: str) -> int:
    return sum(int(c) for c in text if c.isdigit())

def count_words(text: str) -> int:
    return len(text.split())

def char_count(text: str) -> int:
    return len(text.strip())


def make_digit_sum_task(seed: int) -> FixedPointTask:
    rng = random.Random(seed)
    base = rng.randint(40, 80)
    return FixedPointTask(
        task_id=f"digit_sum_{base}",
        base_prompt=f"Explain why 17 is prime. Your response must have EXACTLY N characters, where N = {base} + (sum of all digit characters in your response). Count carefully!",
        target_fn=lambda x, b=base: b + sum_digits(x),
        actual_fn=char_count,
        target_fn_desc=f"N = {base} + sum_of_digits",
        difficulty=3,
    )

def make_vowel_count_task(seed: int) -> FixedPointTask:
    rng = random.Random(seed)
    base = rng.randint(30, 60)
    return FixedPointTask(
        task_id=f"vowel_count_{base}",
        base_prompt=f"Explain the sum formula 1+2+...+n = n(n+1)/2. Your response must have EXACTLY N characters, where N = {base} + 2*(number of vowels in your response).",
        target_fn=lambda x, b=base: b + 2 * count_vowels(x),
        actual_fn=char_count,
        target_fn_desc=f"N = {base} + 2*vowel_count",
        difficulty=3,
    )

def make_word_digit_task(seed: int) -> FixedPointTask:
    return FixedPointTask(
        task_id=f"word_digit_{seed}",
        base_prompt="Explain why a^2 + b^2 = c^2 for right triangles. Your response must have EXACTLY N characters, where N = (word_count * 10) + (digit_count * 5).",
        target_fn=lambda x: count_words(x) * 10 + count_digits(x) * 5,
        actual_fn=char_count,
        target_fn_desc="N = words*10 + digits*5",
        difficulty=4,
    )

def make_self_counting_task(seed: int) -> FixedPointTask:
    rng = random.Random(seed)
    topic = rng.choice(["prime numbers", "the golden ratio", "Euler's identity"])
    return FixedPointTask(
        task_id=f"self_count_{seed}",
        base_prompt=f"Write about {topic}. Your response must END with 'Length: N' where N is the exact character count of your entire response (including 'Length: N').",
        target_fn=lambda x: int(re.search(r'Length:\s*(\d+)\s*$', x).group(1)) if re.search(r'Length:\s*(\d+)\s*$', x) else -1,
        actual_fn=char_count,
        target_fn_desc="stated_length == actual_length",
        difficulty=5,
    )


# =============================================================================
# PROMPTS
# =============================================================================

def prompt_oneshot(task: FixedPointTask) -> str:
    return f"""{task.base_prompt}

Think carefully. The constraint is self-referential - what you write affects what you must write.

Your response:"""

def prompt_stage1_draft(task: FixedPointTask) -> str:
    return f"""I need to solve this self-referential constraint problem:

{task.base_prompt}

First, write a DRAFT response that explains the topic well. Don't worry about the length constraint yet - just write good content. I'll measure and adjust in the next step.

Draft:"""

def prompt_stage2_measure_adjust(task: FixedPointTask, draft: str, target: int, actual: int) -> str:
    diff = actual - target
    direction = "too long" if diff > 0 else "too short"
    return f"""My draft has {actual} characters but needs EXACTLY {target} characters ({direction} by {abs(diff)}).

The constraint: {task.target_fn_desc}

My draft:
{draft}

Adjust this draft to have EXACTLY {target} characters while keeping the explanation clear. Output ONLY the adjusted response, nothing else:"""

def prompt_stage3_verify_adjust(task: FixedPointTask, attempt: str, target: int, actual: int) -> str:
    if actual == target:
        return None
    diff = actual - target
    direction = "remove" if diff > 0 else "add"
    return f"""Still not exact. Current: {actual} chars, Need: {target} chars.

{direction.upper()} exactly {abs(diff)} characters while keeping it coherent.

Current text:
{attempt}

Final adjusted version (EXACTLY {target} chars):"""


# =============================================================================
# VERIFICATION
# =============================================================================

def verify_fixed_point(text: str, task: FixedPointTask) -> dict:
    text = text.strip()
    try:
        target = task.target_fn(text)
        actual = task.actual_fn(text)
        return {
            "target": target,
            "actual": actual,
            "delta": actual - target,
            "is_fixed_point": actual == target,
        }
    except Exception as e:
        return {
            "target": -1,
            "actual": len(text),
            "delta": float('inf'),
            "is_fixed_point": False,
            "error": str(e),
        }


# =============================================================================
# API CLIENT
# =============================================================================

class BudgetExceeded(Exception):
    pass

class APIClient:
    def __init__(self, api_key: str, max_calls: int = 200, max_cost: float = 10.0):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.max_calls = max_calls
        self.max_cost = max_cost
        self.model_tokens = {}  # Track per-model

    def estimated_cost(self, model_name: str = None) -> float:
        # Use opus pricing as upper bound
        return (self.input_tokens / 1e6 * 15) + (self.output_tokens / 1e6 * 75)

    def check_budget(self):
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"Max calls ({self.max_calls}) reached")
        if self.estimated_cost() >= self.max_cost:
            raise BudgetExceeded(f"Max cost (${self.max_cost:.2f}) reached")

    def generate(self, model_id: str, prompt: str, max_tokens: int) -> str:
        self.check_budget()
        try:
            r = self.client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}]
            )
            self.calls += 1
            self.input_tokens += r.usage.input_tokens
            self.output_tokens += r.usage.output_tokens

            # Track per-model
            if model_id not in self.model_tokens:
                self.model_tokens[model_id] = {"input": 0, "output": 0, "calls": 0}
            self.model_tokens[model_id]["input"] += r.usage.input_tokens
            self.model_tokens[model_id]["output"] += r.usage.output_tokens
            self.model_tokens[model_id]["calls"] += 1

            return r.content[0].text.strip()
        except BudgetExceeded:
            raise
        except Exception as e:
            print(f"    API ERROR: {e}")
            return ""


# =============================================================================
# EXPERIMENT
# =============================================================================

@dataclass
class TrialResult:
    task_id: str
    model_id: str
    model_name: str
    protocol: str
    is_fixed_point: bool
    target: int
    actual: int
    delta: int
    iterations: int


def run_oneshot(client: APIClient, model_id: str, model_name: str,
                task: FixedPointTask, budget: int) -> TrialResult:
    response = client.generate(model_id, prompt_oneshot(task), budget)
    v = verify_fixed_point(response, task)
    return TrialResult(
        task_id=task.task_id,
        model_id=model_id,
        model_name=model_name,
        protocol="oneshot",
        is_fixed_point=v["is_fixed_point"],
        target=v["target"],
        actual=v["actual"],
        delta=v["delta"],
        iterations=1,
    )

def run_staged(client: APIClient, model_id: str, model_name: str,
               task: FixedPointTask, budget: int, max_iterations: int = 3) -> TrialResult:
    draft_budget = budget // 2
    adjust_budget = (budget - draft_budget) // max_iterations

    draft = client.generate(model_id, prompt_stage1_draft(task), draft_budget)
    v = verify_fixed_point(draft, task)
    current = draft
    iterations = 1

    for i in range(max_iterations):
        if v["is_fixed_point"]:
            break
        iterations += 1
        target = v["target"]
        actual = v["actual"]

        if i == 0:
            prompt = prompt_stage2_measure_adjust(task, current, target, actual)
        else:
            prompt = prompt_stage3_verify_adjust(task, current, target, actual)

        if prompt is None:
            break

        current = client.generate(model_id, prompt, adjust_budget)
        v = verify_fixed_point(current, task)

    return TrialResult(
        task_id=task.task_id,
        model_id=model_id,
        model_name=model_name,
        protocol="staged",
        is_fixed_point=v["is_fixed_point"],
        target=v["target"],
        actual=v["actual"],
        delta=v["delta"],
        iterations=iterations,
    )


def analyze_model_results(results: List[dict], model_name: str) -> dict:
    """Compute graded metrics for a model."""
    model_results = [r for r in results if r["model_name"] == model_name]

    by_task = {}
    for r in model_results:
        tid = r["task_id"]
        if tid not in by_task:
            by_task[tid] = {}
        by_task[tid][r["protocol"]] = r

    analysis = []
    for task_id, protocols in by_task.items():
        os = protocols.get("oneshot", {})
        st = protocols.get("staged", {})

        os_delta = abs(os.get("delta", float('inf')))
        st_delta = abs(st.get("delta", float('inf')))

        # Improvement ratio
        if st_delta > 0:
            ratio = os_delta / st_delta
        else:
            ratio = float('inf') if os_delta > 0 else 1.0

        # Winner
        if st_delta < os_delta:
            winner = "staged"
        elif os_delta < st_delta:
            winner = "oneshot"
        else:
            winner = "tie"

        analysis.append({
            "task_id": task_id,
            "oneshot_delta": os.get("delta", 0),
            "staged_delta": st.get("delta", 0),
            "abs_oneshot": os_delta,
            "abs_staged": st_delta,
            "improvement_ratio": ratio,
            "winner": winner,
        })

    # Aggregate
    staged_wins = sum(1 for a in analysis if a["winner"] == "staged")
    valid_ratios = [a["improvement_ratio"] for a in analysis
                    if a["improvement_ratio"] != float('inf') and a["improvement_ratio"] > 0]
    avg_ratio = sum(valid_ratios) / len(valid_ratios) if valid_ratios else 0

    return {
        "model_name": model_name,
        "tasks_analyzed": len(analysis),
        "staged_wins": staged_wins,
        "avg_improvement_ratio": avg_ratio,
        "task_details": analysis,
    }


def run_experiment(api_key: str, models: List[str], seed: int, budget: int,
                   max_calls: int, max_cost: float):
    """Run fixed-point experiment across Claude family."""

    client = APIClient(api_key, max_calls=max_calls, max_cost=max_cost)

    # Generate task suite (same for all models)
    tasks = [
        make_digit_sum_task(seed),
        make_digit_sum_task(seed + 1),
        make_vowel_count_task(seed),
        make_vowel_count_task(seed + 1),
        make_word_digit_task(seed),
        make_self_counting_task(seed),
    ]

    print()
    print("=" * 75)
    print("  FIXED-POINT FLOOR: CLAUDE FAMILY ANALYSIS")
    print("  ICML 2026 - Diagonal Cost Bounds")
    print("=" * 75)
    print()
    print(f"  Models: {', '.join(models)}")
    print(f"  Tasks: {len(tasks)} self-referential constraints")
    print(f"  Seed: {seed}, Budget: {budget} tokens")
    print(f"  Expected: ~{len(models) * len(tasks) * 5} API calls")
    print()

    all_results = []

    for model_name in models:
        model_id = CLAUDE_FAMILY[model_name]
        print(f"\n  Testing {model_name}...")
        print(f"  {'-'*60}")

        for task in tasks:
            try:
                os_result = run_oneshot(client, model_id, model_name, task, budget)
                st_result = run_staged(client, model_id, model_name, task, budget)

                all_results.append(asdict(os_result))
                all_results.append(asdict(st_result))

                os_d = os_result.delta
                st_d = st_result.delta
                better = "STAGED" if abs(st_d) < abs(os_d) else "oneshot" if abs(os_d) < abs(st_d) else "tie"

                print(f"    {task.task_id:<20} 1-shot:{os_d:+4d}  staged:{st_d:+4d}  -> {better}")

            except BudgetExceeded as e:
                print(f"\n  *** BUDGET EXCEEDED: {e} ***")
                break

        print(f"  [Calls: {client.calls}/{max_calls}, Cost: ${client.estimated_cost():.2f}/${max_cost:.2f}]")

    # Analyze results per model
    print("\n" + "=" * 75)
    print("  GRADED ANALYSIS SUMMARY")
    print("=" * 75)

    model_analyses = []
    for model_name in models:
        analysis = analyze_model_results(all_results, model_name)
        model_analyses.append(analysis)

        print(f"\n  {model_name}:")
        print(f"    Staged wins: {analysis['staged_wins']}/{analysis['tasks_analyzed']} tasks")
        print(f"    Avg improvement ratio: {analysis['avg_improvement_ratio']:.1f}x")

    # Summary table
    print("\n" + "=" * 75)
    print("  MODEL COMPARISON TABLE")
    print("=" * 75)
    print(f"\n  {'Model':<12} {'Staged Wins':>12} {'Avg Ratio':>12} {'Staging':>15}")
    print(f"  {'-'*55}")

    for a in model_analyses:
        wins = f"{a['staged_wins']}/{a['tasks_analyzed']}"
        ratio = f"{a['avg_improvement_ratio']:.1f}x" if a['avg_improvement_ratio'] > 0 else "N/A"
        benefit = "helps" if a['staged_wins'] > a['tasks_analyzed'] // 2 else "marginal"
        print(f"  {a['model_name']:<12} {wins:>12} {ratio:>12} {benefit:>15}")

    # Save results
    output = {
        "meta": {
            "experiment": "fixed_point_model_family",
            "models": models,
            "seed": seed,
            "budget": budget,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "api_calls": client.calls,
            "input_tokens": client.input_tokens,
            "output_tokens": client.output_tokens,
            "per_model_tokens": client.model_tokens,
        },
        "model_analyses": model_analyses,
        "results": all_results,
    }

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, "fixed_point_model_family.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved: {output_path}")
    print(f"  Total API calls: {client.calls}")
    print(f"  Estimated cost: ${client.estimated_cost():.2f}")

    return output


def dry_run(models: List[str], seed: int = 43):
    """Preview what will be tested."""
    tasks = [
        make_digit_sum_task(seed),
        make_vowel_count_task(seed),
        make_word_digit_task(seed),
        make_self_counting_task(seed),
    ]

    print()
    print("=" * 75)
    print("  FIXED-POINT CLAUDE FAMILY - DRY RUN")
    print("=" * 75)
    print()
    print("  MODELS:")
    for name in models:
        print(f"    - {name}: {CLAUDE_FAMILY[name]}")
    print()
    print("  TASKS (self-referential constraints):")
    for t in tasks:
        print(f"    [{t.task_id}] {t.target_fn_desc}")
    print()
    print(f"  Expected API calls: ~{len(models) * 6 * 5} ({len(models)} models x 6 tasks x ~5 calls)")
    print(f"  Estimated cost: ~${len(models) * 1.5:.2f} (varies by model)")
    print()
    print("  GRADED METRICS:")
    print("    - |delta|: distance from fixed point (0 = exact)")
    print("    - Improvement ratio: |oneshot_delta| / |staged_delta|")
    print("    - Winner: which protocol gets closer")
    print()


def main():
    parser = argparse.ArgumentParser(description="Fixed-Point Floor - Claude Family")
    parser.add_argument("--api-key", help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    parser.add_argument("--models", default="haiku-3,sonnet-4,sonnet-4.5,opus-4.5",
                        help="Comma-separated model names")
    parser.add_argument("--seed", type=int, default=43, help="RNG seed")
    parser.add_argument("--budget", type=int, default=512, help="Token budget per call")
    parser.add_argument("--max-calls", type=int, default=350, help="Max total API calls")
    parser.add_argument("--max-cost", type=float, default=25.0, help="Max cost in $")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",")]

    # Validate model names
    for m in models:
        if m not in CLAUDE_FAMILY:
            sys.exit(f"ERROR: Unknown model '{m}'. Valid: {list(CLAUDE_FAMILY.keys())}")

    if args.dry_run:
        dry_run(models, args.seed)
        return

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: --api-key required or set ANTHROPIC_API_KEY")

    run_experiment(api_key, models, args.seed, args.budget, args.max_calls, args.max_cost)


if __name__ == "__main__":
    main()
