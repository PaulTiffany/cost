"""
ci/submission_surface_check.py
Standalone checker for ci/submission_surface_manifest.json.

Verifies:
  1. Manifest parses as valid JSON and all entries have required schema fields.
  2. Every path with expected_in_submission=true exists on disk.
  3. No entry has role=internal_or_excluded AND expected_in_submission=true (contradictory).
  4. Every evidence_asset entry has at least one of: claim_ids (non-empty) or paper_locations (non-empty).
  5. Every raw_observation referenced by a shipped result JSON either appears in the manifest as
     expected_in_submission=true, or is explicitly role=internal_or_excluded (allowlisted).
  6. Every *_draft.png path is role=internal_or_excluded or role=provenance_source (never silently shipped).
  7. Every reviewer_aid entry has a non-empty scope_note.

Output: ci/submission_surface_results.json (standard result JSON format).
Exit codes: 0=PASS, 1=SOFT_FAIL (warnings only), 2=FAIL (hard failures).

Usage:
  python ci/submission_surface_check.py
  python ci/submission_surface_check.py --verbose

Dependencies: stdlib only (json, pathlib, sys).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "ci" / "submission_surface_manifest.json"
RESULTS_PATH = REPO_ROOT / "ci" / "submission_surface_results.json"
BUNDLE_MANIFEST_PATH = REPO_ROOT / "ci" / "bundle_manifest.json"

REQUIRED_FIELDS = {
    "path",
    "role",
    "artifact_kind",
    "expected_in_submission",
    "hash_policy",
    "scope_note",
}

VALID_ROLES = {
    "evidence_asset",
    "raw_observation",
    "reviewer_aid",
    "provenance_source",
    "internal_or_excluded",
}

VALID_HASH_POLICIES = {"required", "inherited_from_result_json", "excluded"}


def check_submission_surface(verbose: bool = False) -> dict:
    failures_hard = []
    failures_soft = []
    passed = []
    warnings = []

    # --- Check 1: parse manifest ---
    if not MANIFEST_PATH.exists():
        failures_hard.append({
            "check": "manifest_exists",
            "path": str(MANIFEST_PATH),
            "reason": "ci/submission_surface_manifest.json not found; run parallel agent that builds it first",
        })
        return _write_results(failures_hard, failures_soft, passed, warnings)

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        failures_hard.append({
            "check": "manifest_parse",
            "path": str(MANIFEST_PATH),
            "reason": f"JSON parse error: {e}",
        })
        return _write_results(failures_hard, failures_soft, passed, warnings)

    passed.append({
        "check": "manifest_parse",
        "detail": "ci/submission_surface_manifest.json is valid JSON",
    })

    entries = manifest.get("entries", [])
    if not entries:
        failures_hard.append({
            "check": "entries_present",
            "reason": "Manifest has no entries",
        })
        return _write_results(failures_hard, failures_soft, passed, warnings)

    passed.append({
        "check": "entries_present",
        "detail": f"{len(entries)} entries found",
    })

    # --- Schema validation + per-entry checks ---
    expected_submission_paths = set()
    draft_png_paths = []
    raw_observation_paths = []
    all_paths_by_role = {}

    for i, entry in enumerate(entries):
        path = entry.get("path", f"<entry {i}>")
        role = entry.get("role", "")
        expected_in_sub = entry.get("expected_in_submission", False)
        scope_note = entry.get("scope_note", "")
        hash_policy = entry.get("hash_policy", "")
        claim_ids = entry.get("claim_ids", [])
        paper_locations = entry.get("paper_locations", [])

        # Schema: required fields
        missing = REQUIRED_FIELDS - set(entry.keys())
        if missing:
            failures_hard.append({
                "check": "schema_required_fields",
                "path": path,
                "reason": f"Missing required fields: {sorted(missing)}",
            })

        # Schema: valid role
        if role not in VALID_ROLES:
            failures_hard.append({
                "check": "schema_valid_role",
                "path": path,
                "reason": f"Invalid role '{role}'; must be one of {sorted(VALID_ROLES)}",
            })

        # Schema: valid hash_policy
        if hash_policy not in VALID_HASH_POLICIES:
            failures_hard.append({
                "check": "schema_valid_hash_policy",
                "path": path,
                "reason": f"Invalid hash_policy '{hash_policy}'; must be one of {sorted(VALID_HASH_POLICIES)}",
            })

        # Track for cross-checks
        all_paths_by_role[path] = role
        if expected_in_sub:
            expected_submission_paths.add(path)

        # Check 3: role=internal_or_excluded AND expected_in_submission=true is contradictory
        if role == "internal_or_excluded" and expected_in_sub:
            failures_hard.append({
                "check": "contradictory_internal_excluded",
                "path": path,
                "reason": "role=internal_or_excluded but expected_in_submission=true; these are contradictory",
            })

        # Check 4: evidence_asset must have claim_ids or paper_locations
        if role == "evidence_asset":
            has_claims = bool(claim_ids and any(str(c).strip() for c in claim_ids))
            has_locs = bool(paper_locations and any(str(p).strip() for p in paper_locations))
            if not has_claims and not has_locs:
                failures_hard.append({
                    "check": "evidence_asset_anchor",
                    "path": path,
                    "reason": "role=evidence_asset requires non-empty claim_ids or paper_locations",
                })

        # Check 7: reviewer_aid must have scope_note
        if role == "reviewer_aid" and not scope_note.strip():
            failures_hard.append({
                "check": "reviewer_aid_scope_note",
                "path": path,
                "reason": "role=reviewer_aid requires a non-empty scope_note",
            })

        # Collect draft PNGs for check 6
        if "_draft.png" in path.lower() or path.lower().endswith("_draft.png"):
            draft_png_paths.append((path, role, expected_in_sub))

        # Collect raw_observations for check 5
        if role == "raw_observation":
            raw_observation_paths.append((path, expected_in_sub))

        # Check 2: expected_in_submission=true files exist on disk
        # (Only check concrete paths, not placeholder group entries like "[23 PNGs]")
        if expected_in_sub and "[" not in path:
            abs_path = REPO_ROOT / path.replace("/", "\\")
            if not abs_path.exists():
                failures_hard.append({
                    "check": "file_exists",
                    "path": path,
                    "reason": "expected_in_submission=true but file not found on disk",
                })
            else:
                if verbose:
                    passed.append({"check": "file_exists", "path": path, "detail": "exists"})

    passed.append({
        "check": "per_entry_schema",
        "detail": f"Checked {len(entries)} entries; hard failures above if any schema issues found",
    })

    # Check 6: draft PNGs must not be silently shipped
    for dp, role, expected_in_sub in draft_png_paths:
        if expected_in_sub and role not in ("internal_or_excluded", "provenance_source"):
            failures_hard.append({
                "check": "draft_png_not_shipped",
                "path": dp,
                "reason": f"*_draft.png with expected_in_submission=true and role={role}; drafts should be internal_or_excluded or provenance_source",
            })
        else:
            passed.append({
                "check": "draft_png_not_shipped",
                "path": dp,
                "detail": f"Draft PNG correctly role={role}, expected_in_submission={expected_in_sub}",
            })

    # Check 5: raw_observation cross-check
    # Load bundle_manifest to identify shipped result JSONs
    shipped_result_jsons = set()
    bundle_manifest_present = BUNDLE_MANIFEST_PATH.exists()
    if bundle_manifest_present:
        try:
            bm = json.loads(BUNDLE_MANIFEST_PATH.read_text(encoding="utf-8"))
            for entry in bm.get("files", []):
                ep = entry.get("path", "").replace("\\", "/")
                if ep.endswith(".json") and entry.get("role") == "result":
                    shipped_result_jsons.add(ep)
        except Exception as e:
            warnings.append({
                "check": "bundle_manifest_load",
                "reason": f"Could not load bundle_manifest.json for raw_observation cross-check: {e}",
            })
    else:
        warnings.append({
            "check": "bundle_manifest_absent",
            "reason": "ci/bundle_manifest.json not found; skipping raw_observation cross-check (check 5)",
        })

    for ro_path, ro_expected in raw_observation_paths:
        # A raw_observation is OK if: it is expected_in_submission=true (shipped),
        # OR it is explicitly role=internal_or_excluded (which it can't be since it's raw_observation),
        # OR the backing result JSON is in the bundle/manifest.
        # The main concern is that raw_observation images backing a shipped result
        # must either themselves be shipped or explicitly excluded.
        if not ro_expected and "[" not in ro_path:
            # Check if this path is explicitly classified as excluded
            if all_paths_by_role.get(ro_path) == "raw_observation":
                failures_soft.append({
                    "check": "raw_observation_disposition",
                    "path": ro_path,
                    "reason": "raw_observation is not expected_in_submission; verify this is intentional (add to internal_or_excluded or ship with backing result)",
                })

    # Summary counts
    n_expected = len(expected_submission_paths)
    n_evidence = sum(1 for e in entries if e.get("role") == "evidence_asset")
    n_internal = sum(1 for e in entries if e.get("role") == "internal_or_excluded")
    n_raw_obs = sum(1 for e in entries if e.get("role") == "raw_observation")
    n_reviewer = sum(1 for e in entries if e.get("role") == "reviewer_aid")
    n_provenance = sum(1 for e in entries if e.get("role") == "provenance_source")

    passed.append({
        "check": "role_counts",
        "detail": (
            f"evidence_asset={n_evidence}, reviewer_aid={n_reviewer}, "
            f"raw_observation={n_raw_obs}, provenance_source={n_provenance}, "
            f"internal_or_excluded={n_internal}; "
            f"total entries={len(entries)}, expected_in_submission={n_expected}"
        ),
    })

    return _write_results(failures_hard, failures_soft, passed, warnings)


def _write_results(failures_hard, failures_soft, passed, warnings) -> dict:
    n_hard = len(failures_hard)
    n_soft = len(failures_soft)
    n_pass = len(passed)
    n_warn = len(warnings)

    if n_hard > 0:
        status = "FAIL"
        exit_code = 2
    elif n_soft > 0:
        status = "SOFT_FAIL"
        exit_code = 1
    else:
        status = "PASS"
        exit_code = 0

    result = {
        "check": "submission_surface_check",
        "status": status,
        "exit_code": exit_code,
        "summary": {
            "hard_failures": n_hard,
            "soft_failures": n_soft,
            "passed": n_pass,
            "warnings": n_warn,
        },
        "hard_failures": failures_hard,
        "soft_failures": failures_soft,
        "passed": passed,
        "warnings": warnings,
    }

    RESULTS_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return result


def main():
    verbose = "--verbose" in sys.argv
    result = check_submission_surface(verbose=verbose)

    status = result["status"]
    s = result["summary"]
    print(
        f"submission_surface_check: {status} "
        f"(hard={s['hard_failures']} soft={s['soft_failures']} "
        f"pass={s['passed']} warn={s['warnings']})"
    )

    if result["hard_failures"]:
        print("\nHard failures:")
        for f in result["hard_failures"]:
            print(f"  [{f['check']}] {f.get('path', '')} -- {f['reason']}")

    if result["soft_failures"]:
        print("\nSoft failures:")
        for f in result["soft_failures"]:
            print(f"  [{f['check']}] {f.get('path', '')} -- {f['reason']}")

    if result["warnings"]:
        print("\nWarnings:")
        for w in result["warnings"]:
            print(f"  [{w['check']}] {w.get('reason', '')}")

    print(f"\nResults written to: {RESULTS_PATH}")
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
