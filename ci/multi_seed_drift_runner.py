#!/usr/bin/env python3
"""
multi_seed_drift_runner.py

Reruns a small set of API cells with the same parameters and writes the
collected drift data to ci/multi_seed_drift_data.json. The data is then
verified by ci/multi_seed_drift_check.py (the cert layer L25) on every
cert run.

This script is API-spending and is NOT invoked by the cert. The user
runs it manually when they want to refresh the cache.

Cells targeted (kept small by design; 3 models x 1 task x 1 protocol x
5 reruns = 15 calls). The point is to capture a baseline variance, not
a comprehensive sweep.

Usage:
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    python ci/multi_seed_drift_runner.py
"""
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "supplementary" / "experiments"))

from fixed_point_claude_family import (
    make_digit_sum_task, prompt_oneshot, verify_fixed_point,
)

OUTPUT = Path(__file__).resolve().parent / "multi_seed_drift_data.json"
N_RERUNS = 5
N_WORKERS = 4

CELLS = [
    {"model_name": "sonnet-4.5", "model_id": "claude-sonnet-4-5-20250929", "accepts_temp": True},
    {"model_name": "opus-4.6",   "model_id": "claude-opus-4-6",            "accepts_temp": True},
    {"model_name": "sonnet-4.6", "model_id": "claude-sonnet-4-6",          "accepts_temp": True},
]
TEMPERATURE = 0.7


def call_one(client, model_id: str, accepts_temp: bool, prompt: str, max_tokens: int = 800) -> str:
    kwargs = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if accepts_temp:
        kwargs["temperature"] = TEMPERATURE
    resp = client.messages.create(**kwargs)
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


def main() -> int:
    try:
        import anthropic
    except ImportError:
        print("anthropic package not found", file=sys.stderr); return 1
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY env var required", file=sys.stderr); return 1
    client = anthropic.Anthropic(api_key=api_key)

    task = make_digit_sum_task(42)
    prompt = prompt_oneshot(task)
    print(f"Task: {task.task_id} oneshot, {len(CELLS)} models, {N_RERUNS} reruns each "
          f"= {len(CELLS) * N_RERUNS} API calls total", flush=True)
    t_start = time.time()

    # Schedule (model, run_idx) pairs flat for max parallelism
    schedule = [(cell, run_idx) for cell in CELLS for run_idx in range(N_RERUNS)]
    raw_results: dict[str, list[dict]] = {}
    print_lock = threading.Lock()

    def run_one(cell: dict, run_idx: int) -> tuple[str, dict]:
        t0 = time.time()
        response = call_one(client, cell["model_id"], cell["accepts_temp"], prompt)
        v = verify_fixed_point(response, task)
        dt = time.time() - t0
        with print_lock:
            print(f"  [{cell['model_name']:<11}] run {run_idx+1}/{N_RERUNS}: "
                  f"actual={v['actual']} target={v['target']} delta={v['delta']} ({dt:.1f}s)", flush=True)
        return cell["model_name"], {
            "run_idx": run_idx,
            "actual": v["actual"],
            "target": v["target"],
            "delta": v["delta"],
            "is_fixed_point": v["is_fixed_point"],
            "elapsed_sec": dt,
        }

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = [ex.submit(run_one, c, i) for (c, i) in schedule]
        for fut in as_completed(futures):
            try:
                name, rec = fut.result()
                raw_results.setdefault(name, []).append(rec)
            except Exception as e:
                print(f"  [FAIL] {e}", file=sys.stderr, flush=True)

    elapsed = time.time() - t_start

    # Per-model drift metrics
    per_model = {}
    for model_name, runs in raw_results.items():
        runs_sorted = sorted(runs, key=lambda r: r["run_idx"])
        deltas = [r["delta"] for r in runs_sorted]
        actuals = [r["actual"] for r in runs_sorted]
        per_model[model_name] = {
            "model_id": next(c["model_id"] for c in CELLS if c["model_name"] == model_name),
            "n_runs": len(runs_sorted),
            "deltas": deltas,
            "actuals": actuals,
            "delta_mean": statistics.mean(deltas) if deltas else None,
            "delta_stdev": statistics.stdev(deltas) if len(deltas) >= 2 else 0.0,
            "actual_mean": statistics.mean(actuals) if actuals else None,
            "actual_stdev": statistics.stdev(actuals) if len(actuals) >= 2 else 0.0,
            "fixed_point_hit_rate": sum(1 for r in runs_sorted if r["is_fixed_point"]) / len(runs_sorted),
            "runs": runs_sorted,
        }

    payload = {
        "_meta": {
            "experiment": "multi_seed_drift_runner",
            "task_id": task.task_id,
            "protocol": "oneshot",
            "temperature": TEMPERATURE,
            "n_reruns_per_cell": N_RERUNS,
            "n_models": len(CELLS),
            "n_total_calls": sum(m["n_runs"] for m in per_model.values()),
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "finished_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": elapsed,
            "note": ("Drift baseline: same prompt and same temperature, repeated N_RERUNS times "
                     "per model. The check L25 reads this data and verifies that the per-cell "
                     "actual_stdev is below a threshold (default 200 chars). This is the API-side "
                     "BIS gap-fill identified by Gemini's taxonomy review."),
            "purpose": "Capture per-model variance under repeated identical sampling so that "
                       "future reruns can detect distributional drift.",
        },
        "per_model": per_model,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {OUTPUT}")
    print()
    print(f"{'model':<14} {'n':<3} {'mean_delta':<11} {'std_delta':<10} {'mean_actual':<12} {'std_actual'}")
    for name, m in per_model.items():
        print(f"  {name:<12} {m['n_runs']:<3} {m['delta_mean']:>10.1f}  {m['delta_stdev']:>9.1f}  "
              f"{m['actual_mean']:>11.1f}  {m['actual_stdev']:>9.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
