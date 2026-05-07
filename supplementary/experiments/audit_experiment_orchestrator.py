#!/usr/bin/env python3
"""
audit_experiment_orchestrator.py

Single entry point for the unified two-channel audit experiment (audit
v4 unified). Runs Anthropic + OpenRouter channels CONCURRENTLY via
asyncio.gather() and emits ObservationPackets to ONE shared JSONL file
(line-interleaved). Channels are distinguished by `observer_id`
(B_claude vs. B_openweight).

Hash-chain: every packet carries identical pre_reg_hash + manifest_hash.
The manifest_hash covers:
    pre_reg + this orchestrator + anthropic_channel + openrouter_channel
    + audit_observer.

Modes:
  --dry-run    2 trials per cell  (~fast validation)
  --default    10 trials per cell (current pre-reg sample)
  --full       15 trials per cell (production run)
  --config P   load model + sample config from JSON
  --go         REQUIRED to actually run (default = print plan + exit)

Outputs (per run, isolated under a timestamped subdir):
  supplementary/experiments/outputs/audit_v4/run_<UTC>/observation_packets.jsonl
  supplementary/experiments/outputs/audit_v4/run_<UTC>/run_manifest.json
  supplementary/experiments/outputs/audit_v4/run_<UTC>/progress.json
  supplementary/experiments/outputs/audit_v4/LATEST.txt   (name of newest run)
  supplementary/experiments/outputs/audit_v4/latest       (symlink, where supported)

Resumability:
  --resume <run_dir>   Append to the JSONL in <run_dir>, skipping cells
                       (observer_id, model_id, task_id, tier, trial_idx)
                       already represented there.

Crash safety:
  - Per-packet fsync in both channels.
  - Atomic manifest + progress.json (temp-write then os.replace).
  - SIGINT triggers graceful drain; manifest gets partial: true.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
sys.path.insert(0, str(SCRIPT_DIR))

REPO_ROOT = SCRIPT_PATH.parents[2]
PRE_REG_PATH = SCRIPT_PATH.parents[1] / "PRE_REGISTRATION_AUDIT_OBSERVER_v4.md"
VERIFIER_PATH = SCRIPT_DIR / "code_constraint_verifier.py"

CHANNELS_DIR = SCRIPT_DIR / "channels"
ANTHROPIC_CHANNEL_PATH = CHANNELS_DIR / "anthropic_channel.py"
OPENROUTER_CHANNEL_PATH = CHANNELS_DIR / "openrouter_channel.py"

AUDIT_OBSERVER_PATH_PRIMARY = REPO_ROOT / "ci" / "audit" / "audit_observer.py"
AUDIT_OBSERVER_PATH_FALLBACK = REPO_ROOT / "ci" / "audit" / "observer.py"

OUTPUT_DIR = SCRIPT_DIR / "outputs" / "audit_v4"
# Legacy paths kept for plan-only display + discovery file location.
OBSERVATION_JSONL = OUTPUT_DIR / "observation_packets.jsonl"
RUN_MANIFEST = OUTPUT_DIR / "run_manifest.json"
DISCOVERY_FILE = OUTPUT_DIR / "model_discovery.json"
LATEST_POINTER = OUTPUT_DIR / "LATEST.txt"

# Per-run filenames (live inside the per-run timestamped dir).
RUN_OBSERVATION_FILENAME = "observation_packets.jsonl"
RUN_MANIFEST_FILENAME = "run_manifest.json"
RUN_PROGRESS_FILENAME = "progress.json"

PROGRESS_CHECKPOINT_EVERY_N = 10
PROGRESS_CHECKPOINT_INTERVAL_SEC = 5.0

DEFAULT_TASKS: List[str] = [
    "factorial",
    "fibonacci",
    "is_palindrome",
    "gcd",
    "reverse_words",
    "fizzbuzz",
]
DEFAULT_TIERS: List[str] = ["control", "low", "moderate", "high"]

# Sane fallback model lists (used when no discovery file is present).
DEFAULT_ANTHROPIC_MODELS = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
]
DEFAULT_OPENROUTER_MODELS = [
    "qwen/qwen-2.5-coder-32b-instruct",
]

# Trial regimes
DRY_RUN_TRIALS = 2
DEFAULT_TRIALS = 10
FULL_TRIALS = 15

DEFAULT_CONCURRENCY = 8
ORDER_SEED = 20260504


# ---------------------------------------------------------------------------
# Atomic write + run-dir helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON via temp + os.replace (atomic on same filesystem).

    Survives process death mid-write: the destination file is either fully
    the previous version or fully the new version, never partial.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


_SAFE_TS_RE = re.compile(r"[^0-9A-Za-z\-]")


def _utc_run_stamp() -> str:
    # 2026-05-04T22-30-00Z (filesystem-safe, no colons).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return _SAFE_TS_RE.sub("-", now)


def _new_run_dir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    run_dir = base / f"run_{_utc_run_stamp()}"
    # If a same-second collision somehow happens, append a counter.
    if run_dir.exists():
        i = 1
        while (base / f"run_{_utc_run_stamp()}-{i}").exists():
            i += 1
        run_dir = base / f"run_{_utc_run_stamp()}-{i}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _update_latest_pointer(base: Path, run_dir: Path) -> None:
    """Best-effort symlink to run_dir; always write LATEST.txt fallback."""
    try:
        LATEST_POINTER.write_text(run_dir.name + "\n", encoding="utf-8")
    except Exception as e:  # pragma: no cover
        print(f"  [orchestrator] warning: failed to write LATEST.txt: {e}",
              flush=True)
    latest = base / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            try:
                if latest.is_symlink() or latest.is_file():
                    latest.unlink()
                else:
                    # Don't recursively remove a real directory we may have
                    # created on a prior copy-fallback; leave it alone.
                    return
            except Exception:
                return
        os.symlink(run_dir.name, latest, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        # Windows non-admin / unsupported: LATEST.txt remains the source
        # of truth.
        pass


def _scan_completed_cells(jsonl_path: Path) -> Tuple[Set[Tuple[str, str, str, str, int]], int, int]:
    """Scan an existing JSONL and return (cell-key set, packet count, error count).

    Cell key = (observer_id, model_id, task_id, tier, trial_idx). Malformed
    lines are skipped silently.
    """
    keys: Set[Tuple[str, str, str, str, int]] = set()
    packets = 0
    errors = 0
    if not jsonl_path.exists():
        return keys, 0, 0
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
            except Exception:
                continue
            try:
                k = (
                    str(p["observer_id"]),
                    str(p["model_id"]),
                    str(p["task_id"]),
                    str(p["tier"]),
                    int(p["trial_idx"]),
                )
            except Exception:
                continue
            keys.add(k)
            packets += 1
            if p.get("error"):
                errors += 1
    return keys, packets, errors


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    if not path.exists():
        return f"MISSING:{path.name}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_pre_reg_hash() -> str:
    return sha256_file(PRE_REG_PATH)


def compute_manifest_hash() -> str:
    """sha256 over (pre_reg + orchestrator + both channels + audit observer).

    Ordering is fixed; this hash is the unified manifest stamped into every
    packet emitted by either channel during the run.
    """
    h = hashlib.sha256()
    inputs = [
        PRE_REG_PATH,
        SCRIPT_PATH,
        ANTHROPIC_CHANNEL_PATH,
        OPENROUTER_CHANNEL_PATH,
    ]
    if AUDIT_OBSERVER_PATH_PRIMARY.exists():
        inputs.append(AUDIT_OBSERVER_PATH_PRIMARY)
    elif AUDIT_OBSERVER_PATH_FALLBACK.exists():
        inputs.append(AUDIT_OBSERVER_PATH_FALLBACK)

    for p in inputs:
        if p.exists():
            h.update(p.read_bytes())
        else:
            h.update(f"MISSING:{p.name}".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Run-level provenance
# ---------------------------------------------------------------------------

# Libraries we record version info for. PackageNotFoundError -> "NOT_INSTALLED".
PROVENANCE_LIBRARIES: List[str] = [
    "anthropic",
    "openai",
    "sentence_transformers",
    "torch",
    "scipy",
    "numpy",
    "pytest",
]

# importlib.metadata uses the *distribution* name, which is not always the
# import name. Map import-name -> distribution-name where they diverge.
_DIST_NAME_OVERRIDES: Dict[str, str] = {
    "sentence_transformers": "sentence-transformers",
}

_GIT_REPO_SENTINEL = "NOT_A_GIT_REPO"
_GIT_UNAVAILABLE_SENTINEL = "GIT_UNAVAILABLE"
_ENCODER_HASH_UNAVAILABLE = "ENCODER_HASH_UNAVAILABLE"


def _run_git(args: List[str], cwd: Path) -> Optional[str]:
    """Return stripped stdout of `git <args>` or None on any failure.

    Cross-platform: relies on `git` being on PATH. shell=False on both
    Windows and POSIX. Times out at 5s so a hung git process never blocks
    orchestrator startup.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _collect_git_info(repo_root: Path) -> Dict[str, Any]:
    """Best-effort git provenance. Never raises."""
    # Detect whether we're inside a git work-tree at all.
    inside = _run_git(["rev-parse", "--is-inside-work-tree"], repo_root)
    if inside is None:
        return {
            "git_commit_sha": _GIT_UNAVAILABLE_SENTINEL,
            "git_commit_sha_short": _GIT_UNAVAILABLE_SENTINEL,
            "git_status_clean": False,
            "git_branch": _GIT_UNAVAILABLE_SENTINEL,
        }
    if inside.lower() != "true":
        return {
            "git_commit_sha": _GIT_REPO_SENTINEL,
            "git_commit_sha_short": _GIT_REPO_SENTINEL,
            "git_status_clean": False,
            "git_branch": _GIT_REPO_SENTINEL,
        }

    full = _run_git(["rev-parse", "HEAD"], repo_root) or _GIT_REPO_SENTINEL
    short = _run_git(["rev-parse", "--short=12", "HEAD"], repo_root) or _GIT_REPO_SENTINEL
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root) or _GIT_REPO_SENTINEL
    porcelain = _run_git(["status", "--porcelain"], repo_root)
    # `porcelain` is "" when clean, non-empty when dirty, None on failure.
    if porcelain is None:
        clean = False
    else:
        clean = (porcelain == "")
    return {
        "git_commit_sha": full,
        "git_commit_sha_short": short,
        "git_status_clean": bool(clean),
        "git_branch": branch,
    }


