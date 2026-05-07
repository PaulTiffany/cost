#!/usr/bin/env python3
"""
claude_trajectory_pivot_experiment.py (v4)

Emits typed ObservationPackets matching pre-reg
`supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md` §3 EXACTLY.

This script's sole job is to PRODUCE PACKETS. It does NOT classify,
compute L_hat, or decide smooth/pivot. The deterministic audit observer
(separate process at ci/audit/) ingests the JSONL and performs
classification with hash-pinned predicate logic.

Differences from v1 (which was buggy):
  * temperature = 1.0 (real sampling variance) for all models EXCEPT
    claude-opus-4-7 (rejects the temperature parameter -> use API default)
  * No circular L_hat self-calibration in this script (deferred to audit)
  * Emits one ObservationPacket per trial (JSONL), not aggregated JSON
  * Hash chain: pre_reg_hash, verifier_hash, manifest_hash on every packet
  * Random ordering with pinned seed to break time-of-day clustering
  * Retry with exponential backoff; failures recorded as `error`, not dropped

Sample design (locked, pre-reg §5):
  3 Claude models x 6 tasks x 4 tiers x 10 trials = 720 trials
  --dry-run reduces trials-per-cell to 2 (~120 trials).

Usage:
  python supplementary/experiments/claude_trajectory_pivot_experiment.py --dry-run
  python supplementary/experiments/claude_trajectory_pivot_experiment.py
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Ensure local imports resolve (sibling experiment modules)
SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
sys.path.insert(0, str(SCRIPT_DIR))

from code_constraint_tasks import TASKS, CodeTask  # noqa: E402
from code_constraint_verifier import (  # noqa: E402
    FORMAT_TIERS,
    extract_code_block,
    verify_both,
    format_rules_to_prompt,
)


# ---------------------------------------------------------------------------
# Locked design constants (pre-reg §5)
# ---------------------------------------------------------------------------

PACKET_SCHEMA_VERSION = "v4.0"
OBSERVER_ID = "B_claude"

MODELS: List[str] = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
]

SELECTED_TASK_IDS: List[str] = [
    "factorial",
    "fibonacci",
    "is_palindrome",
    "gcd",
    "reverse_words",
    "fizzbuzz",
]

TIERS: List[str] = ["control", "low", "moderate", "high"]

DEFAULT_TRIALS_PER_CELL = 10
DRY_RUN_TRIALS_PER_CELL = 2
MAX_TOKENS = 1024
DEFAULT_CONCURRENCY = 8
ORDER_SEED = 20260504

ENCODER_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DEVICE = "cpu"

import os as _os
ENV_FILE = Path(_os.environ["AUDIT_ANTHROPIC_ENV_FILE"]) if _os.environ.get("AUDIT_ANTHROPIC_ENV_FILE") else None
PRE_REG_PATH = SCRIPT_PATH.parents[1] / "PRE_REGISTRATION_AUDIT_OBSERVER_v4.md"
VERIFIER_PATH = SCRIPT_DIR / "code_constraint_verifier.py"
# audit observer substrate (use ci/audit/observer.py if dedicated audit_observer.py absent)
REPO_ROOT = SCRIPT_PATH.parents[2]
AUDIT_OBSERVER_PATH_PRIMARY = REPO_ROOT / "ci" / "audit" / "audit_observer.py"
AUDIT_OBSERVER_PATH_FALLBACK = REPO_ROOT / "ci" / "audit" / "observer.py"

OUTPUT_DIR = SCRIPT_DIR / "outputs" / "audit_v4"
OUTPUT_FILE = OUTPUT_DIR / "claude_observation_packets.jsonl"

# Indicative cost per 1M tokens (rough, USD); used only for the running estimate.
PRICING_PER_MTOK = {
    "claude-haiku-4-5":  {"in": 1.00,  "out": 5.00},
    "claude-sonnet-4-6": {"in": 3.00,  "out": 15.00},
    "claude-opus-4-7":   {"in": 15.00, "out": 75.00},
}


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_manifest_hash() -> str:
    """sha256 of pre_reg + this script + audit observer substrate."""
    h = hashlib.sha256()
    for p in (PRE_REG_PATH, SCRIPT_PATH):
        h.update(p.read_bytes())
    if AUDIT_OBSERVER_PATH_PRIMARY.exists():
        h.update(AUDIT_OBSERVER_PATH_PRIMARY.read_bytes())
    elif AUDIT_OBSERVER_PATH_FALLBACK.exists():
        h.update(AUDIT_OBSERVER_PATH_FALLBACK.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Env / API key
# ---------------------------------------------------------------------------

def load_anthropic_key() -> str:
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Env file not found: {ENV_FILE}")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError(f"ANTHROPIC_API_KEY not found in {ENV_FILE}")


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

class TrajectoryEncoder:
    """Wraps MiniLM-L6-v2 for streaming-text embedding."""

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(ENCODER_ID, device=EMBED_DEVICE)

    def embed(self, text: str):
        import numpy as np
        if not text.strip():
            return np.zeros(384, dtype=float)
        v = self.model.encode(
            [text], show_progress_bar=False, normalize_embeddings=False
        )[0]
        return v


def cosine(a, b) -> float:
    import numpy as np
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-12 or nb < 1e-12:
        return 1.0
    return float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def build_prompt(task: CodeTask, tier_name: str) -> str:
    rules = FORMAT_TIERS[tier_name]
    rules_text = format_rules_to_prompt(rules)
    return (
        f"{task.description}\n\n"
        f"Constraints:\n{rules_text}\n\n"
        f"Return only the function in a single ```python ... ``` code block."
    )


# ---------------------------------------------------------------------------
# Trial execution
# ---------------------------------------------------------------------------

@dataclass
class _Cell:
    model: str
    task: CodeTask
    tier: str
    trial_idx: int


def _retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    keywords = ("ratelimit", "timeout", "overloaded", "apiconnection",
                "503", "502", "504", "429", "transient")
    return any(k in name or k in msg for k in keywords)


async def run_single_trial(
    client,
    encoder: TrajectoryEncoder,
    cell: _Cell,
    sem: asyncio.Semaphore,
    pre_reg_hash: str,
    verifier_hash: str,
    manifest_hash: str,
    encoder_pkg_version: str,
) -> dict:
    """Execute one trial and return a serialized ObservationPacket dict.

    Retries up to 3x with exponential backoff on transient API errors.
    On terminal failure, packet still emitted with `error` populated.
    """
    import numpy as np

    prompt_text = build_prompt(cell.task, cell.tier)
    prompt_hash = sha256_str(prompt_text)

    # Build kwargs once. opus-4-7 rejects temperature -> omit.
    kwargs = dict(
        model=cell.model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt_text}],
    )
    if "opus-4-7" not in cell.model:
        kwargs["temperature"] = 1.0

    chunk_trace: List[dict] = []
    running_text = ""
    api_model_snapshot = cell.model
    error_msg: Optional[str] = None

    async with sem:
        backoff = 2.0
        for attempt in range(3):
            chunk_trace = []
            running_text = ""
            initial_emb = None
            prior_emb = None
            try:
                async with client.messages.stream(**kwargs) as stream:
                    chunk_idx = 0
                    async for event in stream:
                        if (event.type == "content_block_delta"
                                and hasattr(event.delta, "text")):
                            text_appended = event.delta.text
                            if not text_appended:
                                continue
                            running_text += text_appended
                            emb = encoder.embed(running_text)

                            if initial_emb is None and float(np.linalg.norm(emb)) > 1e-6:
                                initial_emb = emb.copy()

                            if prior_emb is None:
                                displacement = 0.0
                            else:
                                displacement = float(np.linalg.norm(emb - prior_emb))

                            if initial_emb is None:
                                drift_deg = 0.0
                            else:
                                cs = cosine(initial_emb, emb)
                                drift_deg = math.degrees(math.acos(max(-1.0, min(1.0, cs))))

                            chunk_trace.append({
                                "chunk_idx": chunk_idx,
                                "text_appended": text_appended,
                                "displacement": displacement,
                                "drift_deg": drift_deg,
                                "cumulative_chars": len(running_text),
                            })
                            prior_emb = emb
                            chunk_idx += 1
                    # capture model snapshot if the SDK exposes it
                    final_msg = await stream.get_final_message()
                    api_model_snapshot = getattr(final_msg, "model", cell.model) or cell.model
                error_msg = None
                break
            except Exception as e:  # noqa: BLE001
                error_msg = f"{type(e).__name__}: {e}"
                if attempt < 2 and _retryable(e):
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                break

    output_text = running_text
    output_hash = sha256_str(output_text)

    # Verifier
    if error_msg is None:
        try:
            code = extract_code_block(output_text) or ""
            verifier_result = verify_both(code, cell.task.test_code, FORMAT_TIERS[cell.tier])
        except Exception as ve:  # noqa: BLE001
            verifier_result = {
                "pass_a": False, "pass_b": False, "pass_both": False,
                "msg_a": f"verifier_error: {type(ve).__name__}: {ve}",
                "msg_b": "",
            }
            error_msg = f"verifier_error: {type(ve).__name__}: {ve}"
    else:
        verifier_result = {
            "pass_a": False, "pass_b": False, "pass_both": False,
            "msg_a": "skipped: api_error", "msg_b": "skipped: api_error",
        }

    displacements = [c["displacement"] for c in chunk_trace if c["displacement"] > 0]
    drifts = [c["drift_deg"] for c in chunk_trace]

    packet = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "observer_id": OBSERVER_ID,
        "model_id": cell.model,
        "api_model_snapshot": api_model_snapshot,
        "task_id": cell.task.task_id,
        "tier": cell.tier,
        "trial_idx": cell.trial_idx,
        "prompt_text": prompt_text,
        "prompt_hash": prompt_hash,
        "output_text": output_text,
        "output_hash": output_hash,
        "chunk_trace": chunk_trace,
        "n_chunks": len(chunk_trace),
        "max_chunk_displacement": float(max(displacements)) if displacements else 0.0,
        "mean_chunk_displacement": (float(sum(displacements) / len(displacements))
                                    if displacements else 0.0),
        "max_drift_deg": float(max(drifts)) if drifts else 0.0,
        "verifier_result": verifier_result,
        "verifier_hash": verifier_hash,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "encoder_id": ENCODER_ID,
        "encoder_package_version": encoder_pkg_version,
        "pre_reg_hash": pre_reg_hash,
        "manifest_hash": manifest_hash,
        "error": error_msg,
    }
    return packet


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def _approx_token_count(s: str) -> int:
    # Cheap heuristic: 1 token ~ 4 chars. Used only for running cost estimate.
    return max(1, len(s) // 4)


def _estimate_cost(packet: dict) -> tuple[int, int, float]:
    pricing = PRICING_PER_MTOK.get(packet["model_id"])
    if not pricing:
        return 0, 0, 0.0
    in_tok = _approx_token_count(packet["prompt_text"])
    out_tok = _approx_token_count(packet["output_text"])
    cost = (in_tok * pricing["in"] + out_tok * pricing["out"]) / 1_000_000.0
    return in_tok, out_tok, cost


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

async def run_all(trials_per_cell: int, concurrency: int, output_path: Path) -> int:
    from anthropic import AsyncAnthropic
    import sentence_transformers

    api_key = load_anthropic_key()
    client = AsyncAnthropic(api_key=api_key)

    print("Loading MiniLM encoder ...", flush=True)
    encoder = TrajectoryEncoder()
    encoder_pkg_version = sentence_transformers.__version__

    pre_reg_hash = sha256_file(PRE_REG_PATH)
    verifier_hash = sha256_file(VERIFIER_PATH)
    manifest_hash = compute_manifest_hash()
    print(f"  pre_reg_hash:  {pre_reg_hash[:16]}...", flush=True)
    print(f"  verifier_hash: {verifier_hash[:16]}...", flush=True)
    print(f"  manifest_hash: {manifest_hash[:16]}...", flush=True)

    tasks_by_id = {t.task_id: t for t in TASKS}
    selected = [tasks_by_id[tid] for tid in SELECTED_TASK_IDS if tid in tasks_by_id]
    missing = set(SELECTED_TASK_IDS) - {t.task_id for t in selected}
    if missing:
        raise RuntimeError(f"Missing required tasks in code_constraint_tasks: {missing}")

    cells: List[_Cell] = []
    for model in MODELS:
        for task in selected:
            for tier in TIERS:
                for trial_idx in range(trials_per_cell):
                    cells.append(_Cell(model=model, task=task, tier=tier, trial_idx=trial_idx))

    rng = random.Random(ORDER_SEED)
    rng.shuffle(cells)
    print(f"Launching {len(cells)} trials at concurrency={concurrency} "
          f"(seed={ORDER_SEED}) ...", flush=True)

    sem = asyncio.Semaphore(concurrency)

    coros = [
        run_single_trial(
            client, encoder, cell, sem,
            pre_reg_hash=pre_reg_hash,
            verifier_hash=verifier_hash,
            manifest_hash=manifest_hash,
            encoder_pkg_version=encoder_pkg_version,
        )
        for cell in cells
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    errors = 0
    total_in_tok = 0
    total_out_tok = 0
    total_cost = 0.0
    t0 = time.time()
    with output_path.open("w", encoding="utf-8") as fh:
        for fut in asyncio.as_completed(coros):
            packet = await fut
            fh.write(json.dumps(packet, ensure_ascii=False) + "\n")
            fh.flush()
            completed += 1
            if packet.get("error"):
                errors += 1
            in_tok, out_tok, cost = _estimate_cost(packet)
            total_in_tok += in_tok
            total_out_tok += out_tok
            total_cost += cost
            if completed % 50 == 0 or completed == len(cells):
                elapsed = time.time() - t0
                print(
                    f"  {completed}/{len(cells)} done "
                    f"({elapsed:.0f}s, errors={errors}, "
                    f"~tok in={total_in_tok}, out={total_out_tok}, "
                    f"~cost=${total_cost:.2f})",
                    flush=True,
                )
    return completed


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=DEFAULT_TRIALS_PER_CELL,
                   help="Trials per (model, task, tier) cell")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help="Max concurrent API calls")
    p.add_argument("--dry-run", action="store_true",
                   help=f"Run only {DRY_RUN_TRIALS_PER_CELL} trials per cell (~120 total)")
    p.add_argument("--output", type=Path, default=OUTPUT_FILE,
                   help="Output JSONL path")
    args = p.parse_args()

    trials = DRY_RUN_TRIALS_PER_CELL if args.dry_run else args.trials
    print(f"trials_per_cell = {trials}, concurrency = {args.concurrency}", flush=True)
    print(f"output: {args.output}", flush=True)

    started = datetime.now(timezone.utc).isoformat()
    n = asyncio.run(run_all(trials, args.concurrency, args.output))
    finished = datetime.now(timezone.utc).isoformat()
    print(f"\nDone. Wrote {n} packets to {args.output}")
    print(f"  started:  {started}")
    print(f"  finished: {finished}")


if __name__ == "__main__":
    main()
