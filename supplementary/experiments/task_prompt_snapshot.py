#!/usr/bin/env python3
"""
task_prompt_snapshot.py

Materialize the exact prompt text + format-rule strings used by the audit
experiment for the 6 audit tasks x 4 tiers (24 resolved prompts).

Why: the pre-registration says the experiment uses 6 tasks x 4 tiers from
``code_constraint_tasks.py`` and ``code_constraint_verifier.FORMAT_TIERS``.
A reviewer can derive the resolved prompt by combining these, but capturing
the exact strings as a snapshot eliminates ambiguity if the templates change
between when packets were emitted and when a reviewer re-derives.

Usage:
    python supplementary/experiments/task_prompt_snapshot.py
        -> writes supplementary/experiments/outputs/audit_v4/task_prompt_snapshot.json

    python supplementary/experiments/task_prompt_snapshot.py \\
        --verify-against supplementary/experiments/outputs/audit_v4/run_X/observation_packets.jsonl
        -> validates every packet's prompt_hash matches snapshot's resolved
           prompts. Exits 0 on PASS, 1 on FAIL.

Pure stdlib + existing experiment imports. Cross-platform. No LLM imports.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make sibling experiment modules importable when invoked from anywhere.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from code_constraint_tasks import TASKS, CodeTask  # noqa: E402
from code_constraint_verifier import (  # noqa: E402
    FORMAT_TIERS,
    FormatRules,
    format_rules_to_prompt,
)


# The 6 audit tasks (subset of the 12 defined in code_constraint_tasks.py).
AUDIT_TASK_IDS: List[str] = [
    "factorial",
    "fibonacci",
    "is_palindrome",
    "gcd",
    "reverse_words",
    "fizzbuzz",
]

# The 4 audit tiers (all entries in FORMAT_TIERS).
AUDIT_TIERS: List[str] = ["control", "low", "moderate", "high"]

DEFAULT_OUTPUT_PATH = (
    SCRIPT_DIR / "outputs" / "audit_v4" / "task_prompt_snapshot.json"
)


def sha256_str(s: str) -> str:
    """SHA256 of a string (UTF-8) as hex digest."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """SHA256 of a file's bytes as hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_prompt(task: CodeTask, tier_name: str) -> str:
    """Replicate the exact prompt format used by the audit channels.

    This mirrors ``channels/anthropic_channel.build_prompt`` and the
    OpenRouter channel's equivalent. Keep these in sync.
    """
    rules = FORMAT_TIERS[tier_name]
    rules_text = format_rules_to_prompt(rules)
    return (
        f"{task.description}\n\n"
        f"Constraints:\n{rules_text}\n\n"
        f"Return only the function in a single ```python ... ``` code block."
    )


def _serialize_format_rules(rules: FormatRules) -> Dict[str, Any]:
    """Serialize FormatRules dataclass to a plain dict (deterministic ordering)."""
    d = dataclasses.asdict(rules)
    # Ensure deterministic key order by re-emitting as a sorted dict.
    return {k: d[k] for k in sorted(d.keys())}


def _serialize_task(task: CodeTask) -> Dict[str, Any]:
    """Serialize a CodeTask + include test_code SHA256 for cross-check."""
    return {
        "task_id": task.task_id,
        "description": task.description,
        "function_name": task.function_name,
        "test_code": task.test_code,
        "test_code_sha256": sha256_str(task.test_code),
    }


