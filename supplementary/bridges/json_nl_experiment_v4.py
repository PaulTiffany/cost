#!/usr/bin/env python3
"""
JSON-NL Transfer Experiment v4 (Substantive Content)

Paper: "The Cost of Cacophony" (ICML 2026)
  - main.tex, references.bib

Verifies:
  - Appendix H (app:json_harness): JSON-NL domain transfer
  - Claim C24: 77.5% → 100% failure scaling with constraint tightness
  - Table A12 (tab:json_nl): JSON-NL tier pass rates

KEY INSIGHT: Previous versions used "form filling" tasks where JSON fields
were just slots for extracted values. These fit trivially in any char limit.

v4 FIX: Tasks require SUBSTANTIVE GENERATED CONTENT in JSON fields:
- Summaries, explanations, reasoning, analysis
- Content that naturally wants to be longer
- Format constraints actually bite

This creates real format vs content tension:
- Content: thorough, complete, detailed
- Format: brief, fits char limit

Design:
- 8 substantive tasks (summaries, explanations, analyses)
- 4 tiers with TIGHT char limits (200 → 80 chars)
- Content verified via keyword presence + length requirements
- Format verified via JSON parse + char limit
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import numpy as np

SEED_BASE = 42


@dataclass
class SubstantiveTask:
    """A task requiring substantive content in JSON format."""
    task_id: str
    prompt: str
    required_fields: List[str]

    # Content verification: keywords that MUST appear (substantive content check)
    required_keywords: List[str]

    # Minimum total content length (ensures substantive response)
    min_content_length: int = 50


# 8 substantive tasks - content naturally wants to be long
TASKS = [
    SubstantiveTask(
        task_id="explain_concept",
        prompt="Explain why the sky is blue in a JSON response with fields: explanation, key_terms, analogy",
        required_fields=["explanation", "key_terms", "analogy"],
        required_keywords=["light", "scatter"],  # Must mention core concepts
        min_content_length=80,
    ),
    SubstantiveTask(
        task_id="summarize_event",
        prompt="Summarize the moon landing in JSON with fields: summary, significance, key_figures",
        required_fields=["summary", "significance", "key_figures"],
        required_keywords=["Apollo", "1969", "Armstrong"],
        min_content_length=100,
    ),
    SubstantiveTask(
        task_id="analyze_problem",
        prompt="Analyze why traffic jams occur in JSON with fields: causes, effects, solutions",
        required_fields=["causes", "effects", "solutions"],
        required_keywords=["congestion", "commute"],
        min_content_length=80,
    ),
    SubstantiveTask(
        task_id="compare_contrast",
        prompt="Compare cats and dogs as pets in JSON with fields: similarities, differences, recommendation",
        required_fields=["similarities", "differences", "recommendation"],
        required_keywords=["pet", "care"],
        min_content_length=80,
    ),
    SubstantiveTask(
        task_id="give_advice",
        prompt="Give advice for learning a new language in JSON with fields: strategy, common_mistakes, timeline",
        required_fields=["strategy", "common_mistakes", "timeline"],
        required_keywords=["practice", "vocabulary"],
        min_content_length=80,
    ),
    SubstantiveTask(
        task_id="describe_process",
        prompt="Describe how photosynthesis works in JSON with fields: process, inputs, outputs",
        required_fields=["process", "inputs", "outputs"],
        required_keywords=["sunlight", "oxygen", "carbon"],
        min_content_length=80,
    ),
    SubstantiveTask(
        task_id="evaluate_argument",
        prompt="Evaluate the argument 'social media is harmful' in JSON with fields: pros, cons, verdict",
        required_fields=["pros", "cons", "verdict"],
        required_keywords=["mental", "connection"],
        min_content_length=80,
    ),
    SubstantiveTask(
        task_id="predict_outcome",
        prompt="Predict effects of rising sea levels in JSON with fields: short_term, long_term, most_affected",
        required_fields=["short_term", "long_term", "most_affected"],
        required_keywords=["coastal", "flood"],
        min_content_length=80,
    ),
]


@dataclass
class TierSpec:
    """Format constraint tier - TIGHT limits that actually bite."""
    tier_id: str
    max_chars: int  # Now actually constraining!

    computed_rho: Optional[float] = None


# TIGHT char limits - these actually constrain substantive content
TIERS = {
    "control": TierSpec(tier_id="control", max_chars=500),   # Comfortable
    "low": TierSpec(tier_id="low", max_chars=300),           # Starting to squeeze
    "moderate": TierSpec(tier_id="moderate", max_chars=180), # Tight
    "high": TierSpec(tier_id="high", max_chars=120),         # Very tight - must abbreviate
}


def compute_tier_rho(tier: TierSpec) -> float:
    """Compute ρ from char pressure. Tighter limit = higher ρ."""
    # Typical substantive response wants ~400 chars
    TYPICAL_LENGTH = 400

    # Pressure increases as limit decreases below typical
    pressure = max(0, 1 - (tier.max_chars / TYPICAL_LENGTH))

    return float(np.clip(pressure, 0.01, 0.95))


@dataclass
class VerificationResult:
    passed_format: bool
    passed_content: bool
    passed_both: bool
    format_errors: List[str]
    content_errors: List[str]


def verify_response(response: str, task: SubstantiveTask, tier: TierSpec) -> VerificationResult:
    """Verify response against format and content requirements."""
    format_errors = []
    content_errors = []

    # === FORMAT VERIFICATION ===

    # 1. Length check (this is the key constraint now)
    if len(response) > tier.max_chars:
        format_errors.append(f"length:{len(response)}>{tier.max_chars}")

    # 2. JSON parse
    try:
        data = json.loads(response)
    except json.JSONDecodeError as e:
        format_errors.append(f"json_parse:{str(e)[:30]}")
        return VerificationResult(False, False, False, format_errors, ["json_failed"])

    if not isinstance(data, dict):
        format_errors.append("not_object")
        return VerificationResult(False, False, False, format_errors, ["not_object"])

    # 3. Required fields
    for field in task.required_fields:
        if field not in data:
            format_errors.append(f"missing:{field}")

    # === CONTENT VERIFICATION ===

    response_lower = response.lower()

    # 1. Required keywords (substantive content check)
    for kw in task.required_keywords:
        if kw.lower() not in response_lower:
            content_errors.append(f"missing_keyword:{kw}")

    # 2. Minimum content length (ensures not just stubs)
    total_content = sum(len(str(v)) for v in data.values() if isinstance(v, (str, list)))
    if total_content < task.min_content_length:
        content_errors.append(f"too_short:{total_content}<{task.min_content_length}")

    passed_format = len(format_errors) == 0
    passed_content = len(content_errors) == 0

    return VerificationResult(
        passed_format=passed_format,
        passed_content=passed_content,
        passed_both=passed_format and passed_content,
        format_errors=format_errors,
        content_errors=content_errors,
    )


def build_prompt(task: SubstantiveTask, tier: TierSpec) -> str:
    """Build prompt emphasizing both content quality AND format constraint."""
    return f"""{task.prompt}

