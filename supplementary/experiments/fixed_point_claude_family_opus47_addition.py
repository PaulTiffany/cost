#!/usr/bin/env python3
"""
fixed_point_claude_family_opus47_addition.py

Companion to fixed_point_claude_family.py: adds Claude Opus 4.7 to the
claude family fixed-point experiment using OpenRouter (since the existing
script requires ANTHROPIC_API_KEY which we don't have here).

Mirrors the methodology:
  - Same 6 tasks (loaded from fixed_point_claude_family.py)
  - Same prompts (oneshot + 3-stage staged)
  - Same scoring (closer-to-target wins; staged_wins counted)
  - Output schema matches existing fixed_point_claude_family.json so a
    merge produces an 8-model artifact

Usage:
    $env:OPENROUTER_API_KEY = "sk-or-..."
    python fixed_point_claude_family_opus47_addition.py
"""
import json
import os
import sys
import time
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

N_WORKERS = 4  # conservative for Anthropic Opus rate limits

# Reuse the task constructors and prompts from the canonical script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixed_point_claude_family import (
    FixedPointTask, make_digit_sum_task, make_vowel_count_task,
    make_word_digit_task, make_self_counting_task,
    prompt_oneshot, prompt_stage1_draft,
    prompt_stage2_measure_adjust, prompt_stage3_verify_adjust,
    verify_fixed_point, char_count,
)

# Multi-model addition support: opus-4.7 (no temperature accepted),
# opus-4.6 and sonnet-4.6 (both accept temperature like the canonical run).
ADDITION_MODELS = {
    "opus-4.7":   {"id": "claude-opus-4-7",        "tag": "opus47", "accepts_temp": False},
    "opus-4.6":   {"id": "claude-opus-4-6",        "tag": "opus46", "accepts_temp": True},
    "sonnet-4.6": {"id": "claude-sonnet-4-6",      "tag": "sonnet46", "accepts_temp": True},
}
CANONICAL_TEMPERATURE = 0.7  # matches the canonical fixed_point_claude_family.json
MAX_STAGE_ROUNDS = 3
SEED = 42
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = "supplementary/experiments/fixed_point_claude_family_opus47_addition.py"
CANONICAL_PATH = REPO_ROOT / "supplementary/experiments/fixed_point_claude_family.json"


def build_tasks() -> list[FixedPointTask]:
    """Same 6 tasks the canonical experiment used (matches existing JSON task_ids)."""
    return [
        make_digit_sum_task(42),       # digit_sum_42
        make_digit_sum_task(66),       # digit_sum_66
        make_vowel_count_task(31),     # vowel_count_31
        make_vowel_count_task(43),     # vowel_count_43
        make_word_digit_task(43),      # word_digit_43
        make_self_counting_task(43),   # self_count_43
    ]


def call_openrouter(client, model_id: str, prompt: str, max_tokens: int) -> str:
    resp = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.7,
    )
    return resp.choices[0].message.content or ""


def call_anthropic(client, model_id: str, prompt: str, max_tokens: int, accepts_temp: bool = True) -> str:
    """Anthropic SDK call. opus-4.7 rejects `temperature` (per Anthropic docs);
    opus-4.6 and sonnet-4.6 accept it. Pass accepts_temp accordingly."""
    kwargs = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if accepts_temp:
        kwargs["temperature"] = CANONICAL_TEMPERATURE
    resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if hasattr(block, "text"))


def run_oneshot(client, model_id: str, task: FixedPointTask, accepts_temp: bool, budget: int = 800) -> dict:
    response = call_anthropic(client, model_id, prompt_oneshot(task), budget, accepts_temp)
    v = verify_fixed_point(response, task)
    return {
        "task_id": task.task_id,
        "protocol": "oneshot",
        "response": response,
        **v,
    }


