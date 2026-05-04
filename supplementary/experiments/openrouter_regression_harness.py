#!/usr/bin/env python3
"""
OpenRouter staging-regression harness.

Goal: measure paired regression (stage 1 passes C_i, stage 3 fails C_i)
across cheap OpenRouter models, per tier. Test whether regression rate
rises with rho (paper claim) or stays flat (existing-data hint).

Captures both stage 1 (correctness-focused) and stage 3 (final) outputs,
evaluates pass_a/pass_b on each, computes paired regression per (model, tier).

Crash-resilient: per-trial checkpoint to JSON.
"""
import json
import os
import sys
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

N_WORKERS = 8
_ckpt_lock = threading.Lock()
_counter_lock = threading.Lock()
_counter = {"done": 0}

from code_constraint_verifier import (
    FORMAT_TIERS, extract_code_block, verify_both, format_rules_to_prompt,
)
from code_constraint_tasks import TASKS

# OpenRouter fleet: 8 models spanning small to frontier across providers
MODELS = {
    # Small / cheap
    "gemini-2.5-flash":  "google/gemini-2.5-flash",
    "gpt-4o-mini":       "openai/gpt-4o-mini",
    "claude-haiku-3.5":  "anthropic/claude-3.5-haiku",
    # Mid
    "llama-3.1-70b":     "meta-llama/llama-3.1-70b-instruct",
    "deepseek-v3":       "deepseek/deepseek-chat-v3-0324",
    "mistral-large":     "mistralai/mistral-large-2411",
    # Frontier
    "gpt-4o":            "openai/gpt-4o",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
}

TASK_IDS = ["factorial", "fibonacci", "is_palindrome", "sum_digits", "count_vowels", "fizzbuzz"]
TIERS = ["control", "low", "moderate", "high"]
N_TRIALS = 5
TEMPERATURE = 0.7
MAX_TOKENS = 384
SEED_BASE = 42


def stable_seed(*parts):
    payload = "|".join([str(SEED_BASE), *map(str, parts)]).encode()
    # Mask to int32 positive range (Google API rejects > 2^31-1).
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "little") & 0x7FFFFFFF


def build_staged_prompts(task, tier):
    rules = FORMAT_TIERS[tier]
    fmt = format_rules_to_prompt(rules)
    s1 = f"""Write a Python function that solves the following task. Focus on correctness first.

TASK: {task.description}

Provide your solution in a ```python code block.
"""
    s2 = f"""Now check your solution against these format requirements:

{fmt}

List any violations of these requirements in your solution. If there are violations, explain what needs to change.
"""
    s3 = """Now provide a revised solution that:
1. Still passes the functional requirements
2. Fixes all format violations

Provide your final solution in a single ```python code block.
"""
    return [s1, s2, s3]


def call_openrouter(client, model_id, messages, seed):
    resp = client.chat.completions.create(
        model=model_id,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        seed=seed,
    )
    return resp.choices[0].message.content


def staged_with_capture(client, model_id, prompts, seed):
    """Run 3-stage protocol, capturing each stage's response."""
    messages = []
    stages = []
    for i, prompt in enumerate(prompts):
        messages.append({"role": "user", "content": prompt})
        try:
            response = call_openrouter(client, model_id, messages, seed + i)
        except Exception as e:
            return stages + [f"<<ERROR: {type(e).__name__}: {e}>>"] * (3 - len(stages))
        stages.append(response)
        messages.append({"role": "assistant", "content": response})
    return stages


