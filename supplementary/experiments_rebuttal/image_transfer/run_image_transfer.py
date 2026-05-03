#!/usr/bin/env python3
"""
run_image_transfer.py - Image-medium bound transfer experiment.

Paper: "The Cost of Cacophony: Geometric Limits on Multi-Constraint
Alignment" (NeurIPS 2026).

Tests whether the diagonal-cost bound proven for text-medium constrained
systems (Theorem 4.x, code/JSON/IF-DSL experiments) transfers to image
generation as a second medium. The reader-verifiable demonstration is
the resulting multi-panel figure: as k stacks, the staged image-gen
pipeline visibly degrades, exhibiting the predicted cliff.

Same target (one source LaTeX block from the paper body) is generated
across all cells; only the constraint stack varies.

Cells
-----
  S0     k=0 (empty prompt)            staged       baseline
  S1     k=1 (LaTeX block alone)       staged       minimum constraint
  S2..5  k=2..5 explicit               staged       NeurIPS constraints
                                                    decomposed into
                                                    individual inputs
  S-imp  k=implicit (NeurIPS bundle)   staged       same constraints
                                                    bundled into one
                                                    input -- tests
                                                    implicit-vs-explicit
                                                    decomposition
  R-exp  k=explicit                    self-refine  same constraints,
                                                    iteratively (k+1
                                                    image calls) --
                                                    tests staged vs
                                                    self-refine cost
                                                    (gemini point 2)

Output
------
  ./image_transfer_results.json   structured results
  ./outputs/{cell}_t{trial}.png   per-trial images (staged)
  ./outputs/{cell}_t{trial}_step{i}.png  per-step images (refine)

Usage
-----
  export OPENROUTER_API_KEY=...
  python run_image_transfer.py [--n-trials 3] [--dry-run]

Cost: ~28 image-gen calls at ~$0.005 each => ~$0.14.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: pip install openai", file=sys.stderr)
    sys.exit(2)


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent  # supplementary/experiments_rebuttal/image_transfer -> repo root
OUTPUTS_DIR = SCRIPT_DIR / "outputs"  # subdir per --run-id appended at runtime
RESULTS_JSON_TEMPLATE = SCRIPT_DIR / "image_transfer_results_{run_id}.json"

# Medium-frame: the explicit "produce an image" instruction. Without
# this, the model treats LaTeX-shaped content as a text query (Run A
# documented the failure pattern). With it, the modality is locked and
# we can isolate the effect of constraint stacking.
MEDIUM_FRAME = "Generate an image illustrating the following:\n\n"

MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
COMPLIANCE_DIR = REPO_ROOT / "supplementary" / "illustrations" / "compliance"
INDIVIDUAL_DIR = COMPLIANCE_DIR / "individual"
NEURIPS_BUNDLE = COMPLIANCE_DIR / "neurips2026_compliance.txt"


# ----------------------------------------------------------------------
# Experiment configuration
# ----------------------------------------------------------------------
# Same source LaTeX block across all non-S0 cells. Lines 349-350 of
# main.tex are the Constitutional AI / staging block that the existing
# staging_vs_refine illustration already targets.
TARGET_BLOCK = {
    "file": "paper/main.tex",
    "line_start": 349,
    "line_end": 350,
}

# Individual NeurIPS constraints (extracted, hashed, source-bound).
INDIVIDUAL_CONSTRAINTS = ["accessibility", "anonymity", "no_offensive", "ethics_scope"]

# 11 SOCIETAL IMPACT topics from the same handbook, each as its own file.
# Used by Run C (conflicting-constraint stack) to test the bound's floor.
# Order matters: cells C-oneshot-k take constraints[:k] for reproducibility.
SOCIETAL_IMPACT_CONSTRAINTS = [
    "safety", "security", "discrimination", "surveillance",
    "deception_harassment", "environment", "human_rights", "bias_fairness",
    "dual_use", "data_enrichment", "synthetic_media",
]

# Run D: image-medium FORMAT_TIERS analog. 1-for-1 replication of the
# code-constraint experiment structure (same task identities -- factorial,
# fibonacci, binary_search -- same tier names -- control/low/moderate/high
# -- just translated to image medium). Tier files at
# supplementary/illustrations/format_tiers/{tier}.txt and task files at
# supplementary/illustrations/image_tasks/{task}.txt. Each tier strictly
# extends the previous: compounding visual format demands sharing latent
# structure, the same shape of escalating-rho the code experiment uses.
RUN_D_TIERS = ["control", "low", "moderate", "high"]
RUN_D_TASKS = ["factorial", "fibonacci", "binary_search"]
FORMAT_TIERS_DIR = REPO_ROOT / "supplementary" / "illustrations" / "format_tiers"
IMAGE_TASKS_DIR = REPO_ROOT / "supplementary" / "illustrations" / "image_tasks"

CELLS = {
    "S0": {
        "k": 0,
        "protocol": "staged",
        "include_target": False,
        "constraints": [],
        "description": "Baseline: empty prompt, no constraints",
    },
    "S1": {
        "k": 1,
        "protocol": "staged",
        "include_target": True,
        "constraints": [],
        "description": "k=1: LaTeX source block alone",
    },
    "S2": {
        "k": 2,
        "protocol": "staged",
        "include_target": True,
        "constraints": ["accessibility"],
        "description": "k=2: + accessibility",
    },
    "S3": {
        "k": 3,
        "protocol": "staged",
        "include_target": True,
        "constraints": ["accessibility", "anonymity"],
        "description": "k=3: + anonymity",
    },
    "S4": {
        "k": 4,
        "protocol": "staged",
        "include_target": True,
        "constraints": ["accessibility", "anonymity", "no_offensive"],
        "description": "k=4: + no_offensive",
    },
    "S5": {
        "k": 5,
        "protocol": "staged",
        "include_target": True,
        "constraints": ["accessibility", "anonymity", "no_offensive", "ethics_scope"],
        "description": "k=5: + ethics_scope",
    },
    "S-imp": {
        "k": "implicit",
        "protocol": "staged",
        "include_target": True,
        "bundle": True,
        "description": "Implicit-k: NeurIPS bundle as single input (4 constraints compressed into 1 file)",
    },
    "R-exp": {
        "k": "explicit",
        "protocol": "self-refine",
        "include_target": True,
        "constraints": ["accessibility", "anonymity", "no_offensive", "ethics_scope"],
        "description": "Self-refine on same explicit constraint set (cost comparison vs S5)",
    },
}

# Run C cells: conflicting-constraint stack to test the bound's floor.
# Each cell uses the same source LaTeX block + medium frame; only the
# number of stacked SOCIETAL_IMPACT_CONSTRAINTS varies. The staged cell
# at k=11 demonstrates the staging cure: stage 1 generates the base
# image with the LaTeX task only; stage 2 takes that image as input
# AND the 11 ethics constraints, asking the model to refine the prior
# image to also respect them. If staging works as the bound predicts,
# stage 2 should "snap to coherence" -- preserve the base while
# absorbing the ethics layer.
for k in [1, 3, 5, 7, 9, 11]:
    CELLS[f"C-oneshot-{k}"] = {
        "k": k,
        "protocol": "staged",  # one-shot
        "include_target": True,
        "constraints_group": "societal_impact",
        "constraints": SOCIETAL_IMPACT_CONSTRAINTS[:k],
        "description": f"One-shot conflict stack: base task + first {k} ethics constraints",
    }
CELLS["C-staged-11"] = {
    "k": 11,
    "protocol": "image-staged",
    "include_target": True,
    "constraints_group": "societal_impact",
    "constraints": SOCIETAL_IMPACT_CONSTRAINTS[:11],
    "description": "Image-staged k=11 cure: stage 1 emits base image; stage 2 ingests stage-1 PNG as input AND adds all 11 ethics constraints in one call (the snap-to-coherence test)",
}

# Run D cells: 3 tasks x 4 tiers = 12 cells. Each cell is a single-shot
# image-gen with a task description (image_task) plus a tier spec
# (format_tier). The combination mirrors a single trial of the code
# experiment exactly, just in image medium.
for task in RUN_D_TASKS:
    for tier in RUN_D_TIERS:
        CELLS[f"D-{task}-{tier}"] = {
            "k": tier,  # tier name as the k axis (matches code experiment)
            "protocol": "staged",  # one-shot
            "include_target": False,  # no LaTeX block; uses task_file instead
            "task_file": f"image_tasks/{task}.txt",
            "tier_file": f"format_tiers/{tier}.txt",
            "constraints": [],  # tier replaces the constraints[] field for Run D cells
            "description": f"Run D ({task} task, {tier} tier): 1-for-1 replication of code experiment in image medium",
        }

DEFAULT_MODEL = "openai/gpt-5.4-image-2"
DEFAULT_N_TRIALS = 3


# ----------------------------------------------------------------------
# Hashing
# ----------------------------------------------------------------------
def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------
# Input loading
# ----------------------------------------------------------------------
def load_target_block() -> tuple[str, str]:
    """Return (block_text, block_hash) for TARGET_BLOCK."""
    src = REPO_ROOT / TARGET_BLOCK["file"]
    lines = src.read_text(encoding="utf-8").splitlines()
    block = "\n".join(lines[TARGET_BLOCK["line_start"] - 1: TARGET_BLOCK["line_end"]])
    return block, sha256_text(block)


def load_constraint(name: str) -> tuple[str, str, Path]:
    """Return (text, hash, path) for an individual constraint.
    Searches INDIVIDUAL_DIR first, then INDIVIDUAL_DIR/societal_impact/."""
    flat = INDIVIDUAL_DIR / f"{name}.txt"
    if flat.exists():
        p = flat
    else:
        p = INDIVIDUAL_DIR / "societal_impact" / f"{name}.txt"
        if not p.exists():
            raise FileNotFoundError(f"constraint not found: {name} (looked in {flat} and {p})")
    txt = p.read_text(encoding="utf-8")
    return txt, sha256_text(txt), p


def load_bundle() -> tuple[str, str, Path]:
    """Return (text, hash, path) for the NeurIPS compliance bundle."""
    txt = NEURIPS_BUNDLE.read_text(encoding="utf-8")
    return txt, sha256_text(txt), NEURIPS_BUNDLE


# ----------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------
def build_staged_prompt(cell_id: str, cell: dict, target_block: str | None,
                         constraint_texts: dict[str, str],
                         bundle_text: str | None,
                         use_medium_frame: bool) -> str:
    """Concatenate all inputs into a single image-gen prompt.

    No 'make a schematic' framing in the constraint payload -- we feed
    only what the cell declares so hidden constraints don't leak. The
    one optional prefix (controlled by --medium-frame) is the explicit
    'generate an image' instruction; documented as the medium-defining
    request, not a content constraint.
    """
    parts: list[str] = []
    # Run D path: task_file + tier_file (replaces include_target + constraints)
    if cell.get("task_file") and cell.get("tier_file"):
        task_path = REPO_ROOT / "supplementary" / "illustrations" / cell["task_file"]
        tier_path = REPO_ROOT / "supplementary" / "illustrations" / cell["tier_file"]
        parts.append(f"Task:\n{task_path.read_text(encoding='utf-8')}")
        parts.append(f"Format requirements:\n{tier_path.read_text(encoding='utf-8')}")
    else:
        if cell.get("include_target") and target_block is not None:
            parts.append(f"Source LaTeX block:\n{target_block}")
        if cell.get("bundle"):
            if bundle_text is None:
                raise RuntimeError("bundle cell requested but bundle_text not loaded")
            parts.append(f"NeurIPS compliance bundle:\n{bundle_text}")
        for name in cell.get("constraints", []):
            parts.append(f"Constraint ({name}):\n{constraint_texts[name]}")
    body = "\n\n".join(parts) if parts else ""  # S0 emits empty string
    if not body:
        return body  # k=0 stays truly empty regardless of medium frame
    return (MEDIUM_FRAME + body) if use_medium_frame else body


def build_refine_prompts(cell: dict, target_block: str,
                          constraint_texts: dict[str, str],
                          use_medium_frame: bool) -> list[str]:
    """Build the sequence of prompts for self-refine.

    Step 0: target alone.
    Step i (i=1..k): cumulative prompt with constraints[0..i-1] added,
    framed as 'now also satisfy this constraint while keeping the prior
    intent'. Each step is a separate image-gen call.
    """
    constraints = cell["constraints"]
    prompts: list[str] = []
    base = f"Source LaTeX block:\n{target_block}"
    prompts.append((MEDIUM_FRAME + base) if use_medium_frame else base)
    cumulative = base
    for i, name in enumerate(constraints):
        cumulative = (cumulative + f"\n\nNow also satisfy this additional "
                       f"constraint while keeping the prior intent.\n"
                       f"Constraint ({name}):\n{constraint_texts[name]}")
        prompts.append((MEDIUM_FRAME + cumulative) if use_medium_frame else cumulative)
    return prompts


# ----------------------------------------------------------------------
# Image-gen API call
# ----------------------------------------------------------------------
def call_image_model(client: OpenAI, model: str, prompt: str,
                      input_image_bytes: bytes | None = None) -> tuple[bytes | None, str]:
    """Single image-gen call. Returns (image_bytes, raw_response_text).

    If input_image_bytes is provided, the message uses the multipart
    content format with both text and an image_url (data URL); this is
    the multimodal input path used by image-staged stage 2.

    Wrapped in try/except so a transient API JSON-decode (or any other)
    failure returns (None, error_text) rather than crashing the harness.
    """
    actual_text = prompt if prompt else " "
    try:
        if input_image_bytes is not None:
            data_url = f"data:image/png;base64,{base64.b64encode(input_image_bytes).decode('ascii')}"
            msg_content = [
                {"type": "text", "text": actual_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        else:
            msg_content = actual_text  # type: ignore[assignment]
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": msg_content}],
            modalities=["image", "text"],
        )
    except Exception as exc:
        return None, f"<API error: {type(exc).__name__}: {str(exc)[:300]}>"
    msg = resp.choices[0].message
    content = msg.content or ""
    images = getattr(msg, "images", None)
    if images:
        first = images[0]
        url = first.get("image_url", {}).get("url") if isinstance(first, dict) else None
        if url and url.startswith("data:image"):
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64), str(content)
    if "data:image" in str(content):
        idx = str(content).find("data:image")
        chunk = str(content)[idx:]
        end = chunk.find('"')
        if end < 0:
            end = chunk.find("\n")
        url = chunk[:end] if end > 0 else chunk
        if "," in url:
            _, b64 = url.split(",", 1)
            try:
                return base64.b64decode(b64), str(content)
            except Exception:
                pass
    return None, str(content)


# ----------------------------------------------------------------------
# Trial execution
# ----------------------------------------------------------------------
@dataclass
class StepResult:
    step: int
    prompt_hash: str
    prompt_chars: int
    image_path: str | None
    image_hash: str | None
    image_bytes: int
    success: bool
    error: str = ""


@dataclass
class TrialResult:
    cell_id: str
    trial: int
    protocol: str
    k: Any
    n_calls: int
    steps: list[StepResult]
    final_image_path: str | None
    final_image_hash: str | None


def run_trial(client: OpenAI, model: str, cell_id: str, trial: int,
              cell: dict, target_block: str | None,
              constraint_texts: dict[str, str], bundle_text: str | None,
              outputs_dir: Path, use_medium_frame: bool,
              dry_run: bool = False) -> TrialResult:
    """Execute one trial of one cell."""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    steps: list[StepResult] = []

    if cell["protocol"] == "staged":
        prompt = build_staged_prompt(cell_id, cell, target_block, constraint_texts, bundle_text, use_medium_frame)
        out_path = outputs_dir / f"{cell_id}_t{trial}.png"
        if dry_run:
            steps.append(StepResult(
                step=0, prompt_hash=sha256_text(prompt), prompt_chars=len(prompt),
                image_path=None, image_hash=None, image_bytes=0, success=True,
                error="dry-run",
            ))
        else:
            t0 = time.time()
            img_bytes, raw = call_image_model(client, model, prompt)
            elapsed = time.time() - t0
            if img_bytes:
                out_path.write_bytes(img_bytes)
                steps.append(StepResult(
                    step=0, prompt_hash=sha256_text(prompt), prompt_chars=len(prompt),
                    image_path=str(out_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    image_hash=sha256_bytes(img_bytes), image_bytes=len(img_bytes),
                    success=True,
                ))
                print(f"  [{cell_id} t{trial}] OK  {len(img_bytes):,}B in {elapsed:.1f}s -> {out_path.name}")
            else:
                steps.append(StepResult(
                    step=0, prompt_hash=sha256_text(prompt), prompt_chars=len(prompt),
                    image_path=None, image_hash=None, image_bytes=0,
                    success=False, error=raw[:300],
                ))
                print(f"  [{cell_id} t{trial}] FAIL no image returned (in {elapsed:.1f}s)")
        n_calls = 1

    elif cell["protocol"] == "image-staged":
        # Stage 1: base task only (LaTeX block + medium frame).
        # Stage 2: stage-1 PNG as input + all constraints, asking the
        # model to refine the prior image to also respect them.
        constraints = cell.get("constraints", [])
        n_calls = 2
        # ---- Stage 1
        stage1_text = (MEDIUM_FRAME if use_medium_frame else "") + f"Source LaTeX block:\n{target_block}"
        out1 = outputs_dir / f"{cell_id}_t{trial}_stage1.png"
        if dry_run:
            steps.append(StepResult(
                step=0, prompt_hash=sha256_text(stage1_text), prompt_chars=len(stage1_text),
                image_path=None, image_hash=None, image_bytes=0, success=True, error="dry-run",
            ))
            stage1_bytes = None
        else:
            t0 = time.time()
            stage1_bytes, raw1 = call_image_model(client, model, stage1_text)
            elapsed = time.time() - t0
            if stage1_bytes:
                out1.write_bytes(stage1_bytes)
                steps.append(StepResult(
                    step=0, prompt_hash=sha256_text(stage1_text), prompt_chars=len(stage1_text),
                    image_path=str(out1.relative_to(REPO_ROOT)).replace("\\", "/"),
                    image_hash=sha256_bytes(stage1_bytes), image_bytes=len(stage1_bytes),
                    success=True,
                ))
                print(f"  [{cell_id} t{trial} stage1] OK  {len(stage1_bytes):,}B in {elapsed:.1f}s")
            else:
                steps.append(StepResult(
                    step=0, prompt_hash=sha256_text(stage1_text), prompt_chars=len(stage1_text),
                    image_path=None, image_hash=None, image_bytes=0,
                    success=False, error=raw1[:300],
                ))
                print(f"  [{cell_id} t{trial} stage1] FAIL")
        # ---- Stage 2
        constraint_block = "\n\n".join(
            f"Constraint ({name}):\n{constraint_texts[name]}" for name in constraints
        )
        stage2_text = (
            (MEDIUM_FRAME if use_medium_frame else "")
            + "Refine the previously generated image (provided) so it ALSO satisfies "
            + f"the following {len(constraints)} additional constraints, while preserving the original intent of the base image.\n\n"
            + f"Source LaTeX block (original task):\n{target_block}\n\n"
            + constraint_block
        )
        out2 = outputs_dir / f"{cell_id}_t{trial}_stage2.png"
        if dry_run:
            steps.append(StepResult(
                step=1, prompt_hash=sha256_text(stage2_text), prompt_chars=len(stage2_text),
                image_path=None, image_hash=None, image_bytes=0, success=True, error="dry-run",
            ))
        elif stage1_bytes is None:
            steps.append(StepResult(
                step=1, prompt_hash=sha256_text(stage2_text), prompt_chars=len(stage2_text),
                image_path=None, image_hash=None, image_bytes=0,
                success=False, error="stage 1 failed; cannot pass image input to stage 2",
            ))
            print(f"  [{cell_id} t{trial} stage2] SKIP (stage 1 failed)")
        else:
            t0 = time.time()
            stage2_bytes, raw2 = call_image_model(client, model, stage2_text, input_image_bytes=stage1_bytes)
            elapsed = time.time() - t0
            if stage2_bytes:
                out2.write_bytes(stage2_bytes)
                steps.append(StepResult(
                    step=1, prompt_hash=sha256_text(stage2_text), prompt_chars=len(stage2_text),
                    image_path=str(out2.relative_to(REPO_ROOT)).replace("\\", "/"),
                    image_hash=sha256_bytes(stage2_bytes), image_bytes=len(stage2_bytes),
                    success=True,
                ))
                print(f"  [{cell_id} t{trial} stage2] OK  {len(stage2_bytes):,}B in {elapsed:.1f}s (with stage-1 image input)")
            else:
                steps.append(StepResult(
                    step=1, prompt_hash=sha256_text(stage2_text), prompt_chars=len(stage2_text),
                    image_path=None, image_hash=None, image_bytes=0,
                    success=False, error=raw2[:300],
                ))
                print(f"  [{cell_id} t{trial} stage2] FAIL")

    elif cell["protocol"] == "self-refine":
        prompts = build_refine_prompts(cell, target_block, constraint_texts, use_medium_frame)  # type: ignore[arg-type]
        n_calls = len(prompts)
        for i, p in enumerate(prompts):
            out_path = outputs_dir / f"{cell_id}_t{trial}_step{i}.png"
            if dry_run:
                steps.append(StepResult(
                    step=i, prompt_hash=sha256_text(p), prompt_chars=len(p),
                    image_path=None, image_hash=None, image_bytes=0, success=True,
                    error="dry-run",
                ))
                continue
            t0 = time.time()
            img_bytes, raw = call_image_model(client, model, p)
            elapsed = time.time() - t0
            if img_bytes:
                out_path.write_bytes(img_bytes)
                steps.append(StepResult(
                    step=i, prompt_hash=sha256_text(p), prompt_chars=len(p),
                    image_path=str(out_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                    image_hash=sha256_bytes(img_bytes), image_bytes=len(img_bytes),
                    success=True,
                ))
                print(f"  [{cell_id} t{trial} step{i}] OK  {len(img_bytes):,}B in {elapsed:.1f}s")
            else:
                steps.append(StepResult(
                    step=i, prompt_hash=sha256_text(p), prompt_chars=len(p),
                    image_path=None, image_hash=None, image_bytes=0,
                    success=False, error=raw[:300],
                ))
                print(f"  [{cell_id} t{trial} step{i}] FAIL")
    else:
        raise ValueError(f"unknown protocol: {cell['protocol']}")

    final = next((s for s in reversed(steps) if s.success and s.image_path), None)
    return TrialResult(
        cell_id=cell_id, trial=trial, protocol=cell["protocol"], k=cell["k"],
        n_calls=n_calls, steps=steps,
        final_image_path=final.image_path if final else None,
        final_image_hash=final.image_hash if final else None,
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                       formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--n-trials", type=int, default=DEFAULT_N_TRIALS)
    parser.add_argument("--cells", default="all",
                        help="Comma-separated cell IDs to run, or 'all'")
    parser.add_argument("--run-id", default="runB",
                        help="Run identifier; outputs go to outputs/{run-id}/, results to image_transfer_results_{run-id}.json")
    parser.add_argument("--medium-frame", action="store_true", default=True,
                        help="Prepend MEDIUM_FRAME ('Generate an image illustrating...') to non-empty prompts")
    parser.add_argument("--no-medium-frame", dest="medium_frame", action="store_false",
                        help="Disable medium frame (Run A baseline)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build prompts and write JSON without calling the API")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Number of trials to run in parallel via ThreadPoolExecutor (default 1 = serial)")
    args = parser.parse_args()
    outputs_dir = OUTPUTS_DIR / args.run_id
    results_json = Path(str(RESULTS_JSON_TEMPLATE).format(run_id=args.run_id))

    if not args.dry_run:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            print("ERROR: set OPENROUTER_API_KEY (or use --dry-run)", file=sys.stderr)
            return 2
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    else:
        client = None  # type: ignore[assignment]

    # Load all inputs once -- merge Run B (INDIVIDUAL_CONSTRAINTS) and
    # Run C (SOCIETAL_IMPACT_CONSTRAINTS).
    target_block, target_hash = load_target_block()
    constraint_texts: dict[str, str] = {}
    constraint_hashes: dict[str, str] = {}
    for name in (*INDIVIDUAL_CONSTRAINTS, *SOCIETAL_IMPACT_CONSTRAINTS):
        txt, h, _p = load_constraint(name)
        constraint_texts[name] = txt
        constraint_hashes[name] = h
    bundle_text, bundle_hash, _ = load_bundle()

    selected = list(CELLS.keys()) if args.cells == "all" else args.cells.split(",")
    selected = [c.strip() for c in selected if c.strip()]
    for c in selected:
        if c not in CELLS:
            print(f"ERROR: unknown cell '{c}'. Known: {list(CELLS)}", file=sys.stderr)
            return 2

    print("=" * 70)
    print("IMAGE-MEDIUM BOUND TRANSFER EXPERIMENT")
    print("=" * 70)
    print(f"  Run ID:      {args.run_id}")
    print(f"  Model:       {args.model}")
    print(f"  N trials:    {args.n_trials}")
    print(f"  Cells:       {selected}")
    print(f"  Target hash: {target_hash[:16]}...")
    print(f"  Medium frame: {args.medium_frame} ('{MEDIUM_FRAME.strip()}')")
    print(f"  Outputs:     {outputs_dir.relative_to(REPO_ROOT)}")
    print(f"  Results:     {results_json.relative_to(REPO_ROOT)}")
    print(f"  Dry run:     {args.dry_run}")
    print()

    # Build the full work queue (cell, trial) pairs across all selected cells.
    work_items: list[tuple[str, int]] = []
    for cell_id in selected:
        for t in range(args.n_trials):
            work_items.append((cell_id, t))

    print(f"  Concurrency: {args.concurrency} (serial if 1)")
    print()

    def _run_one(item: tuple[str, int]) -> TrialResult:
        cid, tnum = item
        c = CELLS[cid]
        return run_trial(client, args.model, cid, tnum, c, target_block,
                          constraint_texts, bundle_text,
                          outputs_dir=outputs_dir, use_medium_frame=args.medium_frame,
                          dry_run=args.dry_run)

    all_trials: list[TrialResult] = []
    if args.concurrency <= 1:
        # Serial path -- preserves original cell-by-cell printing
        cur_cell = None
        for item in work_items:
            cid, _ = item
            if cid != cur_cell:
                cur_cell = cid
                print(f"--- Cell {cid} ({CELLS[cid]['description']}) ---")
            all_trials.append(_run_one(item))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"--- Pool: {len(work_items)} trials across {len(selected)} cells, concurrency={args.concurrency} ---")
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(_run_one, item): item for item in work_items}
            for fut in as_completed(futures):
                all_trials.append(fut.result())
        # Sort for deterministic JSON ordering
        cell_order = {cid: i for i, cid in enumerate(selected)}
        all_trials.sort(key=lambda tr: (cell_order.get(tr.cell_id, 999), tr.trial))

    # Aggregate
    cells_summary: dict[str, dict] = {}
    for cell_id in selected:
        cell = CELLS[cell_id]
        ts = [t for t in all_trials if t.cell_id == cell_id]
        successes = sum(1 for t in ts if t.final_image_path is not None)
        total_calls = sum(t.n_calls for t in ts)
        cells_summary[cell_id] = {
            "k": cell["k"],
            "protocol": cell["protocol"],
            "description": cell["description"],
            "n_trials": len(ts),
            "n_successes": successes,
            "calls_per_trial": ts[0].n_calls if ts else 0,
            "total_calls": total_calls,
        }

    payload = {
        "schema_version": 2,
        "run_id": args.run_id,
        "medium_frame": MEDIUM_FRAME if args.medium_frame else None,
        "medium_frame_source": "OpenAI image-gen prompting cookbook (no mandatory prefix; this is a documented common-convention 'declare modality' instruction). Cookbook: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide",
        "model": args.model,
        "n_trials_per_cell": args.n_trials,
        "target_block": {**TARGET_BLOCK, "hash": target_hash},
        "constraint_hashes": constraint_hashes,
        "bundle_hash": bundle_hash,
        "neurips_source_hash": "a202a0afe6901e0863364460977c037093a4497bdee50ef3b10da8e1f1213450",
        "neurips_source_file": "docs/neurips_official/MainTrackHandbook.txt",
        "neurips_source_version": "V202611",
        "cells": cells_summary,
        "trials": [
            {**asdict(t), "steps": [asdict(s) for s in t.steps]}
            for t in all_trials
        ],
    }
    results_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print()
    print("=" * 70)
    print(f"Wrote {results_json.name}")
    for cid, s in cells_summary.items():
        print(f"  {cid:6s} k={str(s['k']):8s} {s['protocol']:11s} {s['n_successes']}/{s['n_trials']} success, {s['total_calls']} total calls")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
