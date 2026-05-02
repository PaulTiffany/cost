#!/usr/bin/env python3
"""
Delta Capacity Experiment: Vary token budget (δ) directly.

Tests the core theoretical prediction:
  "Staging benefit increases as capacity δ decreases"

Design:
- 4 models (same as main experiment)
- 6 representative tasks
- 1 tier (moderate - provides format pressure)
- 5 token budgets: 128, 192, 256, 384, 512
- 5 trials per cell
- 2 protocols (one-shot vs staged)

Total: 4 models × 6 tasks × 5 budgets × 5 trials × 2 protocols = 1200 trials
Expected runtime: 6-10 hours on GPU (shorter generations = faster)

Key prediction: staging_delta should be positive at low budgets,
near-zero or negative at high budgets.
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from code_constraint_verifier import (
    FORMAT_TIERS,
    extract_code_block,
    verify_both,
    format_rules_to_prompt,
)
from code_constraint_tasks import TASKS, CodeTask


# =============================================================================
# EXPERIMENT CONFIGURATION
# =============================================================================

# Models in order: strongest fast model first for best signal
MODEL_NAMES = [
    "Qwen/Qwen2.5-Coder-1.5B-Instruct",         # Best balance of speed + capability
    "deepseek-ai/deepseek-coder-1.3b-instruct",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",       # Weakest
    "Qwen/Qwen2.5-3B-Instruct",                  # Slowest (needs 4-bit)
]

# 6 representative tasks (mix of difficulties)
TASK_IDS = [
    "factorial",      # Simple recursion/loop
    "fibonacci",      # Classic DP/recursion
    "is_palindrome",  # String manipulation
    "sum_digits",     # Number manipulation
    "find_max",       # List traversal
    "count_vowels",   # String iteration
]

# Token budgets to test (the δ variable)
TOKEN_BUDGETS = [128, 192, 256, 384, 512]

# Fixed tier (low format pressure - enough signal to measure delta effect)
TIER = "low"

# Experiment parameters
N_TRIALS = 5
TEMPERATURE = 0.7
SEED_BASE = 42


@dataclass
class TrialResult:
    """Result of a single trial."""
    model: str
    task_id: str
    token_budget: int
    protocol: str
    trial: int
    pass_a: bool      # Functional correctness
    pass_b: bool      # Format compliance
    pass_both: bool   # Both verifiers pass
    generated_tokens: int
    hit_max_tokens: bool


def build_one_shot_prompt(task: CodeTask) -> str:
    """Build one-shot prompt with all constraints upfront."""
    rules = FORMAT_TIERS[TIER]
    format_text = format_rules_to_prompt(rules)

    return f"""Write a Python function that solves the following task.

TASK: {task.description}

FORMAT REQUIREMENTS:
{format_text}

Provide your solution in a single ```python code block.
"""


def build_staged_prompts(task: CodeTask) -> List[str]:
    """Build staged prompts (3-step protocol)."""
    rules = FORMAT_TIERS[TIER]
    format_text = format_rules_to_prompt(rules)

    stage1 = f"""Write a Python function that solves the following task. Focus on correctness first.

TASK: {task.description}

Provide your solution in a ```python code block.
"""

    stage2 = f"""Now check your solution against these format requirements:

{format_text}

List any violations of these requirements in your solution. If there are violations, explain what needs to change.
"""

    stage3 = f"""Now provide a revised solution that:
1. Still passes the functional requirements
2. Fixes all format violations

