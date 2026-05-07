#!/usr/bin/env python3
"""
anthropic_channel.py

Channel B_claude. Refactored from claude_trajectory_pivot_experiment.py to
expose a single async entry point used by the unified orchestrator.

Exports:
  run_anthropic_channel(
    models, tasks, tiers, trials_per_cell, encoder, output_path,
    pre_reg_hash, manifest_hash, verifier_hash, encoder_pkg_version,
    shared_jsonl_lock, progress, concurrency=8, order_seed=20260504,
  ) -> int
"""
from __future__ import annotations

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
from typing import List, Optional, Sequence, Set, Tuple

# Import sibling modules (from supplementary/experiments/)
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from code_constraint_tasks import TASKS, CodeTask  # noqa: E402
from code_constraint_verifier import (  # noqa: E402
    FORMAT_TIERS,
    extract_code_block,
    verify_both,
    format_rules_to_prompt,
)


PACKET_SCHEMA_VERSION = "v4.1"
OBSERVER_ID = "B_claude"
ENCODER_ID = "sentence-transformers/all-MiniLM-L6-v2"
CHUNKER_ID = "anthropic_stream_delta"
MAX_TOKENS = 1024

# Secrets are loaded from environment, never hardcoded. Reviewers and
# replicators set ANTHROPIC_API_KEY (or point AUDIT_ANTHROPIC_ENV_FILE at
# a file containing ``ANTHROPIC_API_KEY=...``). Authors do the same in
# their local shell before invoking the orchestrator.

# Indicative pricing (USD per 1M tokens). Used only for live cost estimate.
PRICING_PER_MTOK = {
    "claude-haiku-4-5":  {"in": 1.00,  "out": 5.00},
    "claude-sonnet-4-6": {"in": 3.00,  "out": 15.00},
    "claude-opus-4-7":   {"in": 15.00, "out": 75.00},
    "claude-opus-4-6":   {"in": 15.00, "out": 75.00},
}
DEFAULT_PRICING = {"in": 3.00, "out": 15.00}


def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_anthropic_key() -> str:
    import os as _os
    key = _os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip().strip('"').strip("'")
    env_file_path = _os.environ.get("AUDIT_ANTHROPIC_ENV_FILE")
    if env_file_path:
        env_file = Path(env_file_path)
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "ANTHROPIC_API_KEY not set. Either export the variable or set "
        "AUDIT_ANTHROPIC_ENV_FILE to a file containing ANTHROPIC_API_KEY=..."
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


def build_prompt(task: CodeTask, tier_name: str) -> str:
    rules = FORMAT_TIERS[tier_name]
    rules_text = format_rules_to_prompt(rules)
    return (
        f"{task.description}\n\n"
        f"Constraints:\n{rules_text}\n\n"
        f"Return only the function in a single ```python ... ``` code block."
    )


def _retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    keywords = ("ratelimit", "timeout", "overloaded", "apiconnection",
                "503", "502", "504", "429", "transient")
    return any(k in name or k in msg for k in keywords)


@dataclass
class _Cell:
    model: str
    task: CodeTask
    tier: str
    trial_idx: int


def _approx_token_count(s: str) -> int:
    return max(1, len(s) // 4)


def _estimate_cost(model_id: str, prompt_text: str, output_text: str) -> tuple[int, int, float]:
    pricing = PRICING_PER_MTOK.get(model_id, DEFAULT_PRICING)
    in_tok = _approx_token_count(prompt_text)
    out_tok = _approx_token_count(output_text)
    cost = (in_tok * pricing["in"] + out_tok * pricing["out"]) / 1_000_000.0
    return in_tok, out_tok, cost


async def _run_single_trial(
    client,
    encoder,
    cell: _Cell,
    sem: asyncio.Semaphore,
    pre_reg_hash: str,
    verifier_hash: str,
    manifest_hash: str,
    encoder_pkg_version: str,
) -> dict:
    import numpy as np

    prompt_text = build_prompt(cell.task, cell.tier)
    prompt_hash = sha256_str(prompt_text)

    kwargs = dict(
        model=cell.model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt_text}],
    )
    # opus-4-7 rejects temperature; older opus-4-6 also gated on the same
    # endpoint family. Be conservative: omit temperature for any opus-4-7
    # snapshot, accept default sampling otherwise pin to 1.0.
    if "opus-4-7" not in cell.model:
        kwargs["temperature"] = 1.0

    chunk_trace: List[dict] = []
    running_text = ""
    api_model_snapshot = cell.model
    error_msg: Optional[str] = None
    # v4.1 provenance fields. Default to None so that on every error path
    # (including the case where get_final_message() never executes) the
    # outgoing packet still carries the documented schema with no crash.
    api_request_id: Optional[str] = None
    actual_token_usage: Optional[dict] = None
    stop_reason: Optional[str] = None
    wall_time_ms: Optional[float] = None

    async with sem:
        backoff = 2.0
        for attempt in range(3):
            chunk_trace = []
            running_text = ""
            initial_emb = None
            prior_emb = None
            api_request_id = None
            actual_token_usage = None
            stop_reason = None
            wall_time_ms = None
            t0 = time.monotonic()
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
                    final_msg = await stream.get_final_message()
                    api_model_snapshot = getattr(final_msg, "model", cell.model) or cell.model
                    # v4.1 provenance extraction (best-effort; never raise)
                    try:
                        api_request_id = getattr(final_msg, "id", None)
                        usage_obj = getattr(final_msg, "usage", None)
                        if usage_obj is not None:
                            if hasattr(usage_obj, "model_dump"):
                                actual_token_usage = usage_obj.model_dump()
                            elif hasattr(usage_obj, "dict"):
                                actual_token_usage = usage_obj.dict()
                            else:
                                actual_token_usage = {
                                    k: getattr(usage_obj, k)
                                    for k in (
                                        "input_tokens", "output_tokens",
                                        "cache_creation_input_tokens",
                                        "cache_read_input_tokens",
                                    )
                                    if hasattr(usage_obj, k)
                                }
                        stop_reason = getattr(final_msg, "stop_reason", None)
                    except Exception:  # noqa: BLE001
                        # Provenance is best-effort; do not fail the trial.
                        pass
                wall_time_ms = (time.monotonic() - t0) * 1000.0
                error_msg = None
                break
            except Exception as e:  # noqa: BLE001
                wall_time_ms = (time.monotonic() - t0) * 1000.0
                error_msg = f"{type(e).__name__}: {e}"
                if attempt < 2 and _retryable(e):
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                break

    output_text = running_text
    output_hash = sha256_str(output_text)

    if error_msg is None:
        try:
            code = extract_code_block(output_text) or ""
            verifier_result = verify_both(code, cell.task.test_code, FORMAT_TIERS[cell.tier])
        except Exception as ve:
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
        "chunker_id": CHUNKER_ID,
        "error": error_msg,
        # v4.1 additive provenance fields. openrouter_provider is always
        # None on this channel by design (Anthropic owns the request).
        "api_request_id": api_request_id,
        "actual_token_usage": actual_token_usage,
        "stop_reason": stop_reason,
        "openrouter_provider": None,
        "wall_time_ms": wall_time_ms,
    }
    return packet


