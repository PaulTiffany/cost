#!/usr/bin/env python3
"""
lipschitz_calibration.py - Per-token embedding Lipschitz constant calibration.

Closes the artifact gap for paper Table tab:lipschitz_calibration. The
paper claims per-model L_hat values and tightness percentages that
were never previously backed by a committed script.

Method (matches paper line 1156):
  For each model and task, generate N completions. For each completion,
  embed each token-prefix with MiniLM-L6-v2 and compute the per-token
  displacement ||x_{t+1} - x_t|| in embedding space. Report per-model
  mean +/- std of the per-token displacement.

Outputs:
  supplementary/experiments/lipschitz_calibration_results.json

Usage:
  python supplementary/experiments/lipschitz_calibration.py
  python supplementary/experiments/lipschitz_calibration.py --n-trials 10
  python supplementary/experiments/lipschitz_calibration.py --models Qwen/Qwen2.5-Coder-1.5B-Instruct
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
from code_constraint_tasks import TASKS  # noqa: E402

OUTPUT_JSON = SCRIPT_DIR / "lipschitz_calibration_results.json"

# Paper's specified models (Table tab:lipschitz_calibration row labels)
DEFAULT_MODELS = [
    "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "deepseek-ai/deepseek-coder-1.3b-instruct",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
]
MODEL_DISPLAY_NAMES = {
    "Qwen/Qwen2.5-Coder-1.5B-Instruct": "Qwen-2.5-Coder",
    "deepseek-ai/deepseek-coder-1.3b-instruct": "DeepSeek-Coder",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0": "TinyLlama-1.1B",
    "Qwen/Qwen2.5-3B-Instruct": "Qwen-2.5-3B",
}

# Paper specifies MiniLM-L6-v2 for embedding measurement
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

DEFAULT_N_TRIALS = 10  # 50 in paper; reduced for deadline-budget run
DEFAULT_TASKS = ["factorial", "fibonacci", "is_palindrome", "sum_digits", "find_max", "count_vowels"]
TEMPERATURE = 0.7
MAX_NEW_TOKENS = 128
TIGHTNESS_BAND_PCT = 12  # paper: "tight within 12%"


@dataclass
class CompletionResult:
    model: str
    task_id: str
    trial: int
    n_tokens: int
    # Unnormalized embedding metrics (raw MiniLM output)
    per_token_displacements: list[float]
    mean_displacement: float
    total_displacement: float  # ||x_T - x_0||
    bound_LT: float            # L_for_this_completion * T
    bound_ratio: float         # total_displacement / bound_LT
    is_tight: bool             # bound_ratio within (1 - TIGHTNESS_BAND_PCT/100, 1.0]
    # L2-normalized embedding metrics (paper's likely default)
    per_token_displacements_norm: list[float] = field(default_factory=list)
    mean_displacement_norm: float = 0.0
    total_displacement_norm: float = 0.0
    bound_LT_norm: float = 0.0
    bound_ratio_norm: float = 0.0
    is_tight_norm: bool = False


def get_embedder():
    """Load the MiniLM-L6-v2 encoder (sentence-transformers)."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("ERROR: sentence_transformers not installed. pip install sentence-transformers",
              file=sys.stderr)
        sys.exit(2)
    print(f"Loading embedder: {EMBEDDING_MODEL}")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    return embedder


def load_model(model_name: str):
    print(f"Loading {model_name} ...", flush=True)
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else None,
    )
    model.eval()
    print(f"  loaded in {time.time()-t0:.1f}s on {next(model.parameters()).device}", flush=True)
    return model, tokenizer


def build_prompt(task) -> str:
    """Minimal prompt: just the task description."""
    return f"Write a Python function that solves the following task.\n\nTASK: {task.description}\n\nProvide your solution in a single ```python code block.\n"


def run_completion(model, tokenizer, prompt: str, seed: int) -> tuple[list[str], list[int]]:
    """Generate a completion. Return (prefix_strings_list, token_ids_list).

    prefix_strings_list[t] = decoded text of tokens[:t+1]
    so x_0 = embed(prompt), x_t = embed(prompt + decoded(tokens[:t+1])).
    """
    torch.manual_seed(seed)
    inputs = tokenizer(prompt, return_tensors="pt").to(next(model.parameters()).device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=TEMPERATURE,
            do_sample=True,
            pad_token_id=tokenizer.pad_token_id,
        )
    # outputs shape: (1, prompt_len + new_tokens)
    new_token_ids = outputs[0, inputs["input_ids"].shape[1]:].tolist()
    # Build prefix strings: prompt + decoded(new_tokens[:t+1]) for t in 0..len(new_tokens)
    prefix_strings = [prompt]
    for t in range(len(new_token_ids)):
        prefix_text = prompt + tokenizer.decode(new_token_ids[:t + 1], skip_special_tokens=True)
        prefix_strings.append(prefix_text)
    return prefix_strings, new_token_ids