def main():
    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not found; pip install openai", file=sys.stderr)
        return 1
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY env var required", file=sys.stderr)
        return 1
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    task_map = {t.task_id: t for t in TASKS}
    tasks = [task_map[tid] for tid in TASK_IDS]

    out_path = Path(__file__).parent / "openrouter_regression_results.json"
    state = {"_meta": {
        "models": list(MODELS.keys()), "model_ids": MODELS,
        "task_ids": TASK_IDS, "tiers": TIERS, "n_trials": N_TRIALS,
        "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, "results": []}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            if "results" in old:
                state["results"] = old["results"]
                print(f"  Resumed: {len(state['results'])} prior results loaded.", flush=True)
        except Exception:
            pass

    # Only treat successful trials as "done" — error entries get retried
    done_keys = {(r["model"], r["task_id"], r["tier"], r["trial"]) for r in state["results"] if "stage1_pass_a" in r}
    # Drop prior error entries so they don't accumulate
    state["results"] = [r for r in state["results"] if "stage1_pass_a" in r]

    # Build work queue (skip already-done cells; treat error entries as retryable)
    work = []
    for model_name, model_id in MODELS.items():
        for task in tasks:
            for tier in TIERS:
                for trial in range(N_TRIALS):
                    if (model_name, task.task_id, tier, trial) in done_keys:
                        continue
                    work.append((model_name, model_id, task, tier, trial))
    n_total = len(MODELS) * len(tasks) * len(TIERS) * N_TRIALS
    n_to_do = len(work)
    n_already = len(state["results"])
    print(f"  Plan: {n_total} total cells | {n_already} done (incl errors) | {n_to_do} to run | {N_WORKERS} workers", flush=True)
    t_start = time.time()

    def run_cell(args):
        model_name, model_id, task, tier, trial = args
        seed = stable_seed(model_name, task.task_id, tier, "staged", trial)
        t_t = time.time()
        stages = staged_with_capture(client, model_id, build_staged_prompts(task, tier), seed)
        if any("<<ERROR" in s for s in stages):
            return {
                "model": model_name, "model_id": model_id,
                "task_id": task.task_id, "tier": tier, "trial": trial,
                "error": [s for s in stages if "<<ERROR" in s][0],
                "_dt": time.time() - t_t,
            }
        s1_code = extract_code_block(stages[0])
        s3_code = extract_code_block(stages[2])
        r1 = verify_both(s1_code, task.test_code, FORMAT_TIERS[tier])
        r3 = verify_both(s3_code, task.test_code, FORMAT_TIERS[tier])
        s1_a, s1_b = r1["pass_a"], r1["pass_b"]
        s3_a, s3_b = r3["pass_a"], r3["pass_b"]
        return {
            "model": model_name, "model_id": model_id,
            "task_id": task.task_id, "tier": tier, "trial": trial,
            "stage1_pass_a": bool(s1_a), "stage1_pass_b": bool(s1_b),
            "stage3_pass_a": bool(s3_a), "stage3_pass_b": bool(s3_b),
            "regression_a": bool(s1_a and not s3_a),
            "regression_b": bool(s1_b and not s3_b),
            "stage1_code": s1_code, "stage3_code": s3_code,
            "_dt": time.time() - t_t,
        }

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(run_cell, w) for w in work]
        for fut in as_completed(futs):
            r = fut.result()
            with _counter_lock:
                _counter["done"] += 1
                done_now = _counter["done"]
            with _ckpt_lock:
                state["results"].append(r)
                out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tag = f"{r['model']:<18} {r['task_id']:<14} {r['tier']:<8} t{r['trial']}"
            if "error" in r:
                msg = "ERROR"
            else:
                msg = f"s1:a{int(r['stage1_pass_a'])}b{int(r['stage1_pass_b'])} s3:a{int(r['stage3_pass_a'])}b{int(r['stage3_pass_b'])} reg_a{int(r['regression_a'])} reg_b{int(r['regression_b'])}"
            elapsed_now = time.time() - t_start
            eta = (elapsed_now / done_now) * (n_to_do - done_now) if done_now else 0
            print(f"  [{done_now:3d}/{n_to_do}] {tag} | {msg} | {r['_dt']:.1f}s | ETA {eta/60:.1f}min", flush=True)

    elapsed = time.time() - t_start
    state["_meta"]["elapsed_sec"] = elapsed
    state["_meta"]["finished_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Per-(model, tier) summary: conditional regression
    summary = {}
    for model_name in MODELS:
        summary[model_name] = {}
        for tier in TIERS:
            rs = [r for r in state["results"] if r.get("model") == model_name and r.get("tier") == tier and "stage1_pass_a" in r]
            n = len(rs)
            s1_a_pass = [r for r in rs if r["stage1_pass_a"]]
            s1_b_pass = [r for r in rs if r["stage1_pass_b"]]
            ra = sum(1 for r in s1_a_pass if not r["stage3_pass_a"]) / max(1, len(s1_a_pass))
            rb = sum(1 for r in s1_b_pass if not r["stage3_pass_b"]) / max(1, len(s1_b_pass))
            summary[model_name][tier] = {
                "n": n, "n_s1_a_pass": len(s1_a_pass), "n_s1_b_pass": len(s1_b_pass),
                "regression_a_conditional": round(ra, 3),
                "regression_b_conditional": round(rb, 3),
            }
    state["summary"] = summary

    # Pooled across models per tier
    pooled = {}
    for tier in TIERS:
        rs = [r for r in state["results"] if r.get("tier") == tier and "stage1_pass_a" in r]
        s1a = [r for r in rs if r["stage1_pass_a"]]
        s1b = [r for r in rs if r["stage1_pass_b"]]
        pooled[tier] = {
            "n": len(rs), "n_s1_a_pass": len(s1a), "n_s1_b_pass": len(s1b),
            "regression_a_conditional": round(sum(1 for r in s1a if not r["stage3_pass_a"]) / max(1, len(s1a)), 3),
            "regression_b_conditional": round(sum(1 for r in s1b if not r["stage3_pass_b"]) / max(1, len(s1b)), 3),
        }
    state["pooled_per_tier"] = pooled
    out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\nTotal: {elapsed:.1f}s ({elapsed/n_total:.1f}s/trial)")
    print(f"\n=== POOLED PER TIER (across {len(MODELS)} models) ===")
    print(f"{'tier':<10} {'n':<5} {'n_s1a':<6} {'n_s1b':<6} {'reg_a%':<8} {'reg_b%':<8}")
    for tier, s in pooled.items():
        print(f"{tier:<10} {s['n']:<5} {s['n_s1_a_pass']:<6} {s['n_s1_b_pass']:<6} {s['regression_a_conditional']*100:6.1f}%  {s['regression_b_conditional']*100:6.1f}%")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
