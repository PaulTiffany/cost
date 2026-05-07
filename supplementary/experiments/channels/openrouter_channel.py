#!/usr/bin/env python3
"""
openrouter_channel.py

Channel B_openweight. Refactored from openrouter_pivot_positive_control.py
to expose a single async entry point used by the unified orchestrator.

Exports:
  run_openrouter_channel(
    models, tasks, tiers, trials_per_cell, encoder, output_path,
    pre_reg_hash, manifest_hash, verifier_hash, encoder_pkg_version,
    shared_jsonl_lock, progress, concurrency=8, order_seed=20260504,
  ) -> dict
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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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
OBSERVER_ID = "B_openweight"
ENCODER_ID = "sentence-transformers/all-MiniLM-L6-v2"
CHUNKER_ID = "openrouter_sse_delta"

MAX_TOKENS = 1024
TEMPERATURE = 1.0
RETRIES = 3
RETRY_BASE_DELAY = 4.0
REQUEST_TIMEOUT_SEC = 180

# Secrets are loaded from environment, never hardcoded. Reviewers and
# replicators set OPENROUTER_API_KEY (or point AUDIT_OPENROUTER_ENV_FILE
# at a file containing ``OPENROUTER_API_KEY=...``).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Indicative pricing (USD per 1M tokens) for cost estimate. Values are
# rough; exact billing comes from OpenRouter's accounting.
PRICING_PER_MTOK_DEFAULT = {"in": 0.40, "out": 1.20}
PRICING_PER_MTOK = {
    "qwen/qwen-2.5-coder-32b-instruct":  {"in": 0.07, "out": 0.16},
    "qwen/qwen3-coder":                  {"in": 0.20, "out": 0.80},
    "qwen/qwen3-coder-plus":             {"in": 0.50, "out": 2.00},
    "qwen/qwen3-235b-a22b-thinking-2507": {"in": 0.20, "out": 0.80},
    "deepseek/deepseek-chat-v3.1":       {"in": 0.27, "out": 1.10},
    "deepseek/deepseek-r1":              {"in": 0.55, "out": 2.19},
    "mistralai/codestral-2508":          {"in": 0.30, "out": 0.90},
    "arcee-ai/coder-large":              {"in": 0.50, "out": 0.80},
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_openrouter_key() -> str:
    import os as _os
    key = _os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip().strip('"').strip("'")
    env_file_path = _os.environ.get("AUDIT_OPENROUTER_ENV_FILE")
    if env_file_path:
        env_file = Path(env_file_path)
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "OPENROUTER_API_KEY not set. Either export the variable or set "
        "AUDIT_OPENROUTER_ENV_FILE to a file containing OPENROUTER_API_KEY=..."
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


@dataclass
class _Cell:
    model: str
    task: CodeTask
    tier: str
    trial_idx: int


def _approx_tok(s: str) -> int:
    return max(1, len(s) // 4)


def _estimate_cost(model_id: str, prompt: str, output: str) -> tuple[int, int, float]:
    pricing = PRICING_PER_MTOK.get(model_id, PRICING_PER_MTOK_DEFAULT)
    in_tok = _approx_tok(prompt)
    out_tok = _approx_tok(output)
    cost = (in_tok * pricing["in"] + out_tok * pricing["out"]) / 1_000_000.0
    return in_tok, out_tok, cost


async def _stream_once(client, model_id: str, prompt: str, encoder) -> Dict[str, Any]:
    import numpy as np

    chunk_trace: List[Dict[str, Any]] = []
    displacements: List[float] = []
    drift_angles: List[float] = []
    running_text = ""
    prior_emb = None
    initial_dir = None
    api_model_snapshot = ""
    usage_total_tokens = 0
    # v4.1 provenance: captured from first / last SSE chunks. OpenRouter
    # echoes `id` and `provider` near the head of the stream and `usage` /
    # `finish_reason` at the tail, so we update on every event seen.
    api_request_id: Optional[str] = None
    actual_token_usage: Optional[Dict[str, Any]] = None
    stop_reason: Optional[str] = None
    openrouter_provider: Optional[str] = None

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
        if not api_model_snapshot:
            try:
                snap = getattr(event, "model", None)
                if snap:
                    api_model_snapshot = str(snap)
            except Exception:
                pass

        # v4.1 provenance: capture id (first chunk) and provider (any chunk
        # that carries it; OR sometimes attaches it to the wrapper).
        if api_request_id is None:
            try:
                rid = getattr(event, "id", None)
                if rid:
                    api_request_id = str(rid)
            except Exception:
                pass
        if openrouter_provider is None:
            try:
                prov = getattr(event, "provider", None)
                if prov:
                    openrouter_provider = str(prov)
            except Exception:
                pass

        try:
            usage = getattr(event, "usage", None)
            if usage is not None:
                tt = getattr(usage, "total_tokens", None)
                if tt:
                    usage_total_tokens = int(tt)
                # v4.1: capture verbatim usage dict from final chunk
                try:
                    if hasattr(usage, "model_dump"):
                        actual_token_usage = usage.model_dump()
                    elif hasattr(usage, "dict"):
                        actual_token_usage = usage.dict()
                    else:
                        actual_token_usage = {
                            k: getattr(usage, k)
                            for k in (
                                "prompt_tokens",
                                "completion_tokens",
                                "total_tokens",
                            )
                            if hasattr(usage, k)
                        }
                except Exception:
                    pass
        except Exception:
            pass

        choices = getattr(event, "choices", None) or []
        if choices:
            try:
                fr = getattr(choices[0], "finish_reason", None)
                if fr:
                    stop_reason = str(fr)
            except Exception:
                pass
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
        # v4.1 provenance fields
        "api_request_id": api_request_id,
        "actual_token_usage": actual_token_usage,
        "stop_reason": stop_reason,
        "openrouter_provider": openrouter_provider,
    }


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
    prompt = build_prompt(cell.task, cell.tier)
    prompt_h = sha256_text(prompt)
    err_msg: Optional[str] = None
    stream_result: Optional[Dict[str, Any]] = None
    wall_time_ms: Optional[float] = None

    async with sem:
        for attempt in range(1, RETRIES + 1):
            t0 = time.monotonic()
            try:
                stream_result = await _stream_once(client, cell.model, prompt, encoder)
                wall_time_ms = (time.monotonic() - t0) * 1000.0
                err_msg = None
                break
            except Exception as e:
                wall_time_ms = (time.monotonic() - t0) * 1000.0
                err_msg = f"{type(e).__name__}: {e}"[:500]
                if attempt < RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                else:
                    stream_result = None

    timestamp = datetime.now(timezone.utc).isoformat()

    if stream_result is None:
        return {
            "packet_schema_version": PACKET_SCHEMA_VERSION,
            "observer_id": OBSERVER_ID,
            "model_id": cell.model,
            "api_model_snapshot": cell.model,
            "task_id": cell.task.task_id,
            "tier": cell.tier,
            "trial_idx": cell.trial_idx,
            "prompt_text": prompt,
            "prompt_hash": prompt_h,
            "output_text": "",
            "output_hash": sha256_text(""),
            "chunk_trace": [],
            "n_chunks": 0,
            "max_chunk_displacement": 0.0,
            "mean_chunk_displacement": 0.0,
            "max_drift_deg": 0.0,
            "verifier_result": {
                "pass_a": False, "pass_b": False, "pass_both": False,
                "msg_a": "no output (api error)", "msg_b": "no output (api error)",
            },
            "verifier_hash": verifier_hash,
            "timestamp_utc": timestamp,
            "encoder_id": ENCODER_ID,
            "encoder_package_version": encoder_pkg_version,
            "pre_reg_hash": pre_reg_hash,
            "manifest_hash": manifest_hash,
            "chunker_id": CHUNKER_ID,
            "error": err_msg,
            # v4.1 additive provenance fields. On the error path none of
            # these were captured from the API, so all five default to None
            # and the packet still validates against the v4.1 schema.
            "api_request_id": None,
            "actual_token_usage": None,
            "stop_reason": None,
            "openrouter_provider": None,
            "wall_time_ms": wall_time_ms,
        }

    output_text = stream_result["running_text"]
    chunk_trace = stream_result["chunk_trace"]
    displacements = stream_result["displacements"]
    drift_angles = stream_result["drift_angles"]
    api_snapshot = stream_result["api_model_snapshot"]

    code = extract_code_block(output_text) or ""
    rules = FORMAT_TIERS[cell.tier]
    verifier_result = verify_both(code, cell.task.test_code, rules)

    max_disp = float(max(displacements)) if displacements else 0.0
    mean_disp = float(sum(displacements) / len(displacements)) if displacements else 0.0
    max_drift = float(max(drift_angles)) if drift_angles else 0.0

    return {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "observer_id": OBSERVER_ID,
        "model_id": cell.model,
        "api_model_snapshot": api_snapshot,
        "task_id": cell.task.task_id,
        "tier": cell.tier,
        "trial_idx": cell.trial_idx,
        "prompt_text": prompt,
        "prompt_hash": prompt_h,
        "output_text": output_text,
        "output_hash": sha256_text(output_text),
        "chunk_trace": chunk_trace,
        "n_chunks": len(chunk_trace),
        "max_chunk_displacement": max_disp,
        "mean_chunk_displacement": mean_disp,
        "max_drift_deg": max_drift,
        "verifier_result": verifier_result,
        "verifier_hash": verifier_hash,
        "timestamp_utc": timestamp,
        "encoder_id": ENCODER_ID,
        "encoder_package_version": encoder_pkg_version,
        "pre_reg_hash": pre_reg_hash,
        "manifest_hash": manifest_hash,
        "chunker_id": CHUNKER_ID,
        "error": None,
        # v4.1 additive provenance fields, captured during streaming.
        "api_request_id": stream_result.get("api_request_id"),
        "actual_token_usage": stream_result.get("actual_token_usage"),
        "stop_reason": stream_result.get("stop_reason"),
        "openrouter_provider": stream_result.get("openrouter_provider"),
        "wall_time_ms": wall_time_ms,
    }


async def run_openrouter_channel(
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
    """Run the OpenRouter channel and append packets to the shared JSONL.

    Returns {completed, errors, cost_usd, total_in_tok, total_out_tok,
             skipped, partial}.
    """
    from openai import AsyncOpenAI

    api_key = load_openrouter_key()
    client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    tasks_by_id = {t.task_id: t for t in TASKS}
    selected = [tasks_by_id[tid] for tid in tasks if tid in tasks_by_id]
    missing = set(tasks) - {t.task_id for t in selected}
    if missing:
        raise RuntimeError(f"OpenRouter channel: missing tasks {missing}")

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

    rng = random.Random(order_seed + 1)  # offset to decorrelate from anthropic ordering
    rng.shuffle(cells)

    total = len(cells)
    progress["openrouter_total"] = total
    progress["openrouter_done"] = 0
    progress["openrouter_errors"] = 0
    progress["openrouter_cost"] = 0.0

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
        progress["openrouter_done"] = completed
        progress["openrouter_errors"] = errors
        progress["openrouter_cost"] = total_cost
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
