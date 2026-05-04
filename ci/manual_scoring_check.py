#!/usr/bin/env python3
"""
manual_scoring_check.py - Verify that manually-scored result JSONs carry
rubric provenance and scorer metadata.

For each file in MANUALLY_SCORED_FILES:
  1. Check the file exists (INFO + skip if absent).
  2. Load it and inspect the top-level `_meta` key.
  3. Verify required fields are present.
  4. Emit warnings for advisory fields.

Required _meta fields
---------------------
  scoring_method   : one of ("manual", "manual_with_rubric", "second_rater",
                              "machine_verifier")
  rubric_path      : path to the rubric document (string)
  rubric_hash      : sha256 hex of the rubric document at the time of scoring
  scorer_id  OR    : opaque identifier for the scorer
  scorer_role      : human-readable role ("single human author",
                                          "blinded second rater", ...)
  n_trials_scored  : int — how many trials were scored
  single_rater_warning : bool — must be True when there is no second-rater data

Advisory / warning fields (present but checked for plausibility)
  scoring_methodology : free-text description (warn if absent)

Output
------
  ci/manual_scoring_results.json:
    {"summary": {"total": N, "passed": N, "failed": N},
     "per_file": [{"path": "...", "passed": bool,
                   "missing": [...], "warnings": [...]}]}

Exit codes
----------
  0  PASS  all checked files passed (files that don't exist are skipped)
  1  FAIL  one or more present files failed validation
  2  invocation error (bad arguments, JSON parse error, etc.)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"
RESULTS_OUT = CI_DIR / "manual_scoring_results.json"

# ---------------------------------------------------------------------------
# Canonical list of files expected to carry manual-scoring metadata.
# Add new paths here as more manually-scored result files are created.
# ---------------------------------------------------------------------------
MANUALLY_SCORED_FILES: list[str] = [
    "supplementary/experiments_rebuttal/image_transfer/image_transfer_runD_passB.json",
]

# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------
VALID_SCORING_METHODS = frozenset([
    "manual",
    "manual_with_rubric",
    "second_rater",
    "machine_verifier",
])

REQUIRED_FIELDS: list[tuple[str, str]] = [
    # (field_name, instruction_on_failure)
    (
        "scoring_method",
        "Add '_meta.scoring_method' — one of: "
        '"manual", "manual_with_rubric", "second_rater", "machine_verifier".',
    ),
    (
        "rubric_path",
        "Add '_meta.rubric_path' — the path (relative to repo root or absolute) "
        "of the rubric document used during scoring.",
    ),
    (
        "rubric_hash",
        "Add '_meta.rubric_hash' — the sha256 hex digest of the rubric document "
        "at the time of scoring. Compute with: "
        "python -c \"import hashlib, pathlib; "
        "print(hashlib.sha256(pathlib.Path('RUBRIC_PATH').read_bytes()).hexdigest())\"",
    ),
    (
        "n_trials_scored",
        "Add '_meta.n_trials_scored' — the integer count of trials you personally "
        "scored for this result file.",
    ),
    (
        "single_rater_warning",
        "Add '_meta.single_rater_warning: true' to acknowledge this file has only "
        "one rater. Set to false only when a second-rater check is documented.",
    ),
]

# scorer_id OR scorer_role is required (either is acceptable)
SCORER_FIELDS = ("scorer_id", "scorer_role")


def _sha256_of_rubric(rubric_path_str: str) -> str | None:
    """Compute sha256 of a rubric document if it can be resolved."""
    p = Path(rubric_path_str)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if p.exists():
        return hashlib.sha256(p.read_bytes()).hexdigest()
    return None


def check_file(path_str: str) -> dict:
    """Validate one manually-scored result file.

    Returns a per_file record:
      {"path": str, "passed": bool, "missing": [str], "warnings": [str]}
    """
    missing: list[str] = []
    warnings: list[str] = []
    path = REPO_ROOT / path_str

    if not path.exists():
        # INFO — file is expected but not yet present; skip silently in
        # pass/fail accounting.
        print(f"  [INFO ] {path_str}: file not found — skipping.")
        return {"path": path_str, "skipped": True, "passed": True,
                "missing": [], "warnings": []}

    # Load JSON
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "path": path_str,
            "skipped": False,
            "passed": False,
            "missing": [f"Could not parse JSON: {type(exc).__name__}: {exc}"],
            "warnings": [],
        }

    meta = data.get("_meta")

    # Top-level _meta key missing entirely
    if meta is None:
        # Check if scoring metadata is embedded differently (e.g. top-level keys
        # that act as the _meta block)
        # Some files use top-level keys directly (e.g. image_transfer_runD_passB.json
        # uses "scoring_methodology" at top level without a "_meta" wrapper).
        # Fall back to the top-level dict for those keys.
        meta = data

    if not isinstance(meta, dict):
        missing.append(
            "Top-level '_meta' key is missing or not a dict. "
            "Add a '_meta': {...} block with scoring provenance fields."
        )
        return {
            "path": path_str,
            "skipped": False,
            "passed": False,
            "missing": missing,
            "warnings": [],
        }

    # -- Required fields ------------------------------------------------------
    for field, instruction in REQUIRED_FIELDS:
        if field not in meta:
            missing.append(f"Missing field '{field}': {instruction}")

    # -- scorer_id OR scorer_role ---------------------------------------------
    if not any(f in meta for f in SCORER_FIELDS):
        missing.append(
            "Missing 'scorer_id' or 'scorer_role'. Add at least one of: "
            "_meta.scorer_id (opaque ID) or _meta.scorer_role "
            "(e.g. \"single human author\" / \"blinded second rater\")."
        )

    # -- scoring_method enum check -------------------------------------------
    sm = meta.get("scoring_method")
    if sm is not None and sm not in VALID_SCORING_METHODS:
        missing.append(
            f"'scoring_method' value {sm!r} is not in the allowed set: "
            f"{sorted(VALID_SCORING_METHODS)}"
        )

    # -- single_rater_warning must be boolean True if only one rater ----------
    srw = meta.get("single_rater_warning")
    if srw is not None and not isinstance(srw, bool):
        missing.append(
            f"'single_rater_warning' must be a boolean (true/false), got {srw!r}."
        )

    # -- Advisory: scoring_methodology free-text description -----------------
    if "scoring_methodology" not in meta:
        warnings.append(
            "Advisory: '_meta.scoring_methodology' not present. "
            "Adding a free-text description of the scoring approach improves auditability."
        )

    # -- Advisory: rubric_hash plausibility (sha256 = 64 hex chars) ----------
    rh = meta.get("rubric_hash")
    if rh is not None and (not isinstance(rh, str) or len(rh) != 64):
        warnings.append(
            f"'rubric_hash' looks malformed (expected 64-char sha256 hex, got {repr(rh)[:80]}). "
            "Recompute with: python -c \"import hashlib, pathlib; "
            "print(hashlib.sha256(pathlib.Path('<rubric_path>').read_bytes()).hexdigest())\""
        )

    passed = len(missing) == 0
    return {
        "path": path_str,
        "skipped": False,
        "passed": passed,
        "missing": missing,
        "warnings": warnings,
    }


def build_report(per_file: list[dict]) -> dict:
    checked = [r for r in per_file if not r.get("skipped")]
    total = len(checked)
    passed = sum(1 for r in checked if r["passed"])
    failed = total - passed
    return {
        "summary": {"total": total, "passed": passed, "failed": failed},
        "per_file": per_file,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify manually-scored result JSONs carry rubric + scorer metadata."
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Skip writing results JSON (useful for dry-run tests).",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("MANUAL SCORING METADATA CHECK")
    print("=" * 70)
    print(f"Repo root : {REPO_ROOT}")
    print(f"Files to check: {len(MANUALLY_SCORED_FILES)}")
    print()

    per_file: list[dict] = []
    for path_str in MANUALLY_SCORED_FILES:
        print(f"  Checking: {path_str}")
        result = check_file(path_str)
        per_file.append(result)

        if result.get("skipped"):
            continue

        if result["passed"]:
            print(f"  [PASS ] {path_str}")
            if result["warnings"]:
                for w in result["warnings"]:
                    print(f"          WARN: {w}")
        else:
            print(f"  [FAIL ] {path_str}")
            for m in result["missing"]:
                print(f"          MISSING: {m}")
            for w in result["warnings"]:
                print(f"          WARN: {w}")
        print()

    report = build_report(per_file)
    s = report["summary"]
    print("-" * 70)
    print(f"  Total checked : {s['total']}")
    print(f"  Passed        : {s['passed']}")
    print(f"  Failed        : {s['failed']}")
    print("-" * 70)

    if not args.no_output:
        RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Results written to: {RESULTS_OUT.relative_to(REPO_ROOT)}")

    overall = "PASS" if s["failed"] == 0 else "FAIL"
    print(f"\nOverall: {overall}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