Provide your final solution in a single ```python code block.
"""

    return [stage1, stage2, stage3]


def generate_response(
    model,
    tokenizer,
    prompt: str,
    max_new_tokens: int,
    temperature: float = TEMPERATURE,
    seed: Optional[int] = None,
) -> Tuple[str, int, bool]:
    """Generate a response from the model."""
    if seed is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        random.seed(seed)

    try:
        messages = [{"role": "user", "content": prompt}]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    except Exception:
        text = f"User: {prompt}\nAssistant: "

    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=temperature > 0,
            pad_token_id=tokenizer.eos_token_id,
            use_cache=False,
        )

    response = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=True,
    )
    gen_tokens = outputs[0].shape[0] - inputs["input_ids"].shape[1]
    hit_max = gen_tokens >= max_new_tokens
    return response.strip(), gen_tokens, hit_max


def generate_staged_response(
    model,
    tokenizer,
    prompts: List[str],
    max_new_tokens: int,
    temperature: float = TEMPERATURE,
    seed: Optional[int] = None,
) -> Tuple[str, int, bool]:
    """Generate response using staged protocol (3 steps)."""
    if seed is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        random.seed(seed)

    conversation = []
    total_tokens = 0
    hit_max = False

    for i, prompt in enumerate(prompts):
        if i == 0:
            full_prompt = prompt
        else:
            context = ""
            for j in range(i):
                context += f"User: {prompts[j]}\n\nAssistant: {conversation[j]}\n\n"
            full_prompt = context + f"User: {prompt}\n\nAssistant: "

        try:
            messages = [{"role": "user", "content": full_prompt}]
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        except Exception:
            text = full_prompt

        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                pad_token_id=tokenizer.eos_token_id,
                use_cache=False,
            )

        response = tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )
        gen_tokens = outputs[0].shape[0] - inputs["input_ids"].shape[1]
        total_tokens += gen_tokens
        hit_max = hit_max or (gen_tokens >= max_new_tokens)
        conversation.append(response.strip())

    return conversation[-1], total_tokens, hit_max


def run_trial(
    model,
    tokenizer,
    task: CodeTask,
    token_budget: int,
    protocol: str,
    trial: int,
    model_name: str,
) -> TrialResult:
    """Run a single trial."""
    seed = SEED_BASE + hash((task.task_id, token_budget, protocol, trial)) % 10000

    if protocol == "oneshot":
        prompt = build_one_shot_prompt(task)
        response, gen_tokens, hit_max = generate_response(
            model, tokenizer, prompt, token_budget, seed=seed
        )
    else:
        prompts = build_staged_prompts(task)
        response, gen_tokens, hit_max = generate_staged_response(
            model, tokenizer, prompts, token_budget, seed=seed
        )

    # Extract and verify
    code = extract_code_block(response)
    rules = FORMAT_TIERS[TIER]
    result = verify_both(code, task.test_code, rules)

    return TrialResult(
        model=model_name,
        task_id=task.task_id,
        token_budget=token_budget,
        protocol=protocol,
        trial=trial,
        pass_a=result["pass_a"],
        pass_b=result["pass_b"],
        pass_both=result["pass_both"],
        generated_tokens=gen_tokens,
        hit_max_tokens=hit_max,
    )


def load_model(model_name: str):
    """Load model and tokenizer."""
    print(f"\n  Loading {model_name}...", flush=True)

    load_in_4bit = "3B" in model_name or "7B" in model_name

    if load_in_4bit:
        from transformers import BitsAndBytesConfig
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=quant_config,
            device_map="auto",
            trust_remote_code=True,
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True,
        )

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    return model, tokenizer


def run_experiment(
    models: List[str] = None,
    output_path: str = "delta_capacity_results.json",
):
    """Run the full δ-capacity experiment."""
    if models is None:
        models = MODEL_NAMES

    # Get selected tasks
    tasks = [t for t in TASKS if t.task_id in TASK_IDS]

    # Load existing results if any
    results_path = Path(output_path)
    if results_path.exists():
        with open(results_path, "r") as f:
            all_results = json.load(f)
        print(f"[Resume] Loaded {len(all_results.get('trials', []))} existing trials")
    else:
        all_results = {
            "meta": {
                "experiment": "delta_capacity",
                "tier": TIER,
                "token_budgets": TOKEN_BUDGETS,
                "n_trials": N_TRIALS,
                "temperature": TEMPERATURE,
                "task_ids": TASK_IDS,
                "started": datetime.now(timezone.utc).isoformat(),
            },
            "trials": [],
        }

    # Build set of completed trials for resume
    completed = set()
    for t in all_results["trials"]:
        key = (t["model"], t["task_id"], t["token_budget"], t["protocol"], t["trial"])
        completed.add(key)

    total_trials = len(models) * len(tasks) * len(TOKEN_BUDGETS) * N_TRIALS * 2
    print(f"\n{'='*60}", flush=True)
    print(f"DELTA CAPACITY EXPERIMENT", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"Models: {len(models)}", flush=True)
    print(f"Tasks: {len(tasks)}", flush=True)
    print(f"Token budgets: {TOKEN_BUDGETS}", flush=True)
    print(f"Trials per cell: {N_TRIALS}", flush=True)
    print(f"Total trials: {total_trials}", flush=True)
    print(f"Already completed: {len(completed)}", flush=True)
    print(f"Remaining: {total_trials - len(completed)}", flush=True)
    print(f"{'='*60}\n", flush=True)

    start_time = time.time()
    trials_done = len(completed)

    for model_name in models:
        model, tokenizer = load_model(model_name)
        model_start = time.time()
        model_trials = 0

        for budget in TOKEN_BUDGETS:
            for task in tasks:
                for protocol in ["oneshot", "staged"]:
                    for trial in range(N_TRIALS):
                        key = (model_name, task.task_id, budget, protocol, trial)
                        if key in completed:
                            continue

                        result = run_trial(
                            model, tokenizer, task, budget, protocol, trial, model_name
                        )
                        all_results["trials"].append(asdict(result))
                        completed.add(key)
                        trials_done += 1
                        model_trials += 1

                        # Progress
                        elapsed = time.time() - start_time
                        rate = trials_done / elapsed if elapsed > 0 else 0
                        remaining = (total_trials - trials_done) / rate if rate > 0 else 0

                        status = "PASS" if result.pass_both else "FAIL"
                        print(f"  [{trials_done}/{total_trials}] "
                              f"budget={budget:3d} {task.task_id:15s} {protocol:7s} "
                              f"trial={trial} -> {status} "
                              f"(ETA: {remaining/60:.1f}m)", flush=True)

                        # Checkpoint every 20 trials
                        if trials_done % 20 == 0:
                            with open(results_path, "w") as f:
                                json.dump(all_results, f, indent=2)

        # Model complete
        model_elapsed = time.time() - model_start
        print(f"\n  {model_name} complete: {model_trials} trials in {model_elapsed/60:.1f}m\n")

        # Free memory
        del model
        del tokenizer
        torch.cuda.empty_cache()

    # Final save
    all_results["meta"]["completed"] = datetime.now(timezone.utc).isoformat()
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)

    total_elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"EXPERIMENT COMPLETE")
    print(f"Total time: {total_elapsed/3600:.2f} hours")
    print(f"Results saved to: {output_path}")
    print(f"{'='*60}\n")

    # Quick summary
    analyze_results(all_results)


def analyze_results(data: dict):
    """Print quick summary of results."""
    trials = data["trials"]

    print("\n" + "="*60)
    print("QUICK ANALYSIS: Staging Delta by Token Budget")
    print("="*60)

    # Group by budget
    for budget in TOKEN_BUDGETS:
        oneshot = [t for t in trials if t["token_budget"] == budget and t["protocol"] == "oneshot"]
        staged = [t for t in trials if t["token_budget"] == budget and t["protocol"] == "staged"]

        if not oneshot or not staged:
            continue

        oneshot_rate = sum(t["pass_both"] for t in oneshot) / len(oneshot)
        staged_rate = sum(t["pass_both"] for t in staged) / len(staged)
        delta = staged_rate - oneshot_rate

        bar = "+" * int(abs(delta) * 50) if delta >= 0 else "-" * int(abs(delta) * 50)
        sign = "+" if delta >= 0 else ""

        print(f"  budget={budget:3d}: 1-shot={oneshot_rate:.1%}, staged={staged_rate:.1%}, "
              f"Delta={sign}{delta:.1%} {bar}")

    print()


def main():
    import sys
    print("[DEBUG] Starting main()", flush=True)

    parser = argparse.ArgumentParser(description="Delta Capacity Experiment")
    parser.add_argument("--models", nargs="+", default=None,
                        help="Models to run (default: all 4)")
    parser.add_argument("--output", default="delta_capacity_results.json",
                        help="Output file path")
    parser.add_argument("--quick", action="store_true",
                        help="Quick test: 1 model, 2 budgets, 2 trials")
    args = parser.parse_args()
    print(f"[DEBUG] Args parsed: quick={args.quick}", flush=True)

    if args.quick:
        # Quick test mode
        global TOKEN_BUDGETS, N_TRIALS, TASK_IDS
        TOKEN_BUDGETS = [128, 384]
        N_TRIALS = 2
        TASK_IDS = ["factorial", "fibonacci"]
        models = [MODEL_NAMES[0]]
        print(f"[DEBUG] Quick mode: models={models}", flush=True)
    else:
        models = args.models

    run_experiment(models=models, output_path=args.output)


if __name__ == "__main__":
    main()