def _collect_library_versions() -> Dict[str, str]:
    """Resolve each library to its installed version, or NOT_INSTALLED."""
    try:
        from importlib.metadata import PackageNotFoundError, version as _ver
    except ImportError:  # pragma: no cover -- py<3.8
        return {name: "IMPORTLIB_METADATA_UNAVAILABLE" for name in PROVENANCE_LIBRARIES}

    out: Dict[str, str] = {}
    for name in PROVENANCE_LIBRARIES:
        dist = _DIST_NAME_OVERRIDES.get(name, name)
        try:
            out[name] = _ver(dist)
        except PackageNotFoundError:
            out[name] = "NOT_INSTALLED"
        except Exception as e:  # pragma: no cover -- defensive
            out[name] = f"LOOKUP_ERROR:{type(e).__name__}"
    return out


def _collect_platform_info() -> Dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
    }


def compute_encoder_state_dict_sha256(encoder: Any) -> str:
    """SHA256 of the encoder's torch state_dict (encoding-agnostic).

    Same model weights -> same hash regardless of on-disk format
    (pytorch_model.bin vs. model.safetensors). Returns a sentinel string
    on any failure rather than raising.
    """
    try:
        import torch  # noqa: F401  (loaded transitively by sentence-transformers)
        # `encoder` here is the local `_Encoder` wrapper; its `.model` is the
        # SentenceTransformer. State dict covers all submodules.
        sd = encoder.model.state_dict()
        buf = io.BytesIO()
        import torch as _torch
        _torch.save(sd, buf)
        return hashlib.sha256(buf.getvalue()).hexdigest()
    except Exception as e:  # pragma: no cover -- defensive
        return f"{_ENCODER_HASH_UNAVAILABLE}:{type(e).__name__}"