def build_snapshot(
    task_ids: Optional[List[str]] = None,
    tier_names: Optional[List[str]] = None,
    ran_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the snapshot dict (no I/O)."""
    task_ids = task_ids or AUDIT_TASK_IDS
    tier_names = tier_names or AUDIT_TIERS

    tasks_by_id = {t.task_id: t for t in TASKS}
    missing = [tid for tid in task_ids if tid not in tasks_by_id]
    if missing:
        raise ValueError(f"Unknown audit task ids: {missing}")
    missing_tiers = [t for t in tier_names if t not in FORMAT_TIERS]
    if missing_tiers:
        raise ValueError(f"Unknown audit tiers: {missing_tiers}")

    # Source file hashes
    tasks_path = SCRIPT_DIR / "code_constraint_tasks.py"
    verifier_path = SCRIPT_DIR / "code_constraint_verifier.py"

    tasks_section: Dict[str, Any] = {}
    for tid in task_ids:
        tasks_section[tid] = _serialize_task(tasks_by_id[tid])

    tiers_section: Dict[str, Any] = {}
    for tname in tier_names:
        rules = FORMAT_TIERS[tname]
        tiers_section[tname] = {
            "tier_name": tname,
            "format_rules": _serialize_format_rules(rules),
            "format_rules_text": format_rules_to_prompt(rules),
        }

    resolved: List[Dict[str, Any]] = []
    for tid in task_ids:
        task = tasks_by_id[tid]
        for tname in tier_names:
            ptext = build_prompt(task, tname)
            resolved.append({
                "task_id": tid,
                "tier": tname,
                "prompt_text": ptext,
                "prompt_sha256": sha256_str(ptext),
            })

    snapshot = {
        "_meta": {
            "script": "task_prompt_snapshot.py",
            "ran_at_utc": ran_at_utc or datetime.now(timezone.utc).isoformat(),
            "code_constraint_tasks_sha256": sha256_file(tasks_path),
            "code_constraint_verifier_sha256": sha256_file(verifier_path),
            "task_count": len(task_ids),
            "tier_count": len(tier_names),
            "total_resolved_prompts": len(resolved),
        },
        "tasks": tasks_section,
        "tiers": tiers_section,
        "resolved_prompts": resolved,
    }
    return snapshot


def write_snapshot(output_path: Path, snapshot: Dict[str, Any]) -> None:
    """Write snapshot JSON deterministically (sorted keys, indent=2)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def _index_resolved(snapshot: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
    """Build a (task_id, tier) -> prompt_sha256 lookup from a snapshot."""
    idx: Dict[Tuple[str, str], str] = {}
    for entry in snapshot["resolved_prompts"]:
        idx[(entry["task_id"], entry["tier"])] = entry["prompt_sha256"]
    return idx


def verify_against_jsonl(
    snapshot: Dict[str, Any], jsonl_path: Path
) -> Tuple[bool, int, List[Dict[str, Any]]]:
    """Verify that every packet's prompt_hash matches the snapshot.

    Returns (all_ok, total_packets, mismatches). ``mismatches`` is a list
    of dicts with keys: line_no, task_id, tier, expected, actual.
    """
    idx = _index_resolved(snapshot)
    mismatches: List[Dict[str, Any]] = []
    total = 0
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            total += 1
            packet = json.loads(line)
            tid = packet.get("task_id")
            tier = packet.get("tier")
            actual = packet.get("prompt_hash")
            expected = idx.get((tid, tier))
            if expected is None:
                mismatches.append({
                    "line_no": line_no,
                    "task_id": tid,
                    "tier": tier,
                    "expected": None,
                    "actual": actual,
                    "reason": "task/tier not in snapshot",
                })
                continue
            if expected != actual:
                mismatches.append({
                    "line_no": line_no,
                    "task_id": tid,
                    "tier": tier,
                    "expected": expected,
                    "actual": actual,
                    "reason": "prompt_hash mismatch",
                })
    return (len(mismatches) == 0), total, mismatches


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Materialize audit prompts and (optionally) verify a JSONL run."
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    p.add_argument(
        "--verify-against",
        type=Path,
        default=None,
        help=(
            "Optional path to an observation_packets.jsonl. If supplied, "
            "validates every packet's prompt_hash against the snapshot and "
            "exits 0 on PASS, 1 on FAIL. The snapshot file is still written."
        ),
    )
    p.add_argument(
        "--ran-at-utc",
        type=str,
        default=None,
        help="Override the _meta.ran_at_utc field (for deterministic tests).",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    snapshot = build_snapshot(ran_at_utc=args.ran_at_utc)
    write_snapshot(args.output, snapshot)
    print(f"Wrote snapshot -> {args.output}")
    print(
        f"  tasks={snapshot['_meta']['task_count']}, "
        f"tiers={snapshot['_meta']['tier_count']}, "
        f"resolved_prompts={snapshot['_meta']['total_resolved_prompts']}"
    )

    if args.verify_against is not None:
        if not args.verify_against.exists():
            print(f"FAIL: --verify-against path does not exist: {args.verify_against}")
            return 1
        ok, total, mismatches = verify_against_jsonl(snapshot, args.verify_against)
        if ok:
            print(f"PASS: all {total} packets match snapshot prompts")
            return 0
        else:
            print(
                f"FAIL: {len(mismatches)} packets have prompt_hash mismatch "
                f"(of {total} total; first 5 listed)"
            )
            for m in mismatches[:5]:
                print(
                    f"  line {m['line_no']}: task={m['task_id']} tier={m['tier']} "
                    f"expected={m['expected']} actual={m['actual']} "
                    f"reason={m.get('reason', '')}"
                )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