def compute_displacements(embedder, prefix_strings: list[str]) -> tuple[list[float], float, list[float], float]:
    """Embed each prefix; return (raw_disps, raw_total, norm_disps, norm_total).

    Computes BOTH unnormalized and L2-normalized per-token displacements
    in the same embedding pass (the embedding is the expensive part;
    normalization is a cheap post-step).
    """
    embeddings = embedder.encode(prefix_strings, batch_size=32, show_progress_bar=False,
                                  convert_to_numpy=True, normalize_embeddings=False)
    # Compute L2-normalized version
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms < 1e-12, 1.0, norms)  # avoid div-by-zero on degenerate rows
    embeddings_norm = embeddings / norms
    # Per-token displacements (both flavors)
    disps_raw: list[float] = []
    disps_norm: list[float] = []
    for t in range(len(embeddings) - 1):
        disps_raw.append(float(np.linalg.norm(embeddings[t + 1] - embeddings[t])))
        disps_norm.append(float(np.linalg.norm(embeddings_norm[t + 1] - embeddings_norm[t])))
    total_raw = float(np.linalg.norm(embeddings[-1] - embeddings[0]))
    total_norm = float(np.linalg.norm(embeddings_norm[-1] - embeddings_norm[0]))
    return disps_raw, total_raw, disps_norm, total_norm


def calibrate_model(model_name: str, embedder, tasks, n_trials: int,
                     checkpoint_path: Path | None = None,
                     existing_results: list[CompletionResult] | None = None) -> list[CompletionResult]:
    """Run calibration for one model; checkpoint per (model, task) if path given.

    If existing_results contains entries for this model + task, those trials
    are skipped (resumes from checkpoint).
    """
    model, tokenizer = load_model(model_name)
    results: list[CompletionResult] = list(existing_results or [])
    completed_keys = {(r.model, r.task_id, r.trial) for r in results}
    for task_id in tasks:
        task = next((t for t in TASKS if t.task_id == task_id), None)
        if task is None:
            print(f"  [warn] task {task_id} not found, skipping")
            continue
        prompt = build_prompt(task)
        print(f"  Task {task_id}: ", end="", flush=True)
        for trial in range(n_trials):
            if (model_name, task_id, trial) in completed_keys:
                print(f"t{trial}(cached) ", end="", flush=True)
                continue
            t0 = time.time()
            prefix_strings, token_ids = run_completion(model, tokenizer, prompt, seed=42 + trial)
            if len(prefix_strings) < 2:
                print(f"skip(len={len(prefix_strings)}) ", end="", flush=True)
                continue
            disps_raw, total_raw, disps_norm, total_norm = compute_displacements(embedder, prefix_strings)
            if not disps_raw:
                continue
            n_tokens = len(disps_raw)
            mean_raw = float(np.mean(disps_raw))
            bound_LT_raw = mean_raw * n_tokens
            ratio_raw = total_raw / bound_LT_raw if bound_LT_raw > 0 else 0.0
            tight_band = 1.0 - TIGHTNESS_BAND_PCT / 100.0
            mean_norm = float(np.mean(disps_norm))
            bound_LT_norm = mean_norm * n_tokens
            ratio_norm = total_norm / bound_LT_norm if bound_LT_norm > 0 else 0.0
            results.append(CompletionResult(
                model=model_name, task_id=task_id, trial=trial,
                n_tokens=n_tokens,
                per_token_displacements=disps_raw,
                mean_displacement=mean_raw,
                total_displacement=total_raw,
                bound_LT=bound_LT_raw,
                bound_ratio=ratio_raw,
                is_tight=ratio_raw >= tight_band,
                per_token_displacements_norm=disps_norm,
                mean_displacement_norm=mean_norm,
                total_displacement_norm=total_norm,
                bound_LT_norm=bound_LT_norm,
                bound_ratio_norm=ratio_norm,
                is_tight_norm=ratio_norm >= tight_band,
            ))
            elapsed = time.time() - t0
            print(f"t{trial}({elapsed:.1f}s) ", end="", flush=True)
        print()
        # Checkpoint after each (model, task)
        if checkpoint_path is not None:
            _write_partial_checkpoint(checkpoint_path, results, n_trials)
    del model, tokenizer
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return results