def _collect_run_provenance(encoder: Any = None) -> Dict[str, Any]:
    """Gather all run-level provenance fields. Never raises.

    The encoder is optional; when None, encoder_model_file_sha256 is
    populated with a sentinel (encoder hash is computed once at run start
    and cached, then re-injected here).
    """
    info = _collect_git_info(REPO_ROOT)
    if encoder is None:
        encoder_hash = "NOT_LOADED_YET"
    else:
        encoder_hash = compute_encoder_state_dict_sha256(encoder)
    return {
        "git_commit_sha": info["git_commit_sha"],
        "git_commit_sha_short": info["git_commit_sha_short"],
        "git_status_clean": info["git_status_clean"],
        "git_branch": info["git_branch"],
        "encoder_model_file_sha256": encoder_hash,
        "library_versions": _collect_library_versions(),
        "python_version": sys.version,
        "platform_info": _collect_platform_info(),
    }


def load_shared_encoder() -> Tuple[Any, str]:
    """Load the MiniLM encoder once. Returns (encoder, encoder_pkg_version).

    Returns (None, sentinel) on import failure so plan-only mode can still
    print whatever provenance is available.
    """
    try:
        import sentence_transformers
        from channels.anthropic_channel import ENCODER_ID
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except Exception as e:
        print(f"  [orchestrator] warning: encoder load failed at import: {e}",
              flush=True)
        return None, "NOT_INSTALLED"

    class _Encoder:
        def __init__(self) -> None:
            self.model = SentenceTransformer(ENCODER_ID, device="cpu")

        def embed(self, text: str):
            if not text.strip():
                return np.zeros(384, dtype=float)
            return self.model.encode(
                [text], show_progress_bar=False, normalize_embeddings=False
            )[0]

    try:
        enc = _Encoder()
    except Exception as e:
        print(f"  [orchestrator] warning: encoder instantiation failed: {e}",
              flush=True)
        return None, getattr(sentence_transformers, "__version__", "unknown")
    return enc, getattr(sentence_transformers, "__version__", "unknown")


