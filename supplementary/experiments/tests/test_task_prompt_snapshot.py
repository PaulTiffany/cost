#!/usr/bin/env python3
"""Tests for task_prompt_snapshot.py."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Make the experiments package importable.
EXP_DIR = Path(__file__).resolve().parent.parent
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

from code_constraint_tasks import TASKS  # noqa: E402
from code_constraint_verifier import (  # noqa: E402
    FORMAT_TIERS,
    format_rules_to_prompt,
)
import task_prompt_snapshot as tps  # noqa: E402


# ---------------------------------------------------------------------------
# Snapshot content tests
# ---------------------------------------------------------------------------

def test_snapshot_contains_24_resolved_prompts():
    """6 audit tasks x 4 tiers = 24 resolved prompts."""
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    assert snap["_meta"]["task_count"] == 6
    assert snap["_meta"]["tier_count"] == 4
    assert snap["_meta"]["total_resolved_prompts"] == 24
    assert len(snap["resolved_prompts"]) == 24
    # Each combination must appear exactly once.
    pairs = {(e["task_id"], e["tier"]) for e in snap["resolved_prompts"]}
    assert len(pairs) == 24


def test_snapshot_prompt_hash_matches_format_used_by_channels():
    """Build a prompt directly, compare SHA against snapshot value."""
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    tasks_by_id = {t.task_id: t for t in TASKS}

    # Spot-check every snapshot entry.
    for entry in snap["resolved_prompts"]:
        task = tasks_by_id[entry["task_id"]]
        rules = FORMAT_TIERS[entry["tier"]]
        rules_text = format_rules_to_prompt(rules)
        expected_text = (
            f"{task.description}\n\n"
            f"Constraints:\n{rules_text}\n\n"
            f"Return only the function in a single ```python ... ``` code block."
        )
        expected_hash = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        assert entry["prompt_text"] == expected_text, (
            f"prompt_text mismatch for {entry['task_id']}/{entry['tier']}"
        )
        assert entry["prompt_sha256"] == expected_hash, (
            f"prompt_sha256 mismatch for {entry['task_id']}/{entry['tier']}"
        )


def test_snapshot_includes_source_file_hashes():
    """_meta must include populated SHA256 of the two source files."""
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    meta = snap["_meta"]
    tasks_path = EXP_DIR / "code_constraint_tasks.py"
    verifier_path = EXP_DIR / "code_constraint_verifier.py"

    expected_tasks_sha = tps.sha256_file(tasks_path)
    expected_verifier_sha = tps.sha256_file(verifier_path)

    assert meta["code_constraint_tasks_sha256"] == expected_tasks_sha
    assert meta["code_constraint_verifier_sha256"] == expected_verifier_sha
    # Sanity: SHA256 hex is 64 chars.
    assert len(meta["code_constraint_tasks_sha256"]) == 64
    assert len(meta["code_constraint_verifier_sha256"]) == 64


def test_snapshot_is_idempotent_modulo_ran_at(tmp_path):
    """Re-running with the same ran_at_utc produces identical bytes."""
    out1 = tmp_path / "snap1.json"
    out2 = tmp_path / "snap2.json"
    fixed_ts = "2026-01-01T00:00:00+00:00"
    snap = tps.build_snapshot(ran_at_utc=fixed_ts)
    tps.write_snapshot(out1, snap)
    tps.write_snapshot(out2, snap)
    assert out1.read_bytes() == out2.read_bytes()


def test_snapshot_tasks_section_has_six_audit_ids():
    """The 'tasks' section must contain exactly the 6 audit task IDs."""
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    assert set(snap["tasks"].keys()) == set(tps.AUDIT_TASK_IDS)
    for tid, task_dict in snap["tasks"].items():
        assert task_dict["task_id"] == tid
        assert "test_code_sha256" in task_dict
        assert len(task_dict["test_code_sha256"]) == 64


def test_snapshot_tiers_section_has_four_tiers():
    """The 'tiers' section must contain control/low/moderate/high."""
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    assert set(snap["tiers"].keys()) == {"control", "low", "moderate", "high"}
    for tname, tier_dict in snap["tiers"].items():
        assert tier_dict["tier_name"] == tname
        assert "format_rules" in tier_dict
        assert "format_rules_text" in tier_dict
        assert isinstance(tier_dict["format_rules"]["max_lines"], int)


# ---------------------------------------------------------------------------
# --verify-against tests (synthetic JSONL)
# ---------------------------------------------------------------------------

def _write_synthetic_jsonl(
    path: Path, snap: dict, mutate_first: bool = False
) -> int:
    """Write a synthetic JSONL whose packets reference the snapshot's hashes."""
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for i, entry in enumerate(snap["resolved_prompts"]):
            phash = entry["prompt_sha256"]
            if mutate_first and i == 0:
                phash = "0" * 64  # corrupt first packet
            packet = {
                "task_id": entry["task_id"],
                "tier": entry["tier"],
                "prompt_hash": phash,
                "trial_idx": 0,
            }
            f.write(json.dumps(packet) + "\n")
            n += 1
    return n


