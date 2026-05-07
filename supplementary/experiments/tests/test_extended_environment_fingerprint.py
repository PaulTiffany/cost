"""Tests for ``extended_environment_fingerprint``.

The module is a standalone provenance helper: pure stdlib, no PII leaks,
cross-platform. These tests exercise the public surface and the PII guard.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Make the sibling experiment module importable without depending on the
# experiments package layout (matches the convention used by the other
# tests in this directory).
EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

import extended_environment_fingerprint as eef  # noqa: E402


# Same scrub patterns as the module under test (so the test is independent
# of whatever scrubber implementation eef chose internally).
_SCRUB_PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\Users\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"/Users/[A-Za-z0-9_.\-]+(?:/[^\"'\s\n,]*)?", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_.\-]+(?:/[^\"'\s\n,]*)?", re.IGNORECASE),
]


def test_fingerprint_captures_basic_os_info():
    fp = eef.capture_extended_fingerprint()
    assert isinstance(fp.os_system, str) and fp.os_system
    assert isinstance(fp.os_release, str) and fp.os_release
    assert isinstance(fp.os_platform, str) and fp.os_platform


def test_fingerprint_captures_cpu_count():
    fp = eef.capture_extended_fingerprint()
    assert isinstance(fp.cpu_count_logical, int)
    assert fp.cpu_count_logical >= 1


def test_fingerprint_captures_ram_or_none():
    # Must not raise; either positive int or None on unsupported platforms.
    fp = eef.capture_extended_fingerprint()
    assert fp.ram_total_mb is None or (
        isinstance(fp.ram_total_mb, int) and fp.ram_total_mb > 0
    )


def test_fingerprint_captures_disk_free_for_existing_dir(tmp_path):
    fp = eef.capture_extended_fingerprint(output_dir=tmp_path)
    assert isinstance(fp.disk_free_gb_at_output_dir, float)
    assert fp.disk_free_gb_at_output_dir > 0.0


def test_fingerprint_python_executable_is_scrubbed_of_username():
    fp = eef.capture_extended_fingerprint()
    exe = fp.python_executable
    # Anonymity check: no raw author username, no raw user-path prefix.
    assert "<redacted_user>" not in exe.lower()
    for pat in _SCRUB_PATTERNS:
        assert not pat.search(exe), (
            f"python_executable still contains a user-path fingerprint: {exe!r}"
        )


def test_fingerprint_captured_at_utc_is_iso8601():
    fp = eef.capture_extended_fingerprint()
    # Must round-trip through fromisoformat without raising.
    parsed = datetime.fromisoformat(fp.captured_at_utc)
    assert parsed is not None


def test_fingerprint_is_serializable_to_json():
    fp = eef.capture_extended_fingerprint()
    raw = json.dumps(dataclasses.asdict(fp))
    restored = json.loads(raw)
    assert restored["os_system"] == fp.os_system
    assert restored["cpu_count_logical"] == fp.cpu_count_logical
    assert restored["python_implementation"] == fp.python_implementation


def test_fingerprint_dataclass_is_frozen():
    fp = eef.capture_extended_fingerprint()
    with pytest.raises(dataclasses.FrozenInstanceError):
        fp.os_system = "tampered"  # type: ignore[misc]