def _write_partial_checkpoint(path: Path, results: list[CompletionResult], n_trials: int) -> None:
    """Save crash-resilient partial checkpoint after each (model, task)."""
    summary = aggregate(results)
    payload = {
        "_meta": {"checkpoint": True, "n_completions_so_far": len(results), "n_trials_per_task": n_trials},
        "per_model_summary": summary,
        "completions": [_completion_to_dict(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _completion_to_dict(r: CompletionResult) -> dict:
    out = asdict(r)
    out["per_token_displacements"] = [round(d, 6) for d in r.per_token_displacements]
    out["per_token_displacements_norm"] = [round(d, 6) for d in r.per_token_displacements_norm]
    return out


def aggregate(results: list[CompletionResult]) -> dict:
    """Compute per-model summary stats."""
    by_model: dict[str, dict] = {}
    for model_name in sorted({r.model for r in results}):
        rs = [r for r in results if r.model == model_name]
        all_disps = [d for r in rs for d in r.per_token_displacements]
        if not all_disps:
            continue
        # L_hat: per-completion mean displacement; report mean+std across completions
        per_completion_means = [r.mean_displacement for r in rs]
        n_tight = sum(1 for r in rs if r.is_tight)
        by_model[model_name] = {
            "display_name": MODEL_DISPLAY_NAMES.get(model_name, model_name),
            "n_completions": len(rs),
            "n_tasks": len({r.task_id for r in rs}),
            "L_hat_mean": float(np.mean(per_completion_means)),
            "L_hat_std": float(np.std(per_completion_means)),
            "L_hat_per_token_mean": float(np.mean(all_disps)),
            "L_hat_per_token_std": float(np.std(all_disps)),
            "L_hat_per_token_p95": float(np.percentile(all_disps, 95)),
            "L_hat_per_token_p99": float(np.percentile(all_disps, 99)),
            "L_hat_per_token_max": float(np.max(all_disps)),
            "tightness_pct": 100.0 * n_tight / len(rs),
            "outliers_pct": 100.0 * (len(rs) - n_tight) / len(rs),
        }
    return by_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                       formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--tasks", default=",".join(DEFAULT_TASKS))
    parser.add_argument("--n-trials", type=int, default=DEFAULT_N_TRIALS)
    parser.add_argument("--output", type=Path, default=OUTPUT_JSON)
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]

    print("=" * 70)
    print("LIPSCHITZ CALIBRATION (per-token embedding displacement)")
    print("=" * 70)
    print(f"  Models:      {models}")
    print(f"  Tasks:       {tasks}")
    print(f"  N trials:    {args.n_trials}")
    print(f"  Embedder:    {EMBEDDING_MODEL}")
    print(f"  CUDA:        {torch.cuda.is_available()}")
    print()

    embedder = get_embedder()
    all_results: list[CompletionResult] = []
    for m in models:
        print(f"--- {m} ---")
        all_results.extend(calibrate_model(m, embedder, tasks, args.n_trials))
        print()

    summary = aggregate(all_results)
    payload = {
        "_meta": {
            "experiment": "lipschitz_calibration",
            "method": "per-token embedding displacement ||x_{t+1} - x_t||, MiniLM-L6-v2",
            "n_trials_per_task": args.n_trials,
            "max_new_tokens": MAX_NEW_TOKENS,
            "temperature": TEMPERATURE,
            "tightness_band_pct": TIGHTNESS_BAND_PCT,
            "tightness_definition": (
                f"completion is 'tight' if ||x_T - x_0|| / (mean_per_token_disp * T) >= "
                f"{1.0 - TIGHTNESS_BAND_PCT / 100.0:.2f}"
            ),
        },
        "per_model_summary": summary,
        "completions": [{**asdict(r), "per_token_displacements": [round(d, 6) for d in r.per_token_displacements]} for r in all_results],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for k, v in summary.items():
        print(f"  {v['display_name']:20s} L_hat = {v['L_hat_mean']:.4f} +/- {v['L_hat_std']:.4f}  "
              f"tightness = {v['tightness_pct']:.1f}%  (n={v['n_completions']})")
    print()
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