Requirements:
- Include ALL required fields: {task.required_fields}
- Your response must be substantive and informative
- STRICT LIMIT: Maximum {tier.max_chars} characters total (including JSON syntax)
- Return ONLY the JSON object, no other text

JSON output:"""


def build_staged_prompts(task: SubstantiveTask, tier: TierSpec) -> Tuple[str, str, str]:
    """3-step staged protocol."""

    stage1 = f"""{task.prompt}

First, think through what you want to say for each field:
{task.required_fields}

Draft your ideas (not final JSON yet):"""

    stage2 = f"""Now check your draft:
- Does it cover all required fields? {task.required_fields}
- Is it substantive and informative?
- Can it fit in {tier.max_chars} characters as JSON?

List any issues to fix:"""

    stage3 = f"""Now output the final JSON.
STRICT LIMIT: {tier.max_chars} characters maximum.
Fields: {task.required_fields}

Output ONLY the JSON:"""

    return stage1, stage2, stage3


def run_experiment(
    model_name: str,
    device: str,
    n_trials: int = 5,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the v4 experiment with substantive content tasks."""

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print("=" * 60)
    print("JSON-NL Experiment v4 (Substantive Content)")
    print("=" * 60)
    print(f"Model: {model_name}")
    print(f"Device: {device}")
    print(f"Tasks: {len(TASKS)}")
    print(f"Tiers: {len(TIERS)}")
    print(f"Trials: {n_trials}")
    print(f"Total generations: {len(TASKS) * len(TIERS) * 2 * n_trials}")
    print()
    print("KEY CHANGES in v4:")
    print("- Substantive content tasks (summaries, explanations, analyses)")
    print("- TIGHT char limits that actually constrain (500 -> 120)")
    print("- Content verified via keywords + min length")
    print()

    # Compute tier rho
    print("[0/4] Computing tier rho from char pressure...")
    tier_rhos = {}
    for tier_id, tier in TIERS.items():
        rho = compute_tier_rho(tier)
        tier.computed_rho = rho
        tier_rhos[tier_id] = rho
        print(f"  {tier_id}: max_chars={tier.max_chars}, rho={rho:.3f}")
    print()

    # Load model
    print("[1/4] Loading model...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map=device if device == "cuda" else None,
    )
    if device == "cpu":
        model = model.to(device)
    model.eval()

    def set_seed(seed: int):
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    def generate(prompt: str, max_tokens: int = 300, seed: Optional[int] = None) -> str:
        if seed is not None:
            set_seed(seed)

        messages = [{"role": "user", "content": prompt}]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=0.7,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id,
            )

        response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

        # Extract JSON
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if json_match:
            return json_match.group()
        return response.strip()

    # Run experiment
    print("[2/4] Running experiment...")
    print()

    total = len(TASKS) * len(TIERS) * 2 * n_trials
    print(f"  Total generations: {total}")
    print()

    results = []
    start_time = time.time()
    completed = 0
    gen_idx = 0

    for task in TASKS:
        for tier_id, tier in TIERS.items():
            for trial in range(n_trials):
                trial_seed = SEED_BASE + gen_idx

                # One-shot
                prompt = build_prompt(task, tier)
                response = generate(prompt, seed=trial_seed)
                verification = verify_response(response, task, tier)

                results.append({
                    "task_id": task.task_id,
                    "tier": tier_id,
                    "tier_rho": tier.computed_rho,
                    "protocol": "one_shot",
                    "trial": trial,
                    "pass_format": verification.passed_format,
                    "pass_content": verification.passed_content,
                    "pass_both": verification.passed_both,
                    "format_errors": verification.format_errors,
                    "content_errors": verification.content_errors,
                    "response_length": len(response),
                })
                completed += 1
                gen_idx += 1

                # Staged
                stage1, stage2, stage3 = build_staged_prompts(task, tier)
                staged_seed = SEED_BASE + gen_idx

                draft = generate(stage1, max_tokens=200, seed=staged_seed)
                critique = generate(stage2 + f"\n\nDraft:\n{draft}", max_tokens=150, seed=staged_seed+1000)
                response = generate(stage3 + f"\n\nDraft:\n{draft}\n\nIssues:\n{critique}", max_tokens=200, seed=staged_seed+2000)
                verification = verify_response(response, task, tier)

                results.append({
                    "task_id": task.task_id,
                    "tier": tier_id,
                    "tier_rho": tier.computed_rho,
                    "protocol": "staged",
                    "trial": trial,
                    "pass_format": verification.passed_format,
                    "pass_content": verification.passed_content,
                    "pass_both": verification.passed_both,
                    "format_errors": verification.format_errors,
                    "content_errors": verification.content_errors,
                    "response_length": len(response),
                })
                completed += 1
                gen_idx += 1

                # Progress
                if completed % 10 == 0:
                    elapsed = time.time() - start_time
                    pct = 100 * completed / total
                    eta = (elapsed / completed) * (total - completed) if completed > 0 else 0
                    print(f"  [{completed}/{total}] {pct:.0f}% | {elapsed/60:.1f}min elapsed | ETA {eta/60:.1f}min")

    elapsed_total = time.time() - start_time
    print(f"\n[3/4] Complete in {elapsed_total/60:.1f} minutes")

    # Summary
    summary = {}
    for tier_id in TIERS:
        tier_results = [r for r in results if r["tier"] == tier_id]
        one_shot = [r for r in tier_results if r["protocol"] == "one_shot"]
        staged = [r for r in tier_results if r["protocol"] == "staged"]

        os_pass = sum(1 for r in one_shot if r["pass_both"]) / len(one_shot) * 100 if one_shot else 0
        st_pass = sum(1 for r in staged if r["pass_both"]) / len(staged) * 100 if staged else 0

        # Also track format-only and content-only pass rates
        os_format = sum(1 for r in one_shot if r["pass_format"]) / len(one_shot) * 100 if one_shot else 0
        os_content = sum(1 for r in one_shot if r["pass_content"]) / len(one_shot) * 100 if one_shot else 0

        summary[tier_id] = {
            "rho": tier_rhos[tier_id],
            "one_shot_pass": os_pass,
            "staged_pass": st_pass,
            "one_shot_format": os_format,
            "one_shot_content": os_content,
            "failure_rate": 100 - max(os_pass, st_pass),
        }

    # Save
    print("\n[4/4] Saving results...")
    output_data = {
        "experiment": "json_nl_v4",
        "model": model_name,
        "timestamp": datetime.now().isoformat(),
        "tier_rhos": tier_rhos,
        "summary": summary,
        "data": results,
    }

    if output_path is None:
        output_path = Path("json_nl_v4_results.json")

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, default=str)

    print(f"[ok] Wrote {output_path}")

    # Print summary
    print("\n=== Summary ===")
    print(f"{'Tier':<10} | {'rho':>5} | {'chars':>5} | {'1-shot':>7} | {'Staged':>7} | {'Fmt%':>5} | {'Cnt%':>5} | {'Fail':>6}")
    print("-" * 75)
    for tier_id, stats in summary.items():
        tier = TIERS[tier_id]
        print(f"{tier_id:<10} | {stats['rho']:>5.2f} | {tier.max_chars:>5} | {stats['one_shot_pass']:>6.1f}% | {stats['staged_pass']:>6.1f}% | {stats['one_shot_format']:>4.0f}% | {stats['one_shot_content']:>4.0f}% | {stats['failure_rate']:>5.1f}%")

    return output_data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output", type=str, default=None)

    args = parser.parse_args()
    output_path = Path(args.output) if args.output else None
    run_experiment(args.model, args.device, args.trials, output_path)


if __name__ == "__main__":
    main()