def run_staged(client, model_id: str, task: FixedPointTask, accepts_temp: bool, budget: int = 800) -> dict:
    draft = call_anthropic(client, model_id, prompt_stage1_draft(task), budget // 2, accepts_temp)
    current = draft
    rounds = 0
    history = []
    for round_idx in range(MAX_STAGE_ROUNDS):
        rounds += 1
        target = task.target_fn(current)
        actual = task.actual_fn(current)
        if actual == target:
            break
        if round_idx == 0:
            prompt = prompt_stage2_measure_adjust(task, current, target, actual)
        else:
            prompt = prompt_stage3_verify_adjust(task, current, target, actual)
            if prompt is None:
                break
        adjust_budget = max(200, budget // 2)
        current = call_anthropic(client, model_id, prompt, adjust_budget, accepts_temp)
        history.append({"round": round_idx, "target": target, "actual": actual})
    v = verify_fixed_point(current, task)
    return {
        "task_id": task.task_id,
        "protocol": "staged",
        "response": current,
        "rounds": rounds,
        "history": history,
        **v,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=list(ADDITION_MODELS.keys()), default="opus-4.7",
                        help="model name to add to the canonical 7-model set")
    args = parser.parse_args()
    model_name = args.model
    cfg = ADDITION_MODELS[model_name]
    model_id = cfg["id"]
    accepts_temp = cfg["accepts_temp"]
    tag = cfg["tag"]
    output_path = REPO_ROOT / f"supplementary/experiments/fixed_point_claude_family_{tag}_addition.json"
    merged_output_path = REPO_ROOT / f"supplementary/experiments/fixed_point_claude_family_with_{tag}.json"

    try:
        import anthropic
    except ImportError:
        print("anthropic package not found", file=sys.stderr); return 1
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY env var required", file=sys.stderr); return 1
    client = anthropic.Anthropic(api_key=api_key)

    print(f"[{model_name}] Probing {model_id}...", flush=True)
    try:
        client.messages.create(model=model_id, max_tokens=1,
                               messages=[{"role": "user", "content": "ping"}])
    except Exception as e:
        print(f"[{model_name}] Probe failed: {e}", file=sys.stderr); return 1
    print(f"[{model_name}]   OK", flush=True)

    tasks = build_tasks()
    cells = [(task, "oneshot") for task in tasks] + [(task, "staged") for task in tasks]
    print(f"[{model_name}] Running {len(tasks)} tasks x 2 protocols = {len(cells)} cells in parallel "
          f"(N_WORKERS={N_WORKERS}, accepts_temp={accepts_temp})", flush=True)
    t_start = time.time()

    raw_results: list[dict] = []
    print_lock = threading.Lock()

    def run_cell(task: FixedPointTask, protocol: str) -> dict:
        t0 = time.time()
        if protocol == "oneshot":
            r = run_oneshot(client, model_id, task, accepts_temp)
        else:
            r = run_staged(client, model_id, task, accepts_temp)
        dt = time.time() - t0
        with print_lock:
            extra = f" rounds={r.get('rounds')}" if protocol == "staged" else ""
            print(f"[{model_name}]   [done] {task.task_id} {protocol}: actual={r['actual']} "
                  f"target={r['target']} delta={r['delta']}{extra} ({dt:.1f}s)", flush=True)
        return {"model_name": model_name, "model_id": model_id, **r}

    with ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = {executor.submit(run_cell, t, p): (t.task_id, p) for (t, p) in cells}
        for fut in as_completed(futures):
            tid, proto = futures[fut]
            try:
                raw_results.append(fut.result())
            except Exception as e:
                print(f"  [FAIL] {tid} {proto}: {e}", file=sys.stderr, flush=True)

    elapsed = time.time() - t_start

    # Aggregate into the same per-model schema used by fixed_point_claude_family.json
    by_task: dict[str, dict] = {}
    for r in raw_results:
        tid = r["task_id"]
        if tid not in by_task:
            by_task[tid] = {}
        by_task[tid][r["protocol"]] = r

    task_details = []
    staged_wins = 0
    for tid, protos in by_task.items():
        os_r = protos.get("oneshot")
        st_r = protos.get("staged")
        if not (os_r and st_r):
            continue
        os_delta = abs(os_r["delta"])
        st_delta = abs(st_r["delta"])
        improvement_ratio = (os_delta / st_delta) if st_delta > 0 else float("inf")
        winner = "staged" if st_delta < os_delta else ("oneshot" if os_delta < st_delta else "tie")
        if winner == "staged":
            staged_wins += 1
        task_details.append({
            "task_id": tid,
            "oneshot_delta": os_r["delta"],
            "staged_delta": st_r["delta"],
            "abs_oneshot": os_delta,
            "abs_staged": st_delta,
            "improvement_ratio": improvement_ratio,
            "winner": winner,
        })

    # Compute avg_improvement_ratio matching the canonical aggregator (geometric-style)
    # The existing JSON uses arithmetic mean of per-task improvement_ratio, capped per-task at inf.
    finite_ratios = [t["improvement_ratio"] for t in task_details if t["improvement_ratio"] != float("inf")]
    avg_imp = (sum(finite_ratios) / len(finite_ratios)) if finite_ratios else float("inf")

    new_entry = {
        "model_name": model_name,
        "tasks_analyzed": len(task_details),
        "staged_wins": staged_wins,
        "avg_improvement_ratio": avg_imp,
        "task_details": task_details,
    }

    # Compute script hash for provenance
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    if accepts_temp:
        method_note = (f"{model_id} accepts the standard `temperature` request param. "
                       f"Run uses temperature={CANONICAL_TEMPERATURE}, matching the canonical "
                       f"7-model fixed_point_claude_family.json. Direct apples-to-apples comparison.")
    else:
        method_note = ("claude-opus-4-7 rejects any non-default value of `temperature`, `top_p`, or "
                       "`top_k` with a 400 error (Anthropic Messages API; per official Opus 4.7 release "
                       "notes). The parameter is therefore omitted for opus-4.7 only. Anthropic does not "
                       "publish the numeric server-side default sampling temperature for this model; the "
                       "docs route users toward `effort` levels and prompting. Canonical comparison runs "
                       f"in this experiment used temperature={CANONICAL_TEMPERATURE}, so the effective "
                       "sampling regime here is unspecified and not directly comparable. Adaptive "
                       "thinking is OFF by default on opus-4.7 (no `thinking` field set here, matching "
                       "the canonical run); no `effort` level was set.")
    payload = {
        "_meta": {
            "experiment": f"fixed_point_claude_family_{tag}_addition",
            "via": f"Anthropic SDK ({model_id})",
            "generator_script": SCRIPT_PATH,
            "script_hash": script_hash,
            "models": [model_name],
            "model_ids": {model_name: model_id},
            "n_workers": N_WORKERS,
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "finished_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": elapsed,
            "n_trials": len(raw_results),
            "tasks": [t.task_id for t in tasks],
            "max_stage_rounds": MAX_STAGE_ROUNDS,
            "merge_target": str(CANONICAL_PATH.relative_to(REPO_ROOT)),
            "note": f"Companion experiment: extends the canonical 7-model claude family with {model_id}. "
                    "Uses the Anthropic SDK directly (matches the canonical script's transport).",
            "methodology_deviation": method_note,
            "sampling_default_source": "https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7",
        },
        "model_analyses": [new_entry],
        "results": raw_results,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n[{model_name}] Wrote {output_path}")
    print(f"[{model_name}]   tasks={len(task_details)}  staged_wins={staged_wins}  avg_imp={avg_imp:.2f}")

    # Merge: load canonical, append new model, write merged.
    canonical = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))
    merged = {**canonical}
    merged["model_analyses"] = canonical.get("model_analyses", []) + [new_entry]
    if "results" in canonical:
        merged["results"] = canonical["results"] + raw_results
    elif "results" in canonical or raw_results:
        merged["results"] = raw_results
    cm = merged.get("_meta", {})
    cm["addition_note"] = (f"8-model merged version: 7 canonical + {model_name}. "
                           f"Source addition: " + str(output_path.relative_to(REPO_ROOT)))
    cm["addition_script_hash"] = script_hash
    cm["models_total"] = len({m["model_name"] for m in merged["model_analyses"]})
    merged["_meta"] = cm
    merged_output_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"[{model_name}] Wrote {merged_output_path} (merged 7+1 = {cm['models_total']} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
