#!/usr/bin/env python3
"""Extended environment fingerprint for reviewer reproducibility.

Captures detailed OS/CPU/RAM/locale/timezone/Python-internal info BEYOND
the basic ``platform_info`` already recorded by the run-level provenance
agent. Pure stdlib, cross-platform (Windows + POSIX), no PII leaks.

Public API
----------
    capture_extended_fingerprint(output_dir: Optional[Path] = None)
        -> ExtendedEnvironmentFingerprint

Companion CLI
-------------
    python supplementary/experiments/extended_environment_fingerprint.py
        Prints the fingerprint as JSON on stdout.

Integration note
----------------
This module is standalone. Downstream callers (e.g. the orchestrator's
``_collect_run_provenance``) may attach the fingerprint dict to the run
manifest under an ``extended_environment_fingerprint`` key. We do NOT
modify the orchestrator from here.
"""
from __future__ import annotations

import ctypes
import dataclasses
import json
import locale
import os
import platform
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# PII scrubbing (replicated inline from code_constraint_verifier._scrub_paths
# to avoid a cross-module import; the regex set must stay in sync).
# ---------------------------------------------------------------------------
_TEMP_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\Users\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"/Users/[A-Za-z0-9_.\-]+(?:/[^\"'\s\n,]*)?", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_.\-]+(?:/[^\"'\s\n,]*)?", re.IGNORECASE),
    re.compile(r"/tmp/[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"/var/folders/[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\\\src\\\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\src\\[^\"'\s\n,]+", re.IGNORECASE),
]


def _scrub_username(text: str) -> str:
    """Replace any user/temp paths with ``<scrubbed>`` placeholders.

    Mirrors ``code_constraint_verifier._scrub_paths`` semantics. We do not
    import that function to keep this module dependency-free across the
    experiments package layout.
    """
    if not text:
        return text
    for pat in _TEMP_PATH_PATTERNS:
        text = pat.sub("<scrubbed>", text)
    return text


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExtendedEnvironmentFingerprint:
    """Comprehensive environment fingerprint for reviewer reproducibility."""

    # OS-level
    os_system: str
    os_release: str
    os_version: str
    os_platform: str

    # CPU
    cpu_machine: str
    cpu_processor: str
    cpu_count_logical: int
    cpu_count_physical: Optional[int]

    # Memory
    ram_total_mb: Optional[int]

    # Disk
    disk_free_gb_at_output_dir: Optional[float]

    # Locale + timezone
    locale_default: str
    locale_encoding: str
    timezone_name: str
    timezone_utc_offset_seconds: int

    # Python
    python_version_full: str
    python_implementation: str
    python_executable: str
    python_max_int: int
    python_float_info: Dict[str, Any] = field(default_factory=dict)

    # Determinism
    pythonhashseed: Optional[str] = None
    pythonutf8: Optional[str] = None

    # Run context
    captured_at_utc: str = ""


# ---------------------------------------------------------------------------
# RAM total (cross-platform, best-effort)
# ---------------------------------------------------------------------------
class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _ram_total_mb() -> Optional[int]:
    """Return total physical RAM in MB, or None if undeterminable."""
    try:
        if sys.platform.startswith("win"):
            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            if not ok:
                return None
            return int(stat.ullTotalPhys // (1024 * 1024))
        # POSIX
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        return int((page_size * phys_pages) // (1024 * 1024))
    except Exception:
        return None


def _cpu_count_physical() -> Optional[int]:
    """Best-effort physical core count without external deps.

    ``os.cpu_count()`` returns logical count only. We try ``psutil`` if it
    is happens to already be importable, otherwise None (per the brief:
    no new deps).
    """
    try:
        import psutil  # type: ignore  # noqa: F401

        return int(psutil.cpu_count(logical=False) or 0) or None
    except Exception:
        return None


def _disk_free_gb(output_dir: Optional[Path]) -> Optional[float]:
    """Return free disk space (GB) at ``output_dir`` (or its parent)."""
    if output_dir is None:
        return None
    try:
        p = Path(output_dir)
        # If path doesn't exist, walk up to nearest existing ancestor.
        probe = p if p.exists() else next(
            (anc for anc in p.parents if anc.exists()), Path(".")
        )
        usage = shutil.disk_usage(str(probe))
        return float(usage.free) / (1024 ** 3)
    except Exception:
        return None


def _locale_pair() -> tuple[str, str]:
    """Return (locale_default, locale_encoding) with safe fallbacks."""
    try:
        # ``getlocale`` is preferred on 3.12+; ``getdefaultlocale`` is
        # deprecated but still works. We fall back through both.
        try:
            lang, enc = locale.getlocale()
        except Exception:
            lang, enc = (None, None)
        if not lang or not enc:
            try:
                lang2, enc2 = locale.getdefaultlocale()  # type: ignore[attr-defined]
                lang = lang or lang2
                enc = enc or enc2
            except Exception:
                pass
    except Exception:
        lang, enc = (None, None)
    return (lang or "C", enc or sys.getdefaultencoding())


def _tz_offset_seconds() -> int:
    """UTC offset in seconds for the local timezone (DST-aware best-effort)."""
    try:
        local_now = datetime.now().astimezone()
        off = local_now.utcoffset()
        if off is not None:
            return int(off.total_seconds())
    except Exception:
        pass
    # Fallback: time.timezone is "seconds west of UTC" — invert sign so
    # the value matches the datetime convention (east of UTC = positive).
    return -int(time.timezone)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def capture_extended_fingerprint(
    output_dir: Optional[Path] = None,
) -> ExtendedEnvironmentFingerprint:
    """Capture comprehensive environment info.

    Parameters
    ----------
    output_dir:
        If provided, ``disk_free_gb_at_output_dir`` is computed via
        ``shutil.disk_usage`` against this directory (or nearest existing
        ancestor). Pass ``None`` to skip the disk check.
    """
    lang, enc = _locale_pair()
    float_info = {
        "max": sys.float_info.max,
        "min": sys.float_info.min,
        "epsilon": sys.float_info.epsilon,
        "dig": sys.float_info.dig,
        "mant_dig": sys.float_info.mant_dig,
        "max_exp": sys.float_info.max_exp,
        "min_exp": sys.float_info.min_exp,
        "max_10_exp": sys.float_info.max_10_exp,
        "min_10_exp": sys.float_info.min_10_exp,
        "radix": sys.float_info.radix,
        "rounds": sys.float_info.rounds,
    }
    return ExtendedEnvironmentFingerprint(
        os_system=platform.system(),
        os_release=platform.release(),
        os_version=platform.version(),
        os_platform=platform.platform(),
        cpu_machine=platform.machine(),
        cpu_processor=platform.processor(),
        cpu_count_logical=int(os.cpu_count() or 1),
        cpu_count_physical=_cpu_count_physical(),
        ram_total_mb=_ram_total_mb(),
        disk_free_gb_at_output_dir=_disk_free_gb(output_dir),
        locale_default=lang,
        locale_encoding=enc,
        timezone_name=time.tzname[0] if time.tzname else "UTC",
        timezone_utc_offset_seconds=_tz_offset_seconds(),
        python_version_full=sys.version,
        python_implementation=platform.python_implementation(),
        python_executable=_scrub_username(sys.executable),
        python_max_int=sys.maxsize,
        python_float_info=float_info,
        pythonhashseed=os.environ.get("PYTHONHASHSEED"),
        pythonutf8=os.environ.get("PYTHONUTF8"),
        captured_at_utc=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main() -> int:
    fp = capture_extended_fingerprint(output_dir=Path.cwd())
    print(json.dumps(dataclasses.asdict(fp), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
