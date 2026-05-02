#!/usr/bin/env python3
"""
Code Constraint Experiment: Judge-free multi-constraint evaluation.

Paper: "The Cost of Cacophony" (ICML 2026)
  - main.tex, references.bib

Verifies:
  - Section 6 (sec:empirical): Main empirical validation
  - Claim C3: 0/4,800 smooth-regime refutations
  - Claim C9: 89% smooth / 11% pivot regime split
  - Table 2 (tab:calibration): Calibration benchmark
  - Table A8 (tab:baselines): Protocol comparison
  - Example 6.1 (ex:e2e): End-to-end regime prediction

Tests diagonal cost predictions using deterministic verifiers:
- Verifier A: Functional correctness (subprocess + unit tests)
- Verifier B: Format constraints (AST parsing)

Success = A AND B (both verifiers pass)

Design:
- 12 tasks (constant difficulty)
- 4 tiers (increasing format constraint strictness)
- 2 protocols (one-shot vs staged)
- N trials per cell (default 5)
- 3 models (run sequentially)

No LLM judge required. Fully reproducible.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import random
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


@dataclass
class ModelSpec:
    name: str
    load_in_4bit: bool = False


# Model configurations
MODEL_SPECS = [
    ModelSpec("Qwen/Qwen2.5-Coder-1.5B-Instruct"),
    ModelSpec("deepseek-ai/deepseek-coder-1.3b-instruct"),
    ModelSpec("TinyLlama/TinyLlama-1.1B-Chat-v1.0"),
    # Large model option: requires CUDA + bitsandbytes
    ModelSpec("Qwen/Qwen2.5-7B-Instruct", load_in_4bit=True),
    # Medium model option: stable on 6GB VRAM with 4-bit
    ModelSpec("Qwen/Qwen2.5-3B-Instruct", load_in_4bit=True),
]

DEFAULT_MODEL_NAMES = [
    "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "deepseek-ai/deepseek-coder-1.3b-instruct",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
]

# Experiment parameters
N_TRIALS = 5
TEMPERATURE = 0.7
MAX_NEW_TOKENS = 384
SEED_BASE = 42
CHECKPOINT_EVERY = 2


@dataclass
class TrialResult:
    """Result of a single trial."""
    task_id: str
    tier: str
    protocol: str
    trial: int
    pass_a: bool
    pass_b: bool
    pass_both: bool
    msg_a: str
    msg_b: str
    code_extracted: Optional[str]
    response_length: int
    generated_tokens: int
    hit_max_tokens: bool


def build_one_shot_prompt(task: CodeTask, tier: str) -> str:
    """Build one-shot prompt with all constraints upfront."""
    rules = FORMAT_TIERS[tier]
    format_text = format_rules_to_prompt(rules)

    return f"""Write a Python function that solves the following task.

TASK: {task.description}

FORMAT REQUIREMENTS:
{format_text}

