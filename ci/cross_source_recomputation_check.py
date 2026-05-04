#!/usr/bin/env python3
"""
cross_source_recomputation_check.py

Cross-source agreement check for the data_ties suite.

Background
----------
The L15 / claim_data_ties_check verifies that each registered numerical claim
recomputes from its own source JSON. It does not catch the case where the same
underlying quantity is encoded in two different source files and the two
sources have drifted apart from each other while each still self-consistent.

This check closes that gap. It scans ci/claim_data_ties.json for any claim
whose `metadata.cross_source_peers` field lists one or more peer claim IDs
that should compute the same numerical value. For each (claim, peer) pair it
evaluates both `value_expr` against their respective `source_file` and
verifies the two results agree within the smaller of the two tolerances.

If no peers are configured anywhere in the manifest the check is a no-op and
returns 0. If peers are configured and all pairs agree it returns 0. If any
pair disagrees it returns 1.

Schema for the optional peer field, attached to a claim entry:

    "metadata": {
        "cross_source_peers": ["other_claim_id_1", "other_claim_id_2"]
    }

The relation is intended to be symmetric. Declaring it on either side is
enough; the check normalises pairs so each is evaluated once.

Output: ci/cross_source_recomputation_results.json
Exit:   0 on agreement (or no peers configured), 1 on disagreement,
        2 on load / evaluation error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MANIFEST_JSON = SCRIPT_DIR / "claim_data_ties.json"
RESULTS_JSON = SCRIPT_DIR / "cross_source_recomputation_results.json"

ALLOWED_BUILTINS = {
    "sum": sum,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
}
ALLOWED_GLOBALS = {"__builtins__": ALLOWED_BUILTINS, "math": math}


def _load_source(path_rel: str) -> tuple[object, str]:
    src = REPO_ROOT / path_rel
    if not src.exists():
        return None, f"source not found: {path_rel}"
    try:
        with src.open(encoding="utf-8") as fh:
            return json.load(fh), "ok"
    except json.JSONDecodeError as exc:
        return None, f"json decode failed for {path_rel}: {exc}"


def _evaluate(expr: str, data: object) -> tuple[float, str]:
    try:
        result = eval(expr, ALLOWED_GLOBALS, {"d": data})
    except Exception as exc:
        return None, f"eval error: {exc}"
    if not isinstance(result, (int, float)):
        return None, f"value_expr did not return a number, got {type(result).__name__}"
    return float(result), "ok"


def _collect_pairs(claims: dict) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for claim_id, claim in claims.items():
        meta = claim.get("metadata") or {}
        peers = meta.get("cross_source_peers") or []
        for peer_id in peers:
            a, b = sorted((claim_id, peer_id))
            if a == b:
                continue
            pairs.add((a, b))
    return sorted(pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-source recomputation check")
    parser.add_argument("--strict", action="store_true",
                        help="kept for parity with sibling checks; behaviour is identical")
    parser.parse_args(argv)

    if not MANIFEST_JSON.exists():
        print(f"ERROR: {MANIFEST_JSON} not found", file=sys.stderr)
        return 2

    with MANIFEST_JSON.open(encoding="utf-8") as fh:
        manifest = json.load(fh)
    claims = manifest.get("claims", {})

    pairs = _collect_pairs(claims)

    per_pair: list[dict] = []
    failed = 0

    for left_id, right_id in pairs:
        left = claims.get(left_id)
        right = claims.get(right_id)
        entry = {"left": left_id, "right": right_id, "passed": False, "detail": ""}

        if left is None or right is None:
            entry["detail"] = (
                f"missing claim entry: left={left is not None} right={right is not None}"
            )
            per_pair.append(entry)
            failed += 1
            continue

        left_data, status = _load_source(left["source_file"])
        if left_data is None:
            entry["detail"] = f"left source: {status}"
            per_pair.append(entry)
            failed += 1
            continue
        right_data, status = _load_source(right["source_file"])
        if right_data is None:
            entry["detail"] = f"right source: {status}"
            per_pair.append(entry)
            failed += 1
            continue

        left_val, status = _evaluate(left["value_expr"], left_data)
        if left_val is None:
            entry["detail"] = f"left expr: {status}"
            per_pair.append(entry)
            failed += 1
            continue
        right_val, status = _evaluate(right["value_expr"], right_data)
        if right_val is None:
            entry["detail"] = f"right expr: {status}"
            per_pair.append(entry)
            failed += 1
            continue

        tol = min(float(left.get("tolerance", 0)), float(right.get("tolerance", 0)))
        diff = abs(left_val - right_val)
        entry["left_value"] = left_val
        entry["right_value"] = right_val
        entry["diff"] = diff
        entry["tolerance"] = tol

        if diff <= tol:
            entry["passed"] = True
            entry["detail"] = f"agree: {left_val} vs {right_val} (diff={diff} <= tol={tol})"
        else:
            failed += 1
            entry["detail"] = (
                f"DRIFT: {left_id}={left_val} vs {right_id}={right_val} "
                f"(diff={diff} > tol={tol})"
            )

        per_pair.append(entry)

    summary = {"total_pairs": len(pairs), "passed": len(pairs) - failed, "failed": failed}
    results = {"summary": summary, "per_pair": per_pair}
    RESULTS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if not pairs:
        print("cross_source_recomputation_check: no peer pairs configured, skipping (PASS)")
        return 0

    print(
        f"cross_source_recomputation_check: {len(pairs)} pair(s), "
        f"{summary['passed']} agreed, {failed} drifted"
    )
    for r in per_pair:
        tag = "PASS" if r["passed"] else "DRIFT"
        print(f"  [{tag}] {r['left']} <-> {r['right']}: {r['detail']}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
