#!/usr/bin/env python3
"""
Smoke test: per-stage regression measurement.

Question: Does staging-induced regression rise with rho (paper's claim)
or stay roughly flat (what existing data suggests)?

Approach: instrument the staged protocol to capture stage-1 and stage-3
outputs separately, evaluate each against tests + format, compute paired
regression (stage1 passed C_i but stage3 violates C_i) per tier.

Subset for speed: TinyLlama (smallest), 2 tasks, 4 tiers, 3 trials = 24 trials.
"""
import json
import sys
import time
import random
import hashlib
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from code_constraint_verifier import (
    FORMAT_TIERS, extract_code_block, verify_both, format_rules_to_prompt,
)
from code_constraint_tasks import TASKS

MODEL_NAME = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
TEMPERATURE = 0.7
MAX_NEW_TOKENS = 384
SEED_BASE = 42

SMOKE_TASKS = ["factorial", "fibonacci"]
SMOKE_TIERS = ["control", "low", "moderate", "high"]
SMOKE_TRIALS = 3


def stable_seed(*parts):
    payload = "|".join([str(SEED_BASE), *map(str, parts)]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "little")


def build_staged_prompts(task, tier):
    rules = FORMAT_TIERS[tier]
    format_text = format_rules_to_prompt(rules)
    s1 = f"""Write a Python function that solves the following task. Focus on correctness first.

TASK: {task.description}

Provide your solution in a ```python code block.
"""
    s2 = f"""Now check your solution against these format requirements:

{format_text}

List any violations of these requirements in your solution. If there are violations, explain what needs to change.
"""
    s3 = f"""Now provide a revised solution that:
1. Still passes the functional requirements
2. Fixes all format violations

Provide your final solution in a single ```python code block.
"""
    return [s1, s2, s3]


def generate_one(model, tokenizer, full_prompt, seed):
    torch.manual_seed(seed); random.seed(seed)
    try:
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": full_prompt}],
            tokenize=False, add_generation_prompt=True,
        )
    except Exception:
        text = f"User: {full_prompt}\nAssistant: "
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=MAX_NEW_TOKENS,
            temperature=TEMPERATURE, do_sample=True,
            pad_token_id=tokenizer.eos_token_id, use_cache=False,
        )
    response = tokenizer.decode(
        out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True,
    ).strip()
    return response


def staged_with_stage_capture(model, tokenizer, prompts, seed):
    """Run staged protocol, capturing each stage's response."""
    torch.manual_seed(seed); random.seed(seed)
    conversation = []
    for i, prompt in enumerate(prompts):
        if i == 0:
            full = prompt
        else:
            ctx = ""
            for j in range(i):
                ctx += f"User: {prompts[j]}\n\nAssistant: {conversation[j]}\n\n"
            full = ctx + f"User: {prompt}\n\nAssistant: "
        resp = generate_one(model, tokenizer, full, seed + i)
        conversation.append(resp)
    return conversation  # [stage1, stage2, stage3]


def main():
    print(f"Loading {MODEL_NAME}...")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME, torch_dtype=torch.float32, device_map="cpu",
    )
    model.eval()
    print(f"  loaded in {time.time()-t0:.1f}s")

    task_map = {t.task_id: t for t in TASKS}
    tasks = [task_map[tid] for tid in SMOKE_TASKS]

    results = []
    n = len(tasks) * len(SMOKE_TIERS) * SMOKE_TRIALS
    i = 0
    t_start = time.time()
    for task in tasks:
        for tier in SMOKE_TIERS:
            for trial in range(SMOKE_TRIALS):
                i += 1
                seed = stable_seed(MODEL_NAME, task.task_id, tier, "staged", trial)
                t_trial = time.time()
                print(f"  [{i}/{n}] {task.task_id} / {tier} / trial {trial}...", flush=True)
                stages = staged_with_stage_capture(
                    model, tokenizer, build_staged_prompts(task, tier), seed,
                )
                # Evaluate stage 1 (correctness-focused) and stage 3 (final)
                s1_code = extract_code_block(stages[0])
                s3_code = extract_code_block(stages[2])
                s1_a, s1_b, s1_msg_a, s1_msg_b = verify_both(s1_code, task, FORMAT_TIERS[tier])
                s3_a, s3_b, s3_msg_a, s3_msg_b = verify_both(s3_code, task, FORMAT_TIERS[tier])
                results.append({
                    "task_id": task.task_id, "tier": tier, "trial": trial,
                    "stage1_pass_a": bool(s1_a), "stage1_pass_b": bool(s1_b),
                    "stage3_pass_a": bool(s3_a), "stage3_pass_b": bool(s3_b),
                    "regression_a": bool(s1_a and not s3_a),
                    "regression_b": bool(s1_b and not s3_b),
                    "stage1_code": s1_code, "stage3_code": s3_code,
                })
                print(f"      stage1: a={s1_a} b={s1_b} | stage3: a={s3_a} b={s3_b} | regress_a={s1_a and not s3_a} regress_b={s1_b and not s3_b} | {time.time()-t_trial:.1f}s")

    elapsed = time.time() - t_start
    print(f"\nTotal: {elapsed:.1f}s ({elapsed/n:.1f}s/trial)")

    # Per-tier summary
    print("\n=== Per-tier paired regression ===")
    print(f"{'tier':<10} {'n':<4} {'reg_a':<8} {'reg_b':<8} {'reg_a%':<8} {'reg_b%':<8}")
    summary = {}
    for tier in SMOKE_TIERS:
        rs = [r for r in results if r["tier"] == tier]
        n_t = len(rs)
        # Conditional regression: out of trials where stage1 passed C, what % failed in stage3?
        s1_a_pass = [r for r in rs if r["stage1_pass_a"]]
        s1_b_pass = [r for r in rs if r["stage1_pass_b"]]
        ra = sum(1 for r in s1_a_pass if not r["stage3_pass_a"]) / max(1, len(s1_a_pass))
        rb = sum(1 for r in s1_b_pass if not r["stage3_pass_b"]) / max(1, len(s1_b_pass))
        # Unconditional rate (regression / total)
        reg_a_unc = sum(1 for r in rs if r["regression_a"]) / max(1, n_t)
        reg_b_unc = sum(1 for r in rs if r["regression_b"]) / max(1, n_t)
        summary[tier] = {
            "n": n_t, "n_s1_a_pass": len(s1_a_pass), "n_s1_b_pass": len(s1_b_pass),
            "regression_a_conditional": ra, "regression_b_conditional": rb,
            "regression_a_unconditional": reg_a_unc, "regression_b_unconditional": reg_b_unc,
        }
        print(f"{tier:<10} {n_t:<4} {sum(1 for r in rs if r['regression_a']):<8} {sum(1 for r in rs if r['regression_b']):<8} {ra*100:6.1f}%  {rb*100:6.1f}%")

    out = {"_meta": {"model": MODEL_NAME, "n_tasks": len(tasks), "n_tiers": 4, "n_trials_per_cell": SMOKE_TRIALS, "elapsed_sec": elapsed}, "summary": summary, "results": results}
    out_path = Path(__file__).parent / "smoke_regression_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