def print_provenance_summary(prov: Dict[str, Any]) -> None:
    """One-screen summary of the provenance dict for the orchestrator log."""
    print("-" * 72)
    print("Run provenance:")
    print(f"  git_commit_sha (short): {prov.get('git_commit_sha_short', '?')}")
    print(f"  git_branch:             {prov.get('git_branch', '?')}")
    print(f"  git_status_clean:       {prov.get('git_status_clean', '?')}")
    enc = str(prov.get("encoder_model_file_sha256", "?"))
    print(f"  encoder_model_sha256:   {enc[:16]}...")
    libs = prov.get("library_versions", {})
    if libs:
        print("  library versions:")
        for name, ver in libs.items():
            print(f"      - {name}: {ver}")
    print(f"  python_version:         {prov.get('python_version', '?').splitlines()[0]}")
    plat = prov.get("platform_info", {})
    if plat:
        print(f"  platform:               {plat.get('system','?')} {plat.get('release','?')} "
              f"({plat.get('machine','?')}, {plat.get('python_implementation','?')})")
    print("-" * 72)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_models_from_discovery(path: Path) -> Optional[Dict[str, List[str]]]:
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    anth = (d.get("anthropic") or {}).get("available") or []
    # Prefer the curated `recommended` subset over the full code-oriented
    # list (which can include 70+ models on OpenRouter).
    or_block = d.get("openrouter") or {}
    orr = or_block.get("recommended") or or_block.get("available_code_oriented") or []
    if not anth and not orr:
        return None
    return {"anthropic": anth, "openrouter": orr}


