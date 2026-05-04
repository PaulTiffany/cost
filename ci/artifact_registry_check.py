#!/usr/bin/env python3
"""
artifact_registry_check.py

Validates ci/artifact_registry.json and checks that no paper claim (L15 entry
in ci/claim_data_ties.json) binds to a deprecated or superseded artifact.

Exit codes:
  0  PASS
  1  FAIL  (at least one deprecated/superseded binding)
  2  invocation / parse error
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REGISTRY_PATH = REPO_ROOT / "ci" / "artifact_registry.json"
TIES_PATH = REPO_ROOT / "ci" / "claim_data_ties.json"
OUTPUT_PATH = REPO_ROOT / "ci" / "artifact_registry_results.json"

ALLOWED_STATUSES = {"current", "supplementary_only", "reviewer_evidence", "superseded", "deprecated"}
ALLOWED_KINDS = {"raw", "aggregate", "derived", "hand_scored", "external_observation"}
REQUIRED_ARTIFACT_FIELDS = {"path", "status", "kind", "supersedes", "superseded_by",
                             "raw_inputs", "recompute_script", "reason_or_note"}


def load_json(path: Path, label: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: {label} not found at {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {label} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def check_schema(registry: dict) -> list[str]:
    """Return list of schema-error messages (empty == OK)."""
    errors = []

    if "_meta" not in registry:
        errors.append("Missing top-level key '_meta'")
    if "artifacts" not in registry:
        errors.append("Missing top-level key 'artifacts'")
        return errors  # nothing more to check

    for i, artifact in enumerate(registry["artifacts"]):
        tag = artifact.get("path", f"<artifact[{i}]>")
        missing = REQUIRED_ARTIFACT_FIELDS - set(artifact.keys())
        if missing:
            errors.append(f"{tag}: missing required fields: {sorted(missing)}")
        status = artifact.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{tag}: status={status!r} not in {sorted(ALLOWED_STATUSES)}")
        kind = artifact.get("kind")
        if kind not in ALLOWED_KINDS:
            errors.append(f"{tag}: kind={kind!r} not in {sorted(ALLOWED_KINDS)}")

    return errors


def build_status_index(registry: dict) -> dict[str, str]:
    """Return {path: status} for all artifacts."""
    return {a["path"]: a["status"] for a in registry.get("artifacts", [])}


def check_deprecated_bindings(ties: dict, status_index: dict[str, str]) -> list[dict]:
    """
    For each L15 claim, check whether its source_file is deprecated or superseded.
    Returns list of violation dicts.
    """
    violations = []
    claims = ties.get("claims", {})
    for claim_id, claim in claims.items():
        source = claim.get("source_file")
        if source is None:
            continue
        artifact_status = status_index.get(source)
        if artifact_status in ("deprecated", "superseded"):
            violations.append({
                "claim_id": claim_id,
                "source_file": source,
                "artifact_status": artifact_status,
                "claim_text_excerpt": claim.get("claim_text_excerpt", ""),
                "paper_location_hint": claim.get("paper_location_hint", ""),
            })
    return violations


def check_orphan_current(ties: dict, status_index: dict[str, str]) -> list[str]:
    """
    Advisory: every status='current' artifact should be bound by at least one L15 claim.
    Returns list of orphan paths.
    """
    referenced = set()
    for claim in ties.get("claims", {}).values():
        source = claim.get("source_file")
        if source:
            referenced.add(source)

    orphans = []
    for path, status in status_index.items():
        if status == "current" and path not in referenced:
            orphans.append(path)
    return orphans


def compute_status_counts(registry: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in registry.get("artifacts", []):
        s = a.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts


def main() -> None:
    registry = load_json(REGISTRY_PATH, "artifact_registry.json")
    ties = load_json(TIES_PATH, "claim_data_ties.json")

    # --- Check 1: Schema validity ---
    schema_errors = check_schema(registry)
    if schema_errors:
        for err in schema_errors:
            print(f"SCHEMA ERROR: {err}", file=sys.stderr)
        sys.exit(2)

    status_index = build_status_index(registry)
    total_artifacts = len(registry.get("artifacts", []))
    status_counts = compute_status_counts(registry)

    # --- Check 2: No deprecated/superseded bindings ---
    deprecated_bindings = check_deprecated_bindings(ties, status_index)

    # --- Check 3: Orphan current artifacts (advisory) ---
    orphans = check_orphan_current(ties, status_index)

    passed = len(deprecated_bindings) == 0

    summary = {
        "total_artifacts": total_artifacts,
        "status_counts": status_counts,
        "deprecated_bindings": len(deprecated_bindings),
        "orphan_current": len(orphans),
        "passed": passed,
    }

    output = {
        "summary": summary,
        "deprecated_bindings": deprecated_bindings,
        "orphans": orphans,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)

    # --- Print results ---
    print(f"Artifacts registered : {total_artifacts}")
    print(f"Status distribution  : {status_counts}")
    print(f"Deprecated bindings  : {len(deprecated_bindings)}")
    print(f"Orphan current       : {len(orphans)}  (advisory, not a failure)")

    if deprecated_bindings:
        print("\nFAIL: Paper claims bind to deprecated/superseded artifacts:")
        for v in deprecated_bindings:
            print(f"  [{v['claim_id']}] -> {v['source_file']}  (status={v['artifact_status']})")
            print(f"    claim: {v['claim_text_excerpt']}")
    else:
        print("\nPASS: No deprecated/superseded bindings.")

    if orphans:
        print("\nWARN: Current artifacts not referenced by any L15 claim:")
        for o in orphans:
            print(f"  {o}")

    print(f"\nResults written to {OUTPUT_PATH}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