Provide your solution in a single ```python code block.
"""


def build_staged_prompts(task: CodeTask, tier: str) -> List[str]:
    """Build staged prompts (3-step protocol)."""
    rules = FORMAT_TIERS[tier]
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
    max_new_tokens: int = MAX_NEW_TOKENS,
    temperature: float = TEMPERATURE,
    seed: Optional[int] = None,
) -> Tuple[str, int, bool]:
    """Generate a response from the model."""
    if seed is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        random.seed(seed)

    # Handle different chat template formats
    try:
        messages = [{"role": "user", "content": prompt}]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
    except Exception:
        # Fallback for models without chat template
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
    hit_max_tokens = gen_tokens >= max_new_tokens
    return response.strip(), gen_tokens, hit_max_tokens


def generate_staged_response(
    model,
    tokenizer,
    prompts: List[str],
    max_new_tokens: int = MAX_NEW_TOKENS,
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
    hit_max_tokens = False

    for i, prompt in enumerate(prompts):
        # Build conversation context
        if i == 0:
            full_prompt = prompt
        else:
            # Include previous exchanges
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
        hit_max_tokens = hit_max_tokens or (gen_tokens >= MAX_NEW_TOKENS)
        conversation.append(response.strip())

    # Return final response (stage 3)
    return conversation[-1], total_tokens, hit_max_tokens


def stable_seed(*parts: str, base: int = SEED_BASE) -> int:
    """Derive a stable seed from experiment identifiers."""
    payload = "|".join([str(base), *map(str, parts)]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "little")


def run_trial(
    model,
    tokenizer,
    task: CodeTask,
    tier: str,
    protocol: str,
    trial_num: int,
    model_name: str,
) -> TrialResult:
    """Run a single trial and return results."""
    seed = stable_seed(model_name, task.task_id, tier, protocol, trial_num)

    if protocol == "one_shot":
        prompt = build_one_shot_prompt(task, tier)
        response, gen_tokens, hit_max_tokens = generate_response(
            model,
            tokenizer,
            prompt,
            seed=seed,
        )
    else:  # staged
        prompts = build_staged_prompts(task, tier)
        response, gen_tokens, hit_max_tokens = generate_staged_response(
            model,
            tokenizer,
            prompts,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=TEMPERATURE,
            seed=seed,
        )

    # Extract code and verify
    code = extract_code_block(response)
    rules = FORMAT_TIERS[tier]
    result = verify_both(code, task.test_code, rules)

    return TrialResult(
        task_id=task.task_id,
        tier=tier,
        protocol=protocol,
        trial=trial_num,
        pass_a=result["pass_a"],
        pass_b=result["pass_b"],
        pass_both=result["pass_both"],
        msg_a=result["msg_a"],
        msg_b=result["msg_b"],
        code_extracted=code[:500] if code else None,  # Truncate for storage
        response_length=len(response),
        generated_tokens=gen_tokens,
        hit_max_tokens=hit_max_tokens,
    )


def _resolve_local_snapshot(model_name: str) -> Path:
    hf_home = os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    model_dir = Path(hf_home) / "hub" / f"models--{model_name.replace('/', '--')}"
    snapshots_dir = model_dir / "snapshots"
    if not snapshots_dir.exists():
        raise FileNotFoundError(f"No local snapshots found for {model_name}.")

    snapshots = sorted(snapshots_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    if not snapshots:
        raise FileNotFoundError(f"No local snapshots found for {model_name}.")
    return snapshots[0]


def load_model(model_spec: ModelSpec, device: str):
    """Load a model with optional 4-bit quantization."""
    if model_spec.load_in_4bit and device != "cuda":
        raise RuntimeError("4-bit models require CUDA. Use a GPU-enabled setup.")

    local_only = os.getenv("HF_HUB_OFFLINE") == "1"
    model_id = _resolve_local_snapshot(model_spec.name) if local_only else model_spec.name

    quantization_config = None
    if model_spec.load_in_4bit:
        try:
            from transformers import BitsAndBytesConfig
        except ImportError as exc:
            raise ImportError(
                "bitsandbytes is required for 4-bit loading. "
                "Install it in your CUDA-enabled Python 3.11 env."
            ) from exc

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

    tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        local_files_only=local_only,
    )
    if device == "cuda" and model_spec.load_in_4bit:
        # Keep quantized weights fully on GPU to avoid CPU offload errors.
        device_map = {"": 0}
    else:
        device_map = "auto" if device == "cuda" else None
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        device_map=device_map,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        quantization_config=quantization_config,
        local_files_only=local_only,
    )
    if device_map is None:
        model.to(device)
    model.eval()
    return model, tokenizer


def run_model_experiment(
    model_spec: ModelSpec,
    device: str = "auto",
    checkpoint_fn=None,
) -> Dict:
    """Run full experiment for one model."""
    print(f"\n{'='*60}")
    print(f"Running experiment: {model_spec.name}")
    print(f"{'='*60}")

    # Load model
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"Loading model on {device}...")
    model, tokenizer = load_model(model_spec, device)

    # Calculate totals
    n_tasks = len(TASKS)
    n_tiers = len(FORMAT_TIERS)
    n_protocols = 2
    total_trials = n_tasks * n_tiers * n_protocols * N_TRIALS
    print(f"Total: {n_tasks} tasks x {n_tiers} tiers x {n_protocols} protocols x {N_TRIALS} trials = {total_trials} generations")

    results = []
    start_time = time.time()
    gen_count = 0

    for tier_name in FORMAT_TIERS.keys():
        for task in TASKS:
            for protocol in ["one_shot", "staged"]:
                for trial in range(N_TRIALS):
                    try:
                        result = run_trial(
                            model,
                            tokenizer,
                            task,
                            tier_name,
                            protocol,
                            trial,
                            model_spec.name,
                        )
                        results.append(asdict(result))
                    except Exception as e:
                        print(f"  ERROR: {task.task_id}/{tier_name}/{protocol}/t{trial}: {e}")
                        results.append({
                            "task_id": task.task_id,
                            "tier": tier_name,
                            "protocol": protocol,
                            "trial": trial,
                            "pass_a": False,
                            "pass_b": False,
                            "pass_both": False,
                            "msg_a": f"Error: {str(e)[:100]}",
                            "msg_b": "Skipped",
                            "code_extracted": None,
                            "response_length": 0,
                            "generated_tokens": 0,
                            "hit_max_tokens": False,
                        })

                    gen_count += 1
                    elapsed = time.time() - start_time
                    rate = gen_count / elapsed if elapsed > 0 else 0
                    eta = (total_trials - gen_count) / rate if rate > 0 else 0

                    # Progress indicator
                    status = "OK" if results[-1]["pass_both"] else ("A" if results[-1]["pass_a"] else "X")
                    print(f"  [{gen_count}/{total_trials}] {task.task_id}/{tier_name}/{protocol}/t{trial}: {status} | "
                          f"Rate: {rate:.2f}/s | ETA: {eta/60:.1f}m")
                    sys.stdout.flush()
                    if checkpoint_fn and (gen_count % CHECKPOINT_EVERY == 0):
                        checkpoint_fn(model_spec.name, results, start_time)
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

    # Cleanup
    del model
    del tokenizer
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    total_time = time.time() - start_time

    return {
        "model": model_spec.name,
        "device": device,
        "n_trials": N_TRIALS,
        "temperature": TEMPERATURE,
        "total_time_seconds": total_time,
        "results": results,
    }


def aggregate_results(model_results: Dict) -> Dict:
    """Aggregate results by tier and protocol."""
    from collections import defaultdict

    by_tier_protocol = defaultdict(lambda: {"pass_a": 0, "pass_b": 0, "pass_both": 0, "total": 0})

    for r in model_results["results"]:
        key = (r["tier"], r["protocol"])
        by_tier_protocol[key]["pass_a"] += int(r["pass_a"])
        by_tier_protocol[key]["pass_b"] += int(r["pass_b"])
        by_tier_protocol[key]["pass_both"] += int(r["pass_both"])
        by_tier_protocol[key]["total"] += 1

    summary = {}
    for (tier, protocol), counts in by_tier_protocol.items():
        if tier not in summary:
            summary[tier] = {}
        n = counts["total"]
        summary[tier][protocol] = {
            "pass_a": f"{counts['pass_a']/n*100:.0f}%",
            "pass_b": f"{counts['pass_b']/n*100:.0f}%",
            "pass_both": f"{counts['pass_both']/n*100:.0f}%",
            "n": n,
        }

    return summary


def summarize_failure_modes(results: List[Dict]) -> Dict:
    """Summarize failure modes by tier/protocol."""
    from collections import defaultdict

    summary = defaultdict(lambda: {"both": 0, "a_only": 0, "b_only": 0, "none": 0, "hit_max": 0})
    for r in results:
        key = (r["tier"], r["protocol"])
        pass_a = bool(r["pass_a"])
        pass_b = bool(r["pass_b"])
        if pass_a and pass_b:
            summary[key]["both"] += 1
        elif pass_a and not pass_b:
            summary[key]["a_only"] += 1
        elif pass_b and not pass_a:
            summary[key]["b_only"] += 1
        else:
            summary[key]["none"] += 1
        summary[key]["hit_max"] += int(bool(r.get("hit_max_tokens")))

    return summary


def print_summary(model_results: Dict):
    """Print formatted summary table."""
    summary = aggregate_results(model_results)

    print(f"\n{'='*70}")
    print(f"RESULTS: {model_results['model']}")
    print(f"Device: {model_results['device']} | Time: {model_results['total_time_seconds']/60:.1f}m | N={model_results['n_trials']}")
    print(f"{'='*70}")
    print(f"{'Tier':<12} {'Protocol':<10} {'Pass A':<10} {'Pass B':<10} {'Pass A^B':<10} {'Delta':<10}")
    print("-"*70)

    for tier in ["control", "low", "moderate", "high"]:
        if tier in summary:
            one = summary[tier].get("one_shot", {})
            stg = summary[tier].get("staged", {})

            one_both = float(one.get("pass_both", "0%").rstrip("%"))
            stg_both = float(stg.get("pass_both", "0%").rstrip("%"))
            delta = stg_both - one_both

            print(f"{tier:<12} {'one_shot':<10} {one.get('pass_a', 'N/A'):<10} {one.get('pass_b', 'N/A'):<10} {one.get('pass_both', 'N/A'):<10}")
            print(f"{'':<12} {'staged':<10} {stg.get('pass_a', 'N/A'):<10} {stg.get('pass_b', 'N/A'):<10} {stg.get('pass_both', 'N/A'):<10} {delta:+.0f}%")
            print("-"*70)

    failure_modes = summarize_failure_modes(model_results["results"])
    print("\nFailure modes (counts):")
    for tier in ["control", "low", "moderate", "high"]:
        for protocol in ["one_shot", "staged"]:
            key = (tier, protocol)
            if key not in failure_modes:
                continue
            counts = failure_modes[key]
            print(
                f"{tier:>8} {protocol:>8} both={counts['both']:>3} "
                f"a_only={counts['a_only']:>3} b_only={counts['b_only']:>3} "
                f"none={counts['none']:>3} hit_max={counts['hit_max']:>3}"
            )


def main():
    """Run the full experiment."""
    parser = argparse.ArgumentParser(description="Code constraint experiment")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODEL_NAMES),
        help=(
            "Comma-separated model names to run. Use 'all' to run every model "
            "in MODEL_SPECS."
        ),
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models and exit.",
    )
    args = parser.parse_args()

    if args.list_models:
        print("Available models:")
        for spec in MODEL_SPECS:
            suffix = " (4-bit)" if spec.load_in_4bit else ""
            print(f"  - {spec.name}{suffix}")
        return

    print("="*60)
    print("Code Constraint Experiment")
    print("Judge-free multi-constraint evaluation")
    print("="*60)

    if torch.cuda.is_available():
        print(f"\n[GPU] DETECTED: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
        device = "cuda"
    else:
        print("\nCPU mode (no GPU detected)")
        device = "cpu"

    if args.models.strip().lower() == "all":
        selected_names = [spec.name for spec in MODEL_SPECS]
    else:
        selected_names = [name.strip() for name in args.models.split(",") if name.strip()]

    spec_by_name = {spec.name: spec for spec in MODEL_SPECS}
    unknown = [name for name in selected_names if name not in spec_by_name]
    if unknown:
        raise ValueError(f"Unknown model(s): {', '.join(unknown)}")

    selected_specs = [spec_by_name[name] for name in selected_names]

    print(f"\nExperiment parameters:")
    print(f"  Tasks: {len(TASKS)}")
    print(f"  Tiers: {len(FORMAT_TIERS)}")
    print(f"  Protocols: 2 (one_shot, staged)")
    print(f"  Trials: {N_TRIALS}")
    print(f"  Models: {len(selected_specs)}")
    print(f"  Total generations per model: {len(TASKS) * len(FORMAT_TIERS) * 2 * N_TRIALS}")
    print("="*60)

    # Load existing results to append, not overwrite
    output_path = Path("code_constraint_results.json")
    if output_path.exists():
        try:
            with open(output_path, "r") as f:
                all_results = json.load(f)
            print(f"\n[Append mode] Loaded existing results: {[k for k in all_results if k != '_meta']}")
        except (json.JSONDecodeError, IOError):
            all_results = {}
            print("\n[Fresh start] Could not load existing results")
    else:
        all_results = {}
        print("\n[Fresh start] No existing results file")

    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        git_hash = "unknown"

    all_results["_meta"] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_hash": git_hash,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "torch_version": getattr(torch, "__version__", "unknown"),
        "transformers_version": getattr(sys.modules.get("transformers"), "__version__", "unknown"),
        "bitsandbytes_available": "bitsandbytes" in sys.modules,
        "args": vars(args),
        "max_new_tokens": MAX_NEW_TOKENS,
        "temperature": TEMPERATURE,
        "n_trials": N_TRIALS,
    }

    def checkpoint(model_name: str, results: List[Dict], start_time: float):
        all_results[model_name] = {
            "model": model_name,
            "device": device,
            "n_trials": N_TRIALS,
            "temperature": TEMPERATURE,
            "total_time_seconds": time.time() - start_time,
            "results": results,
        }
        output_path = Path("code_constraint_results.json")
        with open(output_path, "w") as f:
            json.dump(all_results, f, indent=2)

    for model_spec in selected_specs:
        model_results = run_model_experiment(
            model_spec,
            device=device,
            checkpoint_fn=checkpoint,
        )
        all_results[model_spec.name] = model_results

        # Print summary for this model
        print_summary(model_results)

        # Save intermediate results
        output_path = Path("code_constraint_results.json")
        with open(output_path, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults saved to: {output_path}")

    print("\n" + "="*60)
    print("EXPERIMENT COMPLETE")
    print("="*60)
    print(f"Results saved to: code_constraint_results.json")


if __name__ == "__main__":
    main()