def load_config(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Plan + cost estimate
# ---------------------------------------------------------------------------

def cells_count(models: List[str], tasks: List[str], tiers: List[str], trials: int) -> int:
    return len(models) * len(tasks) * len(tiers) * trials


# Rough average tokens per trial (in + out) used for high-level cost estimate.
# Inputs ~ 200 tokens (prompt). Outputs ~ 250 tokens (single function).
APPROX_IN_TOK_PER_TRIAL = 200
APPROX_OUT_TOK_PER_TRIAL = 250

# Pull pricing from channel modules (lazy import to avoid heavy deps at parse).
def _approx_cost_anthropic(models: List[str], n_trials: int) -> float:
    from channels.anthropic_channel import PRICING_PER_MTOK, DEFAULT_PRICING
    if not models:
        return 0.0
    avg_in = 0.0
    avg_out = 0.0
    for m in models:
        p = PRICING_PER_MTOK.get(m, DEFAULT_PRICING)
        avg_in += p["in"]
        avg_out += p["out"]
    avg_in /= len(models)
    avg_out /= len(models)
    cost_per_trial = (
        APPROX_IN_TOK_PER_TRIAL * avg_in + APPROX_OUT_TOK_PER_TRIAL * avg_out
    ) / 1_000_000.0
    return cost_per_trial * n_trials


def _approx_cost_openrouter(models: List[str], n_trials: int) -> float:
    from channels.openrouter_channel import (
        PRICING_PER_MTOK,
        PRICING_PER_MTOK_DEFAULT,
    )
    if not models:
        return 0.0
    avg_in = 0.0
    avg_out = 0.0
    for m in models:
        p = PRICING_PER_MTOK.get(m, PRICING_PER_MTOK_DEFAULT)
        avg_in += p["in"]
        avg_out += p["out"]
    avg_in /= len(models)
    avg_out /= len(models)
    cost_per_trial = (
        APPROX_IN_TOK_PER_TRIAL * avg_in + APPROX_OUT_TOK_PER_TRIAL * avg_out
    ) / 1_000_000.0
    return cost_per_trial * n_trials


# ---------------------------------------------------------------------------
# Progress reporter (live ticker while channels run)
# ---------------------------------------------------------------------------

async def progress_reporter(progress: Dict[str, Any], stop_evt: asyncio.Event,
                            interval_sec: float = 5.0,
                            progress_path: Optional[Path] = None,
                            started_utc: Optional[str] = None,
                            packets_target: int = 0) -> None:
    while not stop_evt.is_set():
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass
        a_done = progress.get("anthropic_done", 0)
        a_tot = progress.get("anthropic_total", 0)
        a_cost = progress.get("anthropic_cost", 0.0)
        a_err = progress.get("anthropic_errors", 0)
        o_done = progress.get("openrouter_done", 0)
        o_tot = progress.get("openrouter_total", 0)
        o_cost = progress.get("openrouter_cost", 0.0)
        o_err = progress.get("openrouter_errors", 0)
        total_cost = a_cost + o_cost
        print(
            f"  [progress] Anthropic: {a_done}/{a_tot} done; "
            f"OpenRouter: {o_done}/{o_tot} done; "
            f"total cost so far: ${total_cost:.2f}",
            flush=True,
        )
        if progress_path is not None:
            try:
                _atomic_write_json(progress_path, {
                    "started_utc": started_utc,
                    "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                    "packets_completed": int(a_done) + int(o_done),
                    "packets_target": int(packets_target),
                    "errors": int(a_err) + int(o_err),
                    "anthropic_done": int(a_done),
                    "openrouter_done": int(o_done),
                    "estimated_cost_usd": round(float(total_cost), 4),
                })
            except Exception as e:  # pragma: no cover
                print(f"  [orchestrator] warning: progress.json write failed: {e}",
                      flush=True)


def _make_sigint_handler(stop_evt: asyncio.Event):
    state = {"count": 0}

    def _handler(signum, frame):  # noqa: ARG001
        state["count"] += 1
        if state["count"] >= 2:
            print("\n[orchestrator] second SIGINT received; hard-exiting", flush=True)
            sys.exit(130)
        print("\n[orchestrator] SIGINT received; draining in-flight requests "
              "(Ctrl+C again to hard-exit)...", flush=True)
        # asyncio.Event.set is thread-safe enough for the simple flag use here.
        stop_evt.set()
    return _handler


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

async def run_unified(
    anthropic_models: List[str],
    openrouter_models: List[str],
    tasks: List[str],
    tiers: List[str],
    trials_per_cell: int,
    concurrency: int,
    run_dir: Path,
    skip_cells: Optional[Set[Tuple[str, str, str, str, int]]] = None,
    resumed: bool = False,
    prior_packets: int = 0,
    prior_errors: int = 0,
    encoder: Any = None,
    encoder_pkg_version: Optional[str] = None,
    run_provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from channels.anthropic_channel import run_anthropic_channel
    from channels.openrouter_channel import run_openrouter_channel

    output_path = run_dir / RUN_OBSERVATION_FILENAME
    manifest_path = run_dir / RUN_MANIFEST_FILENAME
    progress_path = run_dir / RUN_PROGRESS_FILENAME
    skip_cells = skip_cells or set()

    # Shared encoder (load once, used by both channels). If caller didn't
    # supply one (legacy call), load it now.
    if encoder is None:
        print("Loading MiniLM encoder (shared across channels) ...", flush=True)
        encoder, loaded_ver = load_shared_encoder()
        if encoder_pkg_version is None:
            encoder_pkg_version = loaded_ver
    if encoder_pkg_version is None:
        encoder_pkg_version = "unknown"

    pre_reg_hash = compute_pre_reg_hash()
    manifest_hash = compute_manifest_hash()
    verifier_hash = sha256_file(VERIFIER_PATH)

    print(f"  pre_reg_hash:  {pre_reg_hash[:16]}...", flush=True)
    print(f"  verifier_hash: {verifier_hash[:16]}...", flush=True)
    print(f"  manifest_hash: {manifest_hash[:16]}...", flush=True)

    # Run-level provenance: prefer the dict caller computed (cheap reuse of
    # the already-cached encoder hash); else compute now.
    if run_provenance is None:
        run_provenance = _collect_run_provenance(encoder=encoder)
        print_provenance_summary(run_provenance)

    # Per-run dir already exists (created by caller). Do NOT wipe — resume
    # mode appends to the existing observation_packets.jsonl.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists():
        output_path.touch()

    shared_lock = asyncio.Lock()
    progress: Dict[str, Any] = {
        "anthropic_total": 0, "anthropic_done": 0,
        "anthropic_errors": 0, "anthropic_cost": 0.0,
        "openrouter_total": 0, "openrouter_done": 0,
        "openrouter_errors": 0, "openrouter_cost": 0.0,
    }

    started_utc = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    stop_evt = asyncio.Event()
    # SIGINT: first hit triggers graceful drain; second hit hard-exits.
    try:
        signal.signal(signal.SIGINT, _make_sigint_handler(stop_evt))
    except (ValueError, OSError):  # pragma: no cover -- non-main thread
        pass

    # Target packets for progress.json = total cells across both channels.
    target_packets = (
        len(anthropic_models) * len(tasks) * len(tiers) * trials_per_cell
        + len(openrouter_models) * len(tasks) * len(tiers) * trials_per_cell
    )
    reporter_task = asyncio.create_task(progress_reporter(
        progress, stop_evt,
        interval_sec=PROGRESS_CHECKPOINT_INTERVAL_SEC,
        progress_path=progress_path,
        started_utc=started_utc,
        packets_target=target_packets,
    ))

    coros = []
    if anthropic_models:
        coros.append(run_anthropic_channel(
            models=anthropic_models,
            tasks=tasks,
            tiers=tiers,
            trials_per_cell=trials_per_cell,
            encoder=encoder,
            output_path=output_path,
            pre_reg_hash=pre_reg_hash,
            manifest_hash=manifest_hash,
            verifier_hash=verifier_hash,
            encoder_pkg_version=encoder_pkg_version,
            shared_jsonl_lock=shared_lock,
            progress=progress,
            concurrency=concurrency,
            order_seed=ORDER_SEED,
            stop_evt=stop_evt,
            skip_cells=skip_cells,
        ))
    if openrouter_models:
        coros.append(run_openrouter_channel(
            models=openrouter_models,
            tasks=tasks,
            tiers=tiers,
            trials_per_cell=trials_per_cell,
            encoder=encoder,
            output_path=output_path,
            pre_reg_hash=pre_reg_hash,
            manifest_hash=manifest_hash,
            verifier_hash=verifier_hash,
            encoder_pkg_version=encoder_pkg_version,
            shared_jsonl_lock=shared_lock,
            progress=progress,
            concurrency=concurrency,
            order_seed=ORDER_SEED,
            stop_evt=stop_evt,
            skip_cells=skip_cells,
        ))

    results = await asyncio.gather(*coros, return_exceptions=True)
    stop_evt.set()
    await reporter_task

    finished_utc = datetime.now(timezone.utc).isoformat()
    elapsed = time.time() - t0

    errors_summary: List[str] = []
    a_summary = None
    o_summary = None
    idx = 0
    if anthropic_models:
        r = results[idx]; idx += 1
        if isinstance(r, Exception):
            errors_summary.append(f"anthropic_channel: {type(r).__name__}: {r}")
        else:
            a_summary = r
    if openrouter_models:
        r = results[idx]; idx += 1
        if isinstance(r, Exception):
            errors_summary.append(f"openrouter_channel: {type(r).__name__}: {r}")
        else:
            o_summary = r

    total_packets = (a_summary["completed"] if a_summary else 0) + \
                    (o_summary["completed"] if o_summary else 0)
    total_cost = (a_summary["cost_usd"] if a_summary else 0.0) + \
                 (o_summary["cost_usd"] if o_summary else 0.0)
    # Include prior run's contributions on resume so the manifest reflects
    # everything currently on disk in this run_dir.
    total_packets_on_disk = total_packets + int(prior_packets)
    total_errors_on_disk = (
        (a_summary["errors"] if a_summary else 0)
        + (o_summary["errors"] if o_summary else 0)
        + int(prior_errors)
    )

    partial = stop_evt.is_set() and any(
        isinstance(r, dict) and r.get("partial") for r in (a_summary, o_summary) if r
    )
    # Conservative fallback: if SIGINT was raised, mark partial.
    if stop_evt.is_set():
        partial = True

    manifest = {
        "pre_reg_hash": pre_reg_hash,
        "manifest_hash": manifest_hash,
        "verifier_hash": verifier_hash,
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "elapsed_sec": round(elapsed, 1),
        "run_dir": str(run_dir),
        "resumed": bool(resumed),
        "partial": bool(partial),
        "prior_packets_on_resume": int(prior_packets),
        "models_used": {
            "anthropic": anthropic_models,
            "openrouter": openrouter_models,
        },
        "tasks": tasks,
        "tiers": tiers,
        "trials_per_cell": trials_per_cell,
        "concurrency": concurrency,
        "order_seed": ORDER_SEED,
        "total_packets_emitted_this_session": total_packets,
        "total_packets_on_disk": total_packets_on_disk,
        "total_errors_on_disk": total_errors_on_disk,
        "anthropic_summary": a_summary,
        "openrouter_summary": o_summary,
        "estimated_cost_usd": round(total_cost, 4),
        "errors": errors_summary,
        "output_jsonl": str(output_path),
        # Run-level provenance (added v4-additive). See PRE_REGISTRATION
        # §11 for the field list.
        "git_commit_sha": run_provenance["git_commit_sha"],
        "git_commit_sha_short": run_provenance["git_commit_sha_short"],
        "git_status_clean": run_provenance["git_status_clean"],
        "git_branch": run_provenance["git_branch"],
        "encoder_model_file_sha256": run_provenance["encoder_model_file_sha256"],
        "library_versions": run_provenance["library_versions"],
        "python_version": run_provenance["python_version"],
        "platform_info": run_provenance["platform_info"],
    }
    _atomic_write_json(manifest_path, manifest)
    if not partial:
        _update_latest_pointer(OUTPUT_DIR, run_dir)
    print("\n" + "=" * 72)
    print("Unified audit run complete." + ("  [PARTIAL]" if partial else ""))
    print(f"  packets (this session): {total_packets}")
    print(f"  packets on disk:        {total_packets_on_disk}")
    print(f"  cost (estimate):        ${total_cost:.2f}")
    print(f"  run_dir:                {run_dir}")
    print(f"  jsonl:                  {output_path}")
    print(f"  manifest:               {manifest_path}")
    if errors_summary:
        print("  errors:")
        for e in errors_summary:
            print(f"    - {e}")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_plan(
    anthropic_models: List[str],
    openrouter_models: List[str],
    tasks: List[str],
    tiers: List[str],
    trials_per_cell: int,
    concurrency: int,
    pre_reg_hash: str,
    manifest_hash: str,
) -> None:
    a_cells = cells_count(anthropic_models, tasks, tiers, trials_per_cell)
    o_cells = cells_count(openrouter_models, tasks, tiers, trials_per_cell)
    a_cost = _approx_cost_anthropic(anthropic_models, a_cells)
    o_cost = _approx_cost_openrouter(openrouter_models, o_cells)

    print("=" * 72)
    print("Audit v4 Unified Orchestrator -- PLANNED CONFIGURATION")
    print("=" * 72)
    print(f"  pre_reg_hash:   {pre_reg_hash}")
    print(f"  manifest_hash:  {manifest_hash}")
    print()
    print(f"  trials per cell:  {trials_per_cell}")
    print(f"  concurrency:      {concurrency} (per channel)")
    print(f"  order seed:       {ORDER_SEED}")
    print(f"  tasks  ({len(tasks)}):  {', '.join(tasks)}")
    print(f"  tiers  ({len(tiers)}):  {', '.join(tiers)}")
    print()
    print(f"  Anthropic channel  (B_claude):     {len(anthropic_models)} models")
    for m in anthropic_models:
        print(f"      - {m}")
    print(f"      cells: {a_cells}; estimated cost: ${a_cost:.2f}")
    print()
    print(f"  OpenRouter channel (B_openweight): {len(openrouter_models)} models")
    for m in openrouter_models:
        print(f"      - {m}")
    print(f"      cells: {o_cells}; estimated cost: ${o_cost:.2f}")
    print()
    print(f"  TOTAL trials:   {a_cells + o_cells}")
    print(f"  TOTAL est cost: ${a_cost + o_cost:.2f}")
    print()
    print(f"  output jsonl:   {OBSERVATION_JSONL}")
    print(f"  run manifest:   {RUN_MANIFEST}")
    print("=" * 72)


def main() -> int:
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help=f"{DRY_RUN_TRIALS} trials per cell (fast validation)")
    mode.add_argument("--default", dest="default_mode", action="store_true",
                      help=f"{DEFAULT_TRIALS} trials per cell (current pre-reg sample)")
    mode.add_argument("--full", action="store_true",
                      help=f"{FULL_TRIALS} trials per cell (production run)")

    p.add_argument("--config", type=Path, default=None,
                   help="Optional JSON config: overrides model lists / sample size")
    p.add_argument("--anthropic-models", type=str, default=None,
                   help="Comma-separated Anthropic model ids (overrides discovery + defaults)")
    p.add_argument("--openrouter-models", type=str, default=None,
                   help="Comma-separated OpenRouter model ids (overrides discovery + defaults)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help="Per-channel concurrency cap")
    p.add_argument("--output", type=Path, default=None,
                   help="Override per-run JSONL path. Default: a new "
                        "timestamped run_dir under outputs/audit_v4/.")
    p.add_argument("--resume", type=Path, default=None,
                   help="Resume an existing run_dir; appends to its "
                        "observation_packets.jsonl, skipping completed cells.")
    p.add_argument("--go", action="store_true",
                   help="REQUIRED to actually launch API calls. Without this, "
                        "orchestrator prints plan and exits.")
    args = p.parse_args()

    # Resolve trials_per_cell
    if args.dry_run:
        trials_per_cell = DRY_RUN_TRIALS
    elif args.full:
        trials_per_cell = FULL_TRIALS
    else:
        # default_mode or no mode flag
        trials_per_cell = DEFAULT_TRIALS

    # Resolve model lists: precedence = CLI flag > config file > discovery file > defaults
    anthropic_models = list(DEFAULT_ANTHROPIC_MODELS)
    openrouter_models = list(DEFAULT_OPENROUTER_MODELS)
    tasks = list(DEFAULT_TASKS)
    tiers = list(DEFAULT_TIERS)

    discovery = load_models_from_discovery(DISCOVERY_FILE)
    if discovery:
        if discovery.get("anthropic"):
            anthropic_models = discovery["anthropic"]
        if discovery.get("openrouter"):
            openrouter_models = discovery["openrouter"]

    if args.config:
        cfg = load_config(args.config)
        if "anthropic_models" in cfg:
            anthropic_models = cfg["anthropic_models"]
        if "openrouter_models" in cfg:
            openrouter_models = cfg["openrouter_models"]
        if "tasks" in cfg:
            tasks = cfg["tasks"]
        if "tiers" in cfg:
            tiers = cfg["tiers"]
        if "trials_per_cell" in cfg:
            trials_per_cell = int(cfg["trials_per_cell"])

    if args.anthropic_models is not None:
        anthropic_models = [s.strip() for s in args.anthropic_models.split(",") if s.strip()]
    if args.openrouter_models is not None:
        openrouter_models = [s.strip() for s in args.openrouter_models.split(",") if s.strip()]

    pre_reg_hash = compute_pre_reg_hash()
    manifest_hash = compute_manifest_hash()

    print_plan(
        anthropic_models=anthropic_models,
        openrouter_models=openrouter_models,
        tasks=tasks,
        tiers=tiers,
        trials_per_cell=trials_per_cell,
        concurrency=args.concurrency,
        pre_reg_hash=pre_reg_hash,
        manifest_hash=manifest_hash,
    )

    # Run-level provenance (collected once at orchestrator start; encoder
    # hash is the bottleneck, so cache the encoder + provenance dict and
    # reuse for every channel via run_unified).
    print("\nLoading MiniLM encoder for provenance hash "
          "(this is also the encoder shared by both channels) ...", flush=True)
    encoder, encoder_pkg_version = load_shared_encoder()
    run_provenance = _collect_run_provenance(encoder=encoder)
    print_provenance_summary(run_provenance)

    # Resolve run_dir (resume vs. new) BEFORE the --go gate so plan-only
    # mode can also display the path that *would* be created.
    skip_cells: Set[Tuple[str, str, str, str, int]] = set()
    prior_packets = 0
    prior_errors = 0
    resumed = False
    if args.resume is not None:
        resume_dir = args.resume.resolve()
        if not resume_dir.exists() or not resume_dir.is_dir():
            print(f"ERROR: --resume path does not exist or is not a directory: "
                  f"{resume_dir}", file=sys.stderr)
            return 2
        run_dir = resume_dir
        resumed = True
        existing_jsonl = run_dir / RUN_OBSERVATION_FILENAME
        skip_cells, prior_packets, prior_errors = _scan_completed_cells(existing_jsonl)
        print(f"\n[orchestrator] RESUMING run_dir: {run_dir}")
        print(f"  existing packets on disk: {prior_packets} "
              f"({prior_errors} with errors)")
        print(f"  cells to skip:            {len(skip_cells)}")
    else:
        # Defer actual dir creation until --go to avoid accumulating empty
        # dirs from repeated plan-only runs. Compute the prospective name
        # now for display.
        prospective_name = f"run_{_utc_run_stamp()}"
        run_dir = OUTPUT_DIR / prospective_name
        if args.go:
            run_dir = _new_run_dir(OUTPUT_DIR)
        print(f"\n[orchestrator] new run_dir: {run_dir}"
              + ("" if args.go else "  (will be created on --go)"))

    # Optional override: caller asked for an explicit JSONL path. We honour
    # it only when not resuming; otherwise the per-run layout is canonical.
    if args.output is not None and not resumed:
        # Treat --output as the destination JSONL; place its sibling
        # manifest/progress files alongside it inside its parent dir.
        run_dir = args.output.parent
        run_dir.mkdir(parents=True, exist_ok=True)
        # Ensure the JSONL filename matches what run_unified expects.
        if args.output.name != RUN_OBSERVATION_FILENAME:
            print(f"  [orchestrator] note: --output filename will be "
                  f"normalized to {RUN_OBSERVATION_FILENAME} inside "
                  f"{run_dir}", flush=True)

    if not args.go:
        print("\n--go not provided. Plan-only mode; no API calls launched.")
        print(f"  would write to: {run_dir / RUN_OBSERVATION_FILENAME}")
        print("Add --go to actually execute. Exiting.")
        return 0

    if not anthropic_models and not openrouter_models:
        print("ERROR: no models selected. Aborting.", file=sys.stderr)
        return 2

    asyncio.run(run_unified(
        anthropic_models=anthropic_models,
        openrouter_models=openrouter_models,
        tasks=tasks,
        tiers=tiers,
        trials_per_cell=trials_per_cell,
        concurrency=args.concurrency,
        run_dir=run_dir,
        skip_cells=skip_cells,
        resumed=resumed,
        prior_packets=prior_packets,
        prior_errors=prior_errors,
        encoder=encoder,
        encoder_pkg_version=encoder_pkg_version,
        run_provenance=run_provenance,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
