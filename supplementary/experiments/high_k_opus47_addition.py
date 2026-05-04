#!/usr/bin/env python3
"""
high_k_opus47_addition.py

Companion to high_k_opus_experiment.py: adds Claude Opus 4.7 to the
high-k diagonal-cost regime experiment via the Anthropic SDK.

Mirrors the canonical methodology:
  - Same 4 tasks (clustered, hard_negative, medium, conflicting)
  - Same constraints, same verifiers, same prompt builder
  - Same TrialResult schema so a merge produces a 2-model artifact

Methodology deviations vs canonical (canonical = opus-4.5):
  1. opus-4.7 deprecates the `temperature` request param (returns
     invalid_request_error). The canonical run used TEMPERATURE=0.7;
     opus-4.7 here uses default Anthropic sampling (no temperature).
  2. Trial count defaults to 25/regime (100 total) here vs 100/regime
     in the canonical run, to keep this addition's API cost bounded.
     Use --trials to override. The cliff signal (clustered ~100% vs
     conflicting ~0%) is robust at N=25.

Usage:
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    python high_k_opus47_addition.py
    python high_k_opus47_addition.py --trials 50
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

N_WORKERS = 4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from high_k_opus_experiment import (
    HighKTask, TrialResult,
    build_clustered_task, build_hard_negative_task,
    build_medium_conflict_task, build_conflicting_task,
    build_prompt, extract_code, compute_pairwise_rho,
    HAS_EMBEDDINGS,
)

ADDITION_MODELS = {
    "opus-4.7":   {"id": "claude-opus-4-7",   "tag": "opus47", "accepts_temp": False},
    "opus-4.6":   {"id": "claude-opus-4-6",   "tag": "opus46", "accepts_temp": True},
    "sonnet-4.6": {"id": "claude-sonnet-4-6", "tag": "sonnet46", "accepts_temp": True},
}
CANONICAL_TEMPERATURE = 0.7  # canonical high_k_opus_experiment.py used 0.7
MAX_TOKENS = 1024
DEFAULT_TRIALS = 25
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = "supplementary/experiments/high_k_opus47_addition.py"
CANONICAL_PATH = REPO_ROOT / "supplementary/experiments/outputs/high_k_opus/high_k_opus_results.json"


def call_anthropic_model(client, model_id: str, prompt: str, accepts_temp: bool) -> tuple[str, int]:
    """opus-4.7 rejects temperature; opus-4.6/sonnet-4.6 accept it."""
    kwargs = {
        "model": model_id,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if accepts_temp:
        kwargs["temperature"] = CANONICAL_TEMPERATURE
    msg = client.messages.create(**kwargs)
    response = msg.content[0].text if msg.content else ""
    tokens = msg.usage.input_tokens + msg.usage.output_tokens
    return response, tokens


_USER_PATH_RX = re.compile(r"[Cc]:[\\/]Users[\\/][^\\/'\"\s]+", re.IGNORECASE)
_USERNAME_RX = re.compile(r"\bpaulc\b", re.IGNORECASE)

def _redact(s: str) -> str:
    """Scrub local user paths and usernames from verifier output before
    we persist them. The correctness verifier writes the model's code to a
    tempfile and the subprocess error capture can include the full path,
    which leaks the local username into supposedly-anonymous artifacts."""
    if not isinstance(s, str):
        return s
    s = _USER_PATH_RX.sub("<redacted_temp_path>", s)
    s = _USERNAME_RX.sub("<redacted_user>", s)
    return s


def run_trial(client, model_id: str, accepts_temp: bool, task: HighKTask,
              trial_num: int, rho_pairs: dict) -> TrialResult:
    prompt = build_prompt(task)
    response, tokens = call_anthropic_model(client, model_id, prompt, accepts_temp)
    code = extract_code(response)

    results = {}
    messages = {}
    if code:
        for c in task.constraints:
            passed, msg = c.verify(code, "")
            results[c.name] = passed
            messages[c.name] = _redact(msg)
    else:
        for c in task.constraints:
            results[c.name] = False
            messages[c.name] = "No code extracted"

    n_pass = sum(results.values())
    all_pass = n_pass == len(task.constraints)
    max_rho = max(rho_pairs.values()) if rho_pairs else 0.0
    mean_rho = sum(rho_pairs.values()) / len(rho_pairs) if rho_pairs else 0.0

    return TrialResult(
        task_id=task.task_id,
        regime=task.regime,
        nominal_k=task.nominal_k,
        trial=trial_num,
        all_pass=all_pass,
        n_pass=n_pass,
        constraint_results=results,
        constraint_messages=messages,
        max_pairwise_rho=max_rho,
        mean_pairwise_rho=mean_rho,
        response_length=len(response),
        tokens_used=tokens,
        code_extracted=code is not None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS,
                        help=f"trials per regime (default: {DEFAULT_TRIALS}; canonical was 100)")
    parser.add_argument("--model", choices=list(ADDITION_MODELS.keys()), default="opus-4.7")
    args = parser.parse_args()
    model_name = args.model
    cfg = ADDITION_MODELS[model_name]
    model_id = cfg["id"]
    accepts_temp = cfg["accepts_temp"]
    tag = cfg["tag"]
    output_dir = REPO_ROOT / f"supplementary/experiments/outputs/high_k_{tag}"
    output_path = output_dir / f"high_k_{tag}_results.json"
    merged_output_path = output_dir / f"high_k_opus_with_{tag}_results.json"

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
    print(f"[{model_name}]   OK (accepts_temp={accepts_temp})", flush=True)

    tasks = [
        build_clustered_task(),
        build_hard_negative_task(),
        build_medium_conflict_task(),
        build_conflicting_task(),
    ]

    encoder = None
    if HAS_EMBEDDINGS:
        try:
            from sentence_transformers import SentenceTransformer
            print("Loading sentence encoder for rho_hat...", flush=True)
            encoder = SentenceTransformer('all-MiniLM-L6-v2')
        except Exception as e:
            print(f"  encoder load failed ({e}); continuing without rho_hat", flush=True)
            encoder = None

    print(f"Will run {len(tasks)} regimes x {args.trials} trials = {len(tasks)*args.trials} total cells, "
          f"parallel N_WORKERS={N_WORKERS}", flush=True)
    t_start = time.time()

    raw_results: list[TrialResult] = []
    print_lock = threading.Lock()

    def run_one(task: HighKTask, trial_idx: int, rho_pairs: dict) -> TrialResult:
        t0 = time.time()
        r = run_trial(client, model_id, accepts_temp, task, trial_idx, rho_pairs)
        dt = time.time() - t0
        with print_lock:
            tag_status = "PASS" if r.all_pass else f"FAIL({r.n_pass}/{r.nominal_k})"
            print(f"[{model_name}]   [done] {r.regime:<14} trial {trial_idx:>3}: {tag_status} ({dt:.1f}s)", flush=True)
        return r

    cells = []
    rho_pairs_by_task = {}
    for task in tasks:
        rho_pairs = compute_pairwise_rho(task.constraints, encoder)
        rho_pairs_by_task[task.task_id] = rho_pairs
        for trial_idx in range(1, args.trials + 1):
            cells.append((task, trial_idx, rho_pairs))

    with ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = {executor.submit(run_one, t, i, rp): (t.task_id, i) for (t, i, rp) in cells}
        for fut in as_completed(futures):
            tid, ti = futures[fut]
            try:
                raw_results.append(fut.result())
            except Exception as e:
                print(f"  [FAIL] {tid} trial {ti}: {e}", file=sys.stderr, flush=True)

    elapsed = time.time() - t_start

    # Aggregate by regime
    by_regime: dict[str, list[TrialResult]] = {}
    for r in raw_results:
        by_regime.setdefault(r.regime, []).append(r)

    regime_summary = {}
    for regime, rs in by_regime.items():
        n = len(rs)
        n_all_pass = sum(1 for r in rs if r.all_pass)
        regime_summary[regime] = {
            "n_trials": n,
            "n_all_pass": n_all_pass,
            "all_pass_rate": (n_all_pass / n) if n else 0.0,
            "max_rho_hat": max((r.max_pairwise_rho for r in rs), default=0.0),
            "mean_n_pass": (sum(r.n_pass for r in rs) / n) if n else 0.0,
        }

    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)

    if accepts_temp:
        method_note = (f"1) {model_id} accepts the standard `temperature` request param. Run uses "
                       f"temperature={CANONICAL_TEMPERATURE}, matching the canonical opus-4.5 run. "
                       f"Direct apples-to-apples comparison. 2) Trial count is " + str(args.trials) +
                       "/regime here vs 100/regime in the canonical run.")
    else:
        method_note = ("1) claude-opus-4-7 rejects any non-default value of `temperature`, `top_p`, "
                       "or `top_k` with a 400 error (Anthropic Messages API; per official Opus 4.7 "
                       "release notes). The parameter is therefore omitted for opus-4.7 only. "
                       "Anthropic does not publish the numeric server-side default sampling "
                       "temperature for this model; the docs route users toward `effort` levels and "
                       "prompting. Canonical comparison runs in this experiment used "
                       f"TEMPERATURE={CANONICAL_TEMPERATURE}, so the effective sampling regime here "
                       "is unspecified and not directly comparable. Adaptive thinking is OFF by "
                       "default on opus-4.7 (no `thinking` field set here, matching the canonical "
                       "run); no `effort` level was set. 2) Trial count is " + str(args.trials) +
                       "/regime here vs 100/regime in the canonical run.")
    payload = {
        "_meta": {
            "experiment": f"high_k_{tag}_addition",
            "via": f"Anthropic SDK ({model_id})",
            "generator_script": SCRIPT_PATH,
            "script_hash": script_hash,
            "model": model_name,
            "model_id": model_id,
            "n_workers": N_WORKERS,
            "trials_per_regime": args.trials,
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "finished_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": elapsed,
            "n_trials_total": len(raw_results),
            "merge_target": str(CANONICAL_PATH.relative_to(REPO_ROOT)),
            "note": f"Companion experiment: extends the canonical opus-4.5 high-k regime experiment "
                    f"to {model_id}. Uses the Anthropic SDK directly.",
            "methodology_deviation": method_note,
            "sampling_default_source": "https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7",
        },
        "regime_summary": regime_summary,
        "results": [asdict(r) for r in raw_results],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n[{model_name}] Wrote {output_path}")
    for regime, s in regime_summary.items():
        print(f"[{model_name}]   {regime:<14}: {s['n_all_pass']}/{s['n_trials']} all-pass "
              f"({s['all_pass_rate']*100:.0f}%), max rho_hat={s['max_rho_hat']:.3f}")

    # Merge: load canonical, append new model into a 2-model artifact
    canonical = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))
    canonical_results = canonical.get("results", [])
    for r in canonical_results:
        r.setdefault("model", "opus-4.5")
    new_results = [{**asdict(r), "model": model_name} for r in raw_results]

    merged = {
        "_meta": {
            "experiment": "high_k_opus_multi_model",
            "models": ["opus-4.5", model_name],
            "canonical_source": canonical.get("experiment", "high_k_opus"),
            "addition_source": str(output_path.relative_to(REPO_ROOT)),
            "addition_script_hash": script_hash,
            "merged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "regimes": canonical.get("regimes"),
        "methodology": canonical.get("methodology"),
        "per_model_summary": {
            "opus-4.5": {
                "trials_per_regime": 100,
                "regimes": {
                    rg: {
                        "n_trials": sum(1 for r in canonical_results if r["regime"] == rg),
                        "n_all_pass": sum(1 for r in canonical_results if r["regime"] == rg and r["all_pass"]),
                    } for rg in {r["regime"] for r in canonical_results}
                },
            },
            model_name: {
                "trials_per_regime": args.trials,
                "regimes": regime_summary,
            },
        },
        "results": canonical_results + new_results,
    }
    merged_output_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"[{model_name}] Wrote {merged_output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
