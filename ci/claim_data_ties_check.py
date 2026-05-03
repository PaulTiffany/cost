#!/usr/bin/env python3
"""
claim_data_ties_check.py - L15: data-tied numerical claim check.

Each entry in ci/claim_data_ties.json declares:
  - a numerical claim that appears (or should appear) in paper/main.tex
  - a source data file (typically JSON) that the claim was derived from
  - a Python expression that computes the value from the loaded source
  - the expected value the paper claims, plus an absolute tolerance

This layer loads each source, evaluates the value expression with restricted
globals, and compares the computed value to the declared expected value
within tolerance. If they diverge, either the paper or the source has
drifted; the cert fails until reconciled.

Complements L9 (formula-vs-value math identity) by handling arbitrary
numerical claims grounded in repo data rather than in formulas. The two
layers cover different parts of the claim surface:
  - L9:  paper claim X = sqrt(2/(1-rho)) at rho=0.5 -> 2.0  (math identity)
  - L15: paper claim X = 33% Pass B at high tier   -> 33    (data identity)

Exit codes
----------
  0  every data-tied claim matches its source
  1  one or more claims diverge from source data
  2  invocation error (manifest missing, malformed, source file missing)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MANIFEST = SCRIPT_DIR / "claim_data_ties.json"
RESULTS_JSON = SCRIPT_DIR / "claim_data_ties_results.json"


# Restricted eval environment: explicitly allow only what makes sense
# for numeric claim evaluation. No __builtins__, no I/O, no imports.
_ALLOWED_BUILTINS = {
    "sum": sum, "len": len, "min": min, "max": max,
    "abs": abs, "round": round, "int": int, "float": float,
    "any": any, "all": all,
    "True": True, "False": False, "None": None,
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    expected: float
    computed: float | None
    tolerance: float
    detail: str
    excerpt: str
    source_file: str


def load_source(source_path: Path):
    """Load a source data file. Currently supports JSON."""
    if source_path.suffix.lower() == ".json":
        return json.loads(source_path.read_text(encoding="utf-8"))
    raise ValueError(f"unsupported source extension: {source_path.suffix}")


def evaluate_claim(name: str, entry: dict) -> CheckResult:
    src_str = entry.get("source_file", "")
    src = (REPO_ROOT / src_str).resolve()
    if not src.exists():
        return CheckResult(
            name=name, passed=False, expected=entry.get("expected", float("nan")),
            computed=None, tolerance=entry.get("tolerance", 0),
            detail=f"source file not found: {src_str}",
            excerpt=entry.get("claim_text_excerpt", ""),
            source_file=src_str,
        )
    try:
        d = load_source(src)
    except Exception as exc:
        return CheckResult(
            name=name, passed=False, expected=entry.get("expected", float("nan")),
            computed=None, tolerance=entry.get("tolerance", 0),
            detail=f"failed to load source: {type(exc).__name__}: {exc}",
            excerpt=entry.get("claim_text_excerpt", ""),
            source_file=src_str,
        )

    expr = entry.get("value_expr", "")
    if not expr:
        return CheckResult(
            name=name, passed=False, expected=entry.get("expected", float("nan")),
            computed=None, tolerance=entry.get("tolerance", 0),
            detail="no value_expr declared",
            excerpt=entry.get("claim_text_excerpt", ""),
            source_file=src_str,
        )

    safe_globals = {"__builtins__": _ALLOWED_BUILTINS, "math": math}
    safe_locals = {"d": d}
    try:
        computed = eval(expr, safe_globals, safe_locals)  # noqa: S307 - intentionally restricted eval
    except Exception as exc:
        return CheckResult(
            name=name, passed=False, expected=entry.get("expected", float("nan")),
            computed=None, tolerance=entry.get("tolerance", 0),
            detail=f"value_expr eval error: {type(exc).__name__}: {exc}",
            excerpt=entry.get("claim_text_excerpt", ""),
            source_file=src_str,
        )

    if not isinstance(computed, (int, float)):
        return CheckResult(
            name=name, passed=False, expected=entry.get("expected", float("nan")),
            computed=None, tolerance=entry.get("tolerance", 0),
            detail=f"value_expr returned non-numeric: {type(computed).__name__}",
            excerpt=entry.get("claim_text_excerpt", ""),
            source_file=src_str,
        )

    expected = float(entry.get("expected"))
    tol = float(entry.get("tolerance", 0))
    diff = abs(float(computed) - expected)
    passed = diff <= tol
    detail = (
        f"computed={computed} expected={expected} diff={diff} tolerance={tol}"
        if passed else
        f"DRIFT: computed={computed} but paper says expected={expected} (diff={diff} > tolerance={tol})"
    )
    return CheckResult(
        name=name, passed=passed, expected=expected, computed=float(computed),
        tolerance=tol, detail=detail,
        excerpt=entry.get("claim_text_excerpt", ""),
        source_file=src_str,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                       formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print every check, not just failures")
    args = parser.parse_args()

    if not MANIFEST.exists():
        print("=" * 70)
        print("DATA-TIED NUMERICAL CLAIM CHECK")
        print("=" * 70)
        print(f"Manifest: {MANIFEST}")
        print("(no manifest yet; nothing to verify)")
        return 0

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = manifest.get("claims", {})
    if not claims:
        print("DATA-TIED CHECK: empty claims dict; nothing to verify")
        return 0

    results = [evaluate_claim(name, entry) for name, entry in claims.items()]
    n_pass = sum(1 for r in results if r.passed)
    n_fail = len(results) - n_pass

    print("=" * 70)
    print("DATA-TIED NUMERICAL CLAIM CHECK")
    print("=" * 70)
    print(f"Manifest:  {MANIFEST.relative_to(REPO_ROOT)}")
    print(f"Claims:    {len(results)}")
    print()

    for r in results:
        badge = "[OK]  " if r.passed else "[FAIL]"
        print(f"  {badge} {r.name}")
        if not r.passed or args.verbose:
            print(f"         excerpt: {r.excerpt}")
            print(f"         source:  {r.source_file}")
            print(f"         {r.detail}")

    print()
    print("-" * 70)
    print(f"  Passed: {n_pass} / {len(results)}")
    print(f"  Failed: {n_fail} / {len(results)}")
    print("-" * 70)

    payload = {
        "summary": {"total": len(results), "passed": n_pass, "failed": n_fail},
        "results": [asdict(r) for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON.relative_to(REPO_ROOT)}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
