#!/usr/bin/env python3
"""
caveat_ledger_check.py -- Validator for ci/caveat_ledger.json.

Exit codes:
  0  All checks pass.
  1  Validation error (missing fields, bad values, policy violation).
  2  File missing or JSON parse error.

Output: ci/caveat_ledger_results.json
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "ci" / "caveat_ledger.json"
RESULTS_PATH = REPO_ROOT / "ci" / "caveat_ledger_results.json"

REQUIRED_FIELDS = {"id", "severity", "summary", "blocking"}
ALLOWED_SEVERITIES = {"science", "reproducibility", "cosmetic", "advisory"}


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    # -- 1. File exists and parses --
    if not LEDGER_PATH.exists():
        result = {
            "status": "ERROR",
            "exit_code": 2,
            "errors": [f"Ledger file not found: {LEDGER_PATH}"],
            "warnings": [],
            "summary": {},
        }
        RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"ERROR: {LEDGER_PATH} does not exist", file=sys.stderr)
        return 2

    try:
        raw = LEDGER_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        result = {
            "status": "ERROR",
            "exit_code": 2,
            "errors": [f"JSON parse error: {exc}"],
            "warnings": [],
            "summary": {},
        }
        RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"ERROR: JSON parse failed: {exc}", file=sys.stderr)
        return 2

    caveats = data.get("caveats", [])
    if not isinstance(caveats, list):
        errors.append("'caveats' must be a list")
        caveats = []

    severity_counts: dict[str, int] = {}
    blocking_count = 0
    seen_ids: set[str] = set()

    # -- 2 & 3 & 4. Validate each entry --
    for i, entry in enumerate(caveats):
        prefix = f"caveats[{i}] (id={entry.get('id', '<missing>')})"

        # Required fields present
        missing = REQUIRED_FIELDS - entry.keys()
        if missing:
            errors.append(f"{prefix}: missing required fields: {sorted(missing)}")

        entry_id = entry.get("id", f"__unnamed_{i}")
        if entry_id in seen_ids:
            errors.append(f"{prefix}: duplicate id '{entry_id}'")
        seen_ids.add(entry_id)

        # severity value
        severity = entry.get("severity")
        if severity is not None:
            if severity not in ALLOWED_SEVERITIES:
                errors.append(
                    f"{prefix}: severity '{severity}' not in {sorted(ALLOWED_SEVERITIES)}"
                )
            else:
                severity_counts[severity] = severity_counts.get(severity, 0) + 1

        # blocking type
        blocking = entry.get("blocking")
        if not isinstance(blocking, bool):
            errors.append(f"{prefix}: 'blocking' must be a boolean, got {type(blocking).__name__}")
        elif blocking:
            blocking_count += 1
            # Policy: blocking=true requires non-empty mitigation
            mitigation = entry.get("mitigation", "")
            if not mitigation or not mitigation.strip():
                errors.append(
                    f"{prefix}: blocking=true but 'mitigation' is empty or missing"
                )

        # summary non-empty
        summary = entry.get("summary", "")
        if not summary or not str(summary).strip():
            warnings.append(f"{prefix}: 'summary' is empty")

    # -- 5. Build summary report --
    total = len(caveats)
    status = "PASS" if not errors else "FAIL"
    exit_code = 0 if not errors else 1

    summary_block = {
        "total_caveats": total,
        "per_severity": severity_counts,
        "blocking_count": blocking_count,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }

    result = {
        "status": status,
        "exit_code": exit_code,
        "errors": errors,
        "warnings": warnings,
        "summary": summary_block,
    }

    RESULTS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Human-readable output
    print(f"caveat_ledger_check: {status}")
    print(f"  Total caveats : {total}")
    for sev in sorted(ALLOWED_SEVERITIES):
        count = severity_counts.get(sev, 0)
        if count:
            print(f"  {sev:>15} : {count}")
    print(f"  Blocking      : {blocking_count}")
    if errors:
        print(f"  Errors        : {len(errors)}")
        for e in errors:
            print(f"    - {e}")
    if warnings:
        print(f"  Warnings      : {len(warnings)}")
        for w in warnings:
            print(f"    * {w}")
    print(f"Results written to: {RESULTS_PATH.relative_to(REPO_ROOT)}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