async def run_anthropic_channel(
    models: Sequence[str],
    tasks: Sequence[str],
    tiers: Sequence[str],
    trials_per_cell: int,
    encoder,
    output_path: Path,
    pre_reg_hash: str,
    manifest_hash: str,
    verifier_hash: str,
    encoder_pkg_version: str,
    shared_jsonl_lock: asyncio.Lock,
    progress: dict,
    concurrency: int = 8,
    order_seed: int = 20260504,
    stop_evt: Optional[asyncio.Event] = None,
    skip_cells: Optional[Set[Tuple[str, str, str, str, int]]] = None,
) -> dict:
    """Run the Anthropic channel and append packets to the shared JSONL.

    Returns {completed, errors, cost_usd, total_in_tok, total_out_tok,
             skipped, partial}.
    """
    from anthropic import AsyncAnthropic

    api_key = load_anthropic_key()
    client = AsyncAnthropic(api_key=api_key)

    tasks_by_id = {t.task_id: t for t in TASKS}
    selected = [tasks_by_id[tid] for tid in tasks if tid in tasks_by_id]
    missing = set(tasks) - {t.task_id for t in selected}
    if missing:
        raise RuntimeError(f"Anthropic channel: missing tasks {missing}")

    skip_cells = skip_cells or set()

    cells: List[_Cell] = []
    skipped = 0
    for model in models:
        for task in selected:
            for tier in tiers:
                for trial_idx in range(trials_per_cell):
                    key = (OBSERVER_ID, model, task.task_id, tier, trial_idx)
                    if key in skip_cells:
                        skipped += 1
                        continue
                    cells.append(_Cell(model=model, task=task, tier=tier, trial_idx=trial_idx))

    rng = random.Random(order_seed)
    rng.shuffle(cells)

    total = len(cells)
    progress["anthropic_total"] = total
    progress["anthropic_done"] = 0
    progress["anthropic_errors"] = 0
    progress["anthropic_cost"] = 0.0

    sem = asyncio.Semaphore(concurrency)

    coros = [
        _run_single_trial(
            client, encoder, cell, sem,
            pre_reg_hash=pre_reg_hash,
            verifier_hash=verifier_hash,
            manifest_hash=manifest_hash,
            encoder_pkg_version=encoder_pkg_version,
        )
        for cell in cells
    ]

    completed = 0
    errors = 0
    total_in_tok = 0
    total_out_tok = 0
    total_cost = 0.0
    partial = False

    for fut in asyncio.as_completed(coros):
        packet = await fut
        # Respect SIGINT graceful drain: still write packets that already
        # finished, but bail before counting more.
        async with shared_jsonl_lock:
            with output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(packet, ensure_ascii=False) + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except (OSError, AttributeError):  # pragma: no cover
                    pass
        completed += 1
        if packet.get("error"):
            errors += 1
        in_tok, out_tok, cost = _estimate_cost(
            packet["model_id"], packet["prompt_text"], packet["output_text"]
        )
        total_in_tok += in_tok
        total_out_tok += out_tok
        total_cost += cost
        progress["anthropic_done"] = completed
        progress["anthropic_errors"] = errors
        progress["anthropic_cost"] = total_cost
        if stop_evt is not None and stop_evt.is_set():
            partial = True
            break

    return {
        "completed": completed,
        "errors": errors,
        "cost_usd": total_cost,
        "total_in_tok": total_in_tok,
        "total_out_tok": total_out_tok,
        "skipped": skipped,
        "partial": partial,
    }
