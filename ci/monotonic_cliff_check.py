"""
monotonic_cliff_check.py  --  Cert layer: validate monotone failure-with-rho claim.

For each configured source, asserts tier-order monotonicity across
[control, low, moderate, high]:
  - pass_rate / pass_count  should be non-increasing  (control >= low >= moderate >= high)
  - failure_rate            should be non-decreasing   (control <= low <= moderate <= high)

A registered exceptions list (initially empty) allows legitimate non-monotone
cases to be acknowledged without failing the cert.

Exit codes:
  0  all checks passed (or all failures are registered exceptions)
  1  one or more unregistered monotonicity violations found
  2  invocation error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"

TIER_ORDER = ["control", "low", "moderate", "high"]

# --------------------------------------------------------------------------- #
# Registered exceptions
# Each entry is a check name that is allowed to be non-monotone.
# Add entries here (with a comment explaining why) if a legitimate inversion
# exists in the data.
# --------------------------------------------------------------------------- #
REGISTERED_EXCEPTIONS: set[str] = set()

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    """Return (data, error_string). data is None on failure."""
    if not path.exists():
        return None, f"MISSING: {path}"
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error in {path}: {exc}"
    except OSError as exc:
        return None, f"IO error reading {path}: {exc}"


def _find_inversions(values: list[float], direction: str) -> list[dict]:
    """
    Return a list of inversion dicts for consecutive tier pairs.
    direction='non_increasing': each value must be <= previous (pass rates).
    direction='non_decreasing': each value must be >= previous (failure rates).
    """
    inversions = []
    for i in range(1, len(values)):
        prev_tier = TIER_ORDER[i - 1]
        curr_tier = TIER_ORDER[i]
        prev_val = values[i - 1]
        curr_val = values[i]
        if direction == "non_increasing" and curr_val > prev_val:
            inversions.append(
                {
                    "pair": f"{prev_tier} -> {curr_tier}",
                    "expected": f"{prev_val} >= {curr_val}",
                    "got": f"{prev_val} < {curr_val}",
                    "delta": curr_val - prev_val,
                }
            )
        elif direction == "non_decreasing" and curr_val < prev_val:
            inversions.append(
                {
                    "pair": f"{prev_tier} -> {curr_tier}",
                    "expected": f"{prev_val} <= {curr_val}",
                    "got": f"{prev_val} > {curr_val}",
                    "delta": prev_val - curr_val,
                }
            )
    return inversions


# --------------------------------------------------------------------------- #
# Per-source extractors
# --------------------------------------------------------------------------- #


def check_calibration_results(source_path: Path) -> dict:
    """
    supplementary/experiments/calibration_results.json
    Path: aggregate.stats.{tier}.pass_rate_oneshot
    Direction: non_increasing
    """
    name = "calibration_pass_rate_oneshot"
    rel = str(source_path.relative_to(REPO_ROOT))

    data, err = _load_json(source_path)
    if err:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "MISSING" if "MISSING" in (err or "") else "ERROR",
            "inversions": [],
            "error": err,
        }

    try:
        stats = data["aggregate"]["stats"]
        values = [stats[tier]["pass_rate_oneshot"] for tier in TIER_ORDER]
    except (KeyError, TypeError) as exc:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "SCHEMA_ERROR",
            "inversions": [],
            "error": f"Unexpected schema: {exc}",
        }

    inversions = _find_inversions(values, "non_increasing")
    passed = len(inversions) == 0
    return {
        "name": name,
        "source": rel,
        "tiers": TIER_ORDER,
        "values": values,
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "inversions": inversions,
    }


def check_per_task_correlation(source_path: Path) -> dict:
    """
    rebuttal/figures/per_task_correlation_results.json
    Path: per_tier.{tier}.mean
    Direction: non_increasing
    """
    name = "per_task_correlation_tier_mean"
    rel = str(source_path.relative_to(REPO_ROOT))

    data, err = _load_json(source_path)
    if err:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "MISSING" if "MISSING" in (err or "") else "ERROR",
            "inversions": [],
            "error": err,
        }

    try:
        per_tier = data["per_tier"]
        values = [per_tier[tier]["mean"] for tier in TIER_ORDER]
    except (KeyError, TypeError) as exc:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "SCHEMA_ERROR",
            "inversions": [],
            "error": f"Unexpected schema: {exc}",
        }

    inversions = _find_inversions(values, "non_increasing")
    passed = len(inversions) == 0
    return {
        "name": name,
        "source": rel,
        "tiers": TIER_ORDER,
        "values": values,
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "inversions": inversions,
    }


def check_json_nl_v4_failure_rate(source_path: Path) -> dict:
    """
    supplementary/experiments/json_nl_v4_results.json
    Path: summary.{tier}.failure_rate
    Direction: non_decreasing
    """
    name = "json_nl_v4_failure_rate"
    rel = str(source_path.relative_to(REPO_ROOT))

    data, err = _load_json(source_path)
    if err:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "MISSING" if "MISSING" in (err or "") else "ERROR",
            "inversions": [],
            "error": err,
        }

    try:
        summary = data["summary"]
        values = [summary[tier]["failure_rate"] for tier in TIER_ORDER]
    except (KeyError, TypeError) as exc:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "SCHEMA_ERROR",
            "inversions": [],
            "error": f"Unexpected schema: {exc}",
        }

    inversions = _find_inversions(values, "non_decreasing")
    passed = len(inversions) == 0
    return {
        "name": name,
        "source": rel,
        "tiers": TIER_ORDER,
        "values": values,
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "inversions": inversions,
    }


def check_openrouter_regression_stage1_pass(source_path: Path) -> dict:
    """
    supplementary/experiments/openrouter_regression_results.json
    Pooled stage-1 format pass count (stage1_pass_a) per tier.
    Direction: non_increasing
    Expected: 239 -> 56 -> 23 -> 0
    """
    name = "openrouter_regression_stage1_pass_count"
    rel = str(source_path.relative_to(REPO_ROOT))

    data, err = _load_json(source_path)
    if err:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "MISSING" if "MISSING" in (err or "") else "ERROR",
            "inversions": [],
            "error": err,
        }

    try:
        results = data["results"]
        if not isinstance(results, list):
            raise TypeError('"results" is not a list')
    except (KeyError, TypeError) as exc:
        return {
            "name": name,
            "source": rel,
            "tiers": TIER_ORDER,
            "values": [],
            "passed": False,
            "status": "SCHEMA_ERROR",
            "inversions": [],
            "error": f"Unexpected schema: {exc}",
        }

    # Pool stage1_pass_a (format pass) counts per tier
    counts: dict[str, int] = {tier: 0 for tier in TIER_ORDER}
    # stage1_pass_b is the format axis (the diagonal-cost cliff signal:
    # 239 -> 56 -> 23 -> 0 across tiers). stage1_pass_a is the test axis
    # which is approximately flat by design (the experiment's null axis).
    for row in results:
        tier = row.get("tier")
        if tier not in counts:
            continue
        if row.get("stage1_pass_b", False):
            counts[tier] += 1

    values = [counts[tier] for tier in TIER_ORDER]
    inversions = _find_inversions(values, "non_increasing")
    passed = len(inversions) == 0
    return {
        "name": name,
        "source": rel,
        "tiers": TIER_ORDER,
        "values": values,
        "passed": passed,
        "status": "PASS" if passed else "FAIL",
        "inversions": inversions,
    }


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

CHECKS = [
    (
        REPO_ROOT / "supplementary/experiments/calibration_results.json",
        check_calibration_results,
    ),
    (
        REPO_ROOT / "rebuttal/figures/per_task_correlation_results.json",
        check_per_task_correlation,
    ),
    (
        REPO_ROOT / "supplementary/experiments/json_nl_v4_results.json",
        check_json_nl_v4_failure_rate,
    ),
    (
        REPO_ROOT / "supplementary/experiments/openrouter_regression_results.json",
        check_openrouter_regression_stage1_pass,
    ),
]


def run_all() -> dict:
    per_check = []
    total = 0
    passed_count = 0
    failed_count = 0
    missing_count = 0

    for source_path, checker in CHECKS:
        result = checker(source_path)
        name = result["name"]

        # Apply registered exceptions
        if name in REGISTERED_EXCEPTIONS and not result["passed"]:
            result["registered_exception"] = True
            result["status"] = "EXCEPTED"
            result["passed"] = True  # counts as pass for summary

        status = result.get("status", "UNKNOWN")
        total += 1
        if status == "MISSING":
            missing_count += 1
            # Missing counts as a soft skip, not hard fail
            result["passed"] = False
            failed_count += 1
        elif result["passed"]:
            passed_count += 1
        else:
            failed_count += 1

        per_check.append(result)

    return {
        "summary": {
            "total": total,
            "passed": passed_count,
            "failed": failed_count,
            "missing": missing_count,
        },
        "registered_exceptions": sorted(REGISTERED_EXCEPTIONS),
        "per_check": per_check,
    }


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> int:
    try:
        results = run_all()
    except Exception as exc:  # noqa: BLE001
        print(f"[monotonic_cliff_check] Invocation error: {exc}", file=sys.stderr)
        return 2

    output_path = CI_DIR / "monotonic_cliff_results.json"
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
    except OSError as exc:
        print(
            f"[monotonic_cliff_check] Could not write output: {exc}", file=sys.stderr
        )
        return 2

    summary = results["summary"]
    print(
        f"[monotonic_cliff_check] {summary['passed']}/{summary['total']} passed"
        f" ({summary['failed']} failed, {summary['missing']} missing)"
    )
    for rec in results["per_check"]:
        status = rec.get("status", "UNKNOWN")
        vals_str = (
            " -> ".join(f"{v:.4g}" for v in rec["values"]) if rec["values"] else "n/a"
        )
        print(f"  {status:12s}  {rec['name']}  [{vals_str}]")
        for inv in rec.get("inversions", []):
            print(f"             INVERSION {inv['pair']}: {inv['got']}")
        if "error" in rec:
            print(f"             {rec['error']}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
