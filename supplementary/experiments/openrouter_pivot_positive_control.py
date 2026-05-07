#!/usr/bin/env python3
"""
openrouter_pivot_positive_control.py

POSITIVE CONTROL black-box observer (B_openweight) for the audit v4
experiment. Same pipeline as claude_trajectory_pivot_experiment.py, but
runs against qwen-2.5-coder-7b-instruct via OpenRouter to reproduce the
model class the original paper measured.

Emits typed ObservationPackets per pre-registration v4 §3 schema. No
summary statistics computed here -- the audit observer (separate
process, cert L31) does classification.

Pre-registration: supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md
Output:           supplementary/experiments/outputs/audit_v4/
                  openrouter_observation_packets.jsonl

Usage:
  python supplementary/experiments/openrouter_pivot_positive_control.py
  python supplementary/experiments/openrouter_pivot_positive_control.py --dry-run
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
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Local imports
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from code_constraint_tasks import TASKS, CodeTask
from code_constraint_verifier import (
    FORMAT_TIERS,
    extract_code_block,
    verify_both,
    format_rules_to_prompt,
)


# ---------------------------------------------------------------------------
# Configuration (locked by pre-registration v4 §5)
# ---------------------------------------------------------------------------

OBSERVER_ID = "B_openweight"
PACKET_SCHEMA_VERSION = "v4.0"
MODEL_ID = "qwen/qwen-2.5-coder-32b-instruct"
# NOTE: Pre-registration v4 §5 names "qwen-2.5-coder-7b-instruct" as the
# positive-control model. As of this run OpenRouter does NOT serve the 7B
# variant; the closest available member of the same model class is the 32B
# instruct. Using the 32B is a documented, minimal deviation from the
# pre-reg (§14): same architecture family, same model class as the
# paper's original calibration. If the 7B is later served on a provider,
# rerun under pre-reg v5 with the 7B endpoint substituted.

SELECTED_TASK_IDS = [
    "factorial",
    "fibonacci",
    "is_palindrome",
    "gcd",
    "reverse_words",
    "fizzbuzz",
]
TIERS = ["control", "low", "moderate", "high"]
DEFAULT_TRIALS = 10
MAX_TOKENS = 1024
DEFAULT_CONCURRENCY = 8
TEMPERATURE = 1.0
RANDOM_SEED = 20260504
RETRIES = 3
RETRY_BASE_DELAY = 4.0  # seconds; exponential -> 4s, 8s, 16s
REQUEST_TIMEOUT_SEC = 180

import os as _os
ENV_FILE = Path(_os.environ["AUDIT_OPENROUTER_ENV_FILE"]) if _os.environ.get("AUDIT_OPENROUTER_ENV_FILE") else None
# Resolve pre-reg relative to this file so the path is repo-portable.
PRE_REG_FILE = SCRIPT_DIR.parent / "PRE_REGISTRATION_AUDIT_OBSERVER_v4.md"
VERIFIER_FILE = SCRIPT_DIR / "code_constraint_verifier.py"
TASKS_FILE = SCRIPT_DIR / "code_constraint_tasks.py"
THIS_FILE = Path(__file__).resolve()

OUTPUT_DIR = SCRIPT_DIR / "outputs" / "audit_v4"
OUTPUT_FILE = OUTPUT_DIR / "openrouter_observation_packets.jsonl"

ENCODER_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DEVICE = "cpu"
CHUNKER_ID = "openrouter_sse_delta"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# ObservationPacket (pre-reg v4 §3, frozen schema)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObservationPacket:
    packet_schema_version: str
    observer_id: str
    model_id: str
    api_model_snapshot: str
    task_id: str
    tier: str
    trial_idx: int
    prompt_text: str
    prompt_hash: str
    output_text: str
    output_hash: str
    chunk_trace: List[Dict[str, Any]]
    n_chunks: int
    max_chunk_displacement: float
    mean_chunk_displacement: float
    max_drift_deg: float
    verifier_result: Dict[str, Any]
    verifier_hash: str
    timestamp_utc: str
    encoder_id: str
    encoder_package_version: str
    pre_reg_hash: str
    manifest_hash: str
    chunker_id: str
    error: Optional[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_openrouter_key() -> str:
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Env file not found: {ENV_FILE}")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError(f"OPENROUTER_API_KEY not found in {ENV_FILE}")


def build_prompt(task: CodeTask, tier_name: str) -> str:
    rules = FORMAT_TIERS[tier_name]
    rules_text = format_rules_to_prompt(rules)
    return (
        f"{task.description}\n\n"
        f"Constraints:\n{rules_text}\n\n"
        f"Return only the function in a single ```python ... ``` code block."
    )


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
# Encoder
# ---------------------------------------------------------------------------

class TrajectoryEncoder:
    """Wraps MiniLM-L6-v2 for streaming-text embedding (CPU)."""

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(ENCODER_NAME, device=EMBED_DEVICE)

    def embed(self, text: str):
        import numpy as np
        if not text.strip():
            return np.zeros(384, dtype=float)
        v = self.model.encode(
            [text], show_progress_bar=False, normalize_embeddings=False
        )[0]
        return v


def encoder_pkg_version() -> str:
    try:
        import sentence_transformers
        return getattr(sentence_transformers, "__version__", "unknown")
    except Exception:
        return "unavailable"


# ---------------------------------------------------------------------------
# Single-trial OpenRouter streaming + chunk trace
# ---------------------------------------------------------------------------

async def _stream_once(
    client,
    model_id: str,
    prompt: str,
    encoder: TrajectoryEncoder,
) -> Dict[str, Any]:
    """One streaming attempt. Returns dict with raw stream products or raises."""
    import numpy as np

    chunk_trace: List[Dict[str, Any]] = []
    displacements: List[float] = []
    drift_angles: List[float] = []
    running_text = ""
    prior_emb = None
    initial_dir = None
    api_model_snapshot = ""
    usage_total_tokens = 0

    stream = await client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        stream=True,
        stream_options={"include_usage": True},
        timeout=REQUEST_TIMEOUT_SEC,
    )

    chunk_idx = 0
    async for event in stream:
        # Capture model snapshot from the first chunk that carries it
        if not api_model_snapshot:
            try:
                snap = getattr(event, "model", None)
                if snap:
                    api_model_snapshot = str(snap)
            except Exception:
                pass

        # Usage info ships in the final chunk when include_usage=True
        try:
            usage = getattr(event, "usage", None)
            if usage is not None:
                tt = getattr(usage, "total_tokens", None)
                if tt:
                    usage_total_tokens = int(tt)
        except Exception:
            pass

        choices = getattr(event, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue
        text_chunk = getattr(delta, "content", None)
        if not text_chunk:
            continue

        running_text += text_chunk
        emb = encoder.embed(running_text)

        disp = 0.0
        ang = 0.0
        if prior_emb is not None:
            disp = float(np.linalg.norm(emb - prior_emb))
            displacements.append(disp)
            if initial_dir is None and float(np.linalg.norm(emb)) > 1e-6:
                initial_dir = emb.copy()
            elif initial_dir is not None and float(np.linalg.norm(emb)) > 1e-6:
                cs = cosine(initial_dir, emb)
                ang = math.degrees(math.acos(max(-1.0, min(1.0, cs))))
                drift_angles.append(ang)

        chunk_trace.append({
            "chunk_idx": chunk_idx,
            "text_appended": text_chunk,
            "displacement": disp,
            "drift_deg": ang,
            "cumulative_chars": len(running_text),
        })
        prior_emb = emb
        chunk_idx += 1

    return {
        "running_text": running_text,
        "chunk_trace": chunk_trace,
        "displacements": displacements,
        "drift_angles": drift_angles,
        "api_model_snapshot": api_model_snapshot or model_id,
        "usage_total_tokens": usage_total_tokens,
    }


async def run_single_trial(
    client,
    encoder: TrajectoryEncoder,
    task: CodeTask,
    tier: str,
    trial_idx: int,
    sem: asyncio.Semaphore,
    hashes: Dict[str, str],
    token_counter: Dict[str, int],
) -> ObservationPacket:
    """Run a single trial with retries; build an ObservationPacket."""
    prompt = build_prompt(task, tier)
    prompt_h = sha256_text(prompt)

    err_msg: Optional[str] = None
    stream_result: Optional[Dict[str, Any]] = None

    async with sem:
        for attempt in range(1, RETRIES + 1):
            try:
                stream_result = await _stream_once(client, MODEL_ID, prompt, encoder)
                err_msg = None
                break
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"[:500]
                if attempt < RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                else:
                    stream_result = None

    timestamp = datetime.now(timezone.utc).isoformat()

    if stream_result is None:
        # Permanent failure -- emit packet with error set
        return ObservationPacket(
            packet_schema_version=PACKET_SCHEMA_VERSION,
            observer_id=OBSERVER_ID,
            model_id=MODEL_ID,
            api_model_snapshot=MODEL_ID,
            task_id=task.task_id,
            tier=tier,
            trial_idx=trial_idx,
            prompt_text=prompt,
            prompt_hash=prompt_h,
            output_text="",
            output_hash=sha256_text(""),
            chunk_trace=[],
            n_chunks=0,
            max_chunk_displacement=0.0,
            mean_chunk_displacement=0.0,
            max_drift_deg=0.0,
            verifier_result={"pass_a": False, "pass_b": False, "pass_both": False,
                             "msg_a": "no output (api error)", "msg_b": "no output (api error)"},
            verifier_hash=hashes["verifier_hash"],
            timestamp_utc=timestamp,
            encoder_id=ENCODER_NAME,
            encoder_package_version=hashes["encoder_pkg_version"],
            pre_reg_hash=hashes["pre_reg_hash"],
            manifest_hash=hashes["manifest_hash"],
            chunker_id=CHUNKER_ID,
            error=err_msg,
        )

    output_text = stream_result["running_text"]
    chunk_trace = stream_result["chunk_trace"]
    displacements = stream_result["displacements"]
    drift_angles = stream_result["drift_angles"]
    api_snapshot = stream_result["api_model_snapshot"]

    token_counter["total"] += int(stream_result.get("usage_total_tokens", 0) or 0)

    # Verifier
    code = extract_code_block(output_text) or ""
    rules = FORMAT_TIERS[tier]
    verifier_result = verify_both(code, task.test_code, rules)

    max_disp = float(max(displacements)) if displacements else 0.0
    mean_disp = float(sum(displacements) / len(displacements)) if displacements else 0.0
    max_drift = float(max(drift_angles)) if drift_angles else 0.0

    return ObservationPacket(
        packet_schema_version=PACKET_SCHEMA_VERSION,
        observer_id=OBSERVER_ID,
        model_id=MODEL_ID,
        api_model_snapshot=api_snapshot,
        task_id=task.task_id,
        tier=tier,
        trial_idx=trial_idx,
        prompt_text=prompt,
        prompt_hash=prompt_h,
        output_text=output_text,
        output_hash=sha256_text(output_text),
        chunk_trace=chunk_trace,
        n_chunks=len(chunk_trace),
        max_chunk_displacement=max_disp,
        mean_chunk_displacement=mean_disp,
        max_drift_deg=max_drift,
        verifier_result=verifier_result,
        verifier_hash=hashes["verifier_hash"],
        timestamp_utc=timestamp,
        encoder_id=ENCODER_NAME,
        encoder_package_version=hashes["encoder_pkg_version"],
        pre_reg_hash=hashes["pre_reg_hash"],
        manifest_hash=hashes["manifest_hash"],
        chunker_id=CHUNKER_ID,
        error=None,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def compute_hashes() -> Dict[str, str]:
    pre_reg_hash = sha256_file(PRE_REG_FILE) if PRE_REG_FILE.exists() else "PRE_REG_FILE_MISSING"
    verifier_hash = sha256_file(VERIFIER_FILE)
    tasks_hash = sha256_file(TASKS_FILE)
    script_hash = sha256_file(THIS_FILE)
    # Manifest = concat of (pre_reg + verifier + tasks + this script) hashes
    manifest_payload = (
        pre_reg_hash + verifier_hash + tasks_hash + script_hash
    ).encode("utf-8")
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    return {
        "pre_reg_hash": pre_reg_hash,
        "verifier_hash": verifier_hash,
        "tasks_hash": tasks_hash,
        "script_hash": script_hash,
        "manifest_hash": manifest_hash,
        "encoder_pkg_version": encoder_pkg_version(),
    }


async def run_all(trials_per_cell: int, concurrency: int, output_path: Path) -> int:
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        print(f"openai package required: {e}", file=sys.stderr)
        return 1

    api_key = load_openrouter_key()
    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    print("Loading MiniLM encoder ...", flush=True)
    encoder = TrajectoryEncoder()

    hashes = compute_hashes()
    print(f"  pre_reg_hash:  {hashes['pre_reg_hash'][:16]}...", flush=True)
    print(f"  verifier_hash: {hashes['verifier_hash'][:16]}...", flush=True)
    print(f"  manifest_hash: {hashes['manifest_hash'][:16]}...", flush=True)

    tasks_by_id = {t.task_id: t for t in TASKS}
    selected_tasks = [tasks_by_id[tid] for tid in SELECTED_TASK_IDS if tid in tasks_by_id]
    if len(selected_tasks) < len(SELECTED_TASK_IDS):
        missing = set(SELECTED_TASK_IDS) - {t.task_id for t in selected_tasks}
        print(f"  WARNING: missing tasks {missing}", flush=True)

    # Build full work list: (task, tier, trial_idx)
    cells: List[tuple] = []
    for task in selected_tasks:
        for tier in TIERS:
            for trial_idx in range(trials_per_cell):
                cells.append((task, tier, trial_idx))

    # Random ordering, deterministic seed
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(cells)

    sem = asyncio.Semaphore(concurrency)
    token_counter = {"total": 0}

    coros = [
        run_single_trial(
            client, encoder, task, tier, trial_idx, sem, hashes, token_counter
        )
        for (task, tier, trial_idx) in cells
    ]

    print(
        f"Launching {len(coros)} trials at concurrency={concurrency} "
        f"(seed={RANDOM_SEED}) ...",
        flush=True,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate any prior output for a clean run
    output_path.write_text("", encoding="utf-8")

    t0 = time.time()
    completed = 0
    errors = 0
    with output_path.open("a", encoding="utf-8") as fh:
        for fut in asyncio.as_completed(coros):
            packet = await fut
            fh.write(json.dumps(asdict(packet), ensure_ascii=False) + "\n")
            fh.flush()
            completed += 1
            if packet.error:
                errors += 1
            if completed % 10 == 0 or completed == len(coros):
                elapsed = time.time() - t0
                print(
                    f"  {completed}/{len(coros)} done ({elapsed:.0f}s, "
                    f"errors={errors}, tokens~{token_counter['total']})",
                    flush=True,
                )

    elapsed = time.time() - t0
    print(
        f"\nDone. Wrote {completed} packets to {output_path} "
        f"({errors} errors, {elapsed:.1f}s, ~{token_counter['total']} tokens)",
        flush=True,
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=DEFAULT_TRIALS,
                   help="Trials per (task, tier) cell (default 10).")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help="Max concurrent OpenRouter calls (default 8).")
    p.add_argument("--dry-run", action="store_true",
                   help="Run only 2 trials per cell (~48 trials).")
    p.add_argument("--output", type=str, default=str(OUTPUT_FILE),
                   help="Override output JSONL path.")
    p.add_argument("--model", type=str, default=None,
                   help=("OpenRouter model id. Default tracks pre-reg-class "
                         "(2.5-coder-32b). Override only with explicit "
                         "deviation justification."))
    args = p.parse_args()

    trials = 2 if args.dry_run else args.trials
    output_path = Path(args.output)

    # Allow CLI override for transient provider outages; record the
    # effective id in every packet's api_model_snapshot.
    global MODEL_ID
    if args.model:
        MODEL_ID = args.model

    print(f"observer_id:    {OBSERVER_ID}")
    print(f"model_id:       {MODEL_ID}")
    print(f"trials/cell:    {trials}")
    print(f"concurrency:    {args.concurrency}")
    print(f"output:         {output_path}")

    return asyncio.run(run_all(trials, args.concurrency, output_path))


if __name__ == "__main__":
    sys.exit(main())