def test_verify_against_jsonl_passes_on_matching_packets(tmp_path):
    """Synthetic JSONL with correct hashes -> verify exits 0."""
    snap_path = tmp_path / "snap.json"
    jsonl_path = tmp_path / "obs.jsonl"
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    tps.write_snapshot(snap_path, snap)
    n = _write_synthetic_jsonl(jsonl_path, snap, mutate_first=False)
    assert n == 24

    # Invoke the script as a subprocess to exercise the CLI exit code path.
    script = EXP_DIR / "task_prompt_snapshot.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--output", str(snap_path),
            "--verify-against", str(jsonl_path),
            "--ran-at-utc", "2026-01-01T00:00:00+00:00",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PASS" in result.stdout
    assert "24 packets" in result.stdout


def test_verify_against_jsonl_fails_on_mismatch(tmp_path):
    """Synthetic JSONL with one corrupted hash -> verify exits 1 with diagnostic."""
    snap_path = tmp_path / "snap.json"
    jsonl_path = tmp_path / "obs.jsonl"
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")
    tps.write_snapshot(snap_path, snap)
    _write_synthetic_jsonl(jsonl_path, snap, mutate_first=True)

    script = EXP_DIR / "task_prompt_snapshot.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--output", str(snap_path),
            "--verify-against", str(jsonl_path),
            "--ran-at-utc", "2026-01-01T00:00:00+00:00",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "mismatch" in result.stdout.lower()


def test_verify_against_jsonl_in_process_pass_and_fail(tmp_path):
    """Direct call to verify_against_jsonl returns (ok, total, mismatches)."""
    snap = tps.build_snapshot(ran_at_utc="2026-01-01T00:00:00+00:00")

    good_path = tmp_path / "good.jsonl"
    bad_path = tmp_path / "bad.jsonl"
    _write_synthetic_jsonl(good_path, snap, mutate_first=False)
    _write_synthetic_jsonl(bad_path, snap, mutate_first=True)

    ok, total, mismatches = tps.verify_against_jsonl(snap, good_path)
    assert ok is True
    assert total == 24
    assert mismatches == []

    ok, total, mismatches = tps.verify_against_jsonl(snap, bad_path)
    assert ok is False
    assert total == 24
    assert len(mismatches) == 1
    assert mismatches[0]["actual"] == "0" * 64


# ---------------------------------------------------------------------------
# CLI smoke test (no --verify-against)
# ---------------------------------------------------------------------------

def test_cli_smoke_writes_json(tmp_path):
    """Running the script with --output writes valid JSON with 24 prompts."""
    out = tmp_path / "snapshot.json"
    script = EXP_DIR / "task_prompt_snapshot.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--output", str(out),
            "--ran-at-utc", "2026-01-01T00:00:00+00:00",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["_meta"]["total_resolved_prompts"] == 24
    assert len(data["resolved_prompts"]) == 24
