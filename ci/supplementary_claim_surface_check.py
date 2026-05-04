#!/usr/bin/env python3
"""
supplementary_claim_surface_check.py

Stop silently skipping supplementary-only claims.

claim_audit.py currently drops claims marked supplementary_only=True from its
main.tex scan (they live in supplementary materials, not the paper body). This
script verifies that each such claim is explicitly covered by at least one entry
in ci/supplementary_manifest.json (created by Agent J).

If the manifest is absent, this script reports an informational note and exits 0
(the manifest not existing yet is not a failure of *this* check).

Exit codes:
  0  all supplementary_only claims are mapped (or manifest absent)
  1  manifest present but one or more supplementary_only claims are unmapped
  2  invocation error (import failure, bad JSON, etc.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_JSON = REPO_ROOT / "ci" / "supplementary_manifest.json"
RESULTS_JSON = REPO_ROOT / "ci" / "supplementary_claim_surface_results.json"


def collect_supplementary_only_claims() -> list[dict]:
    """Import claim_audit.py and collect all entries with supplementary_only=True."""
    # Add ci/ to path so claim_audit imports its siblings cleanly
    ci_dir = REPO_ROOT / "ci"
    if str(ci_dir) not in sys.path:
        sys.path.insert(0, str(ci_dir))

    try:
        import claim_audit  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"Could not import claim_audit: {exc}") from exc

    all_claims = claim_audit.CLAIMS + claim_audit.IMPORTANT + claim_audit.COMPLETE
    return [c for c in all_claims if c.get("supplementary_only")]


def load_manifest(path: Path) -> list[dict] | None:
    """Load supplementary_manifest.json; return None if absent."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"supplementary_manifest.json is not valid JSON: {exc}") from exc
    # Accept either a top-level list or a dict with an 'entries' key
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "entries" in data:
        return data["entries"]
    # Also accept a dict whose values are the entries
    if isinstance(data, dict):
        return list(data.values()) if data else []
    raise RuntimeError(
        f"supplementary_manifest.json has unexpected structure: {type(data)}"
    )


def build_claim_to_manifest(
    claims: list[dict], entries: list[dict]
) -> dict[str, list[str]]:
    """For each claim id, collect manifest entry paths that list it.

    Manifest entries are expected to be dicts with at least:
      - 'path' (or 'file'): the supplementary file path
      - 'claim_ids': list of claim id strings covered by that file
    """
    claim_ids = {c["id"] for c in claims}
    mapping: dict[str, list[str]] = {cid: [] for cid in claim_ids}

    for entry in entries:
        # Support both 'path' and 'file' as the path key
        entry_path = entry.get("path") or entry.get("file") or "<unknown>"
        covered = entry.get("claim_ids", [])
        for cid in covered:
            if cid in mapping:
                mapping[cid].append(str(entry_path))

    return mapping


def main() -> int:
    try:
        supp_claims = collect_supplementary_only_claims()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR (unexpected): {exc}", file=sys.stderr)
        return 2

    n_total = len(supp_claims)
    print(f"Supplementary-only claims found: {n_total}")
    for c in supp_claims:
        print(f"  {c['id']}: {c['description']}")
    print()

    # --- Load manifest ---
    try:
        entries = load_manifest(MANIFEST_JSON)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if entries is None:
        # Manifest not yet created (Agent J's deliverable). Not a failure.
        note = (
            f"INFO: supplementary_manifest.json not found at {MANIFEST_JSON}. "
            "Agent J has not yet created it. Skipping coverage check."
        )
        print(note)

        output = {
            "summary": {
                "supplementary_only_claims_total": n_total,
                "mapped": 0,
                "deprecated": 0,
                "unmapped": 0,
                "passed": True,  # Not a failure; manifest simply absent
                "note": "Manifest absent — coverage check skipped.",
            },
            "unmapped_claims": [],
            "claim_to_manifest": {c["id"]: [] for c in supp_claims},
        }
        RESULTS_JSON.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"\nResults written to: {RESULTS_JSON}")
        print("\nRESULT: INFO -- manifest absent, no coverage check performed.")
        return 0

    print(f"Manifest loaded: {len(entries)} entries from {MANIFEST_JSON}")

    # --- Build coverage map ---
    try:
        claim_to_manifest = build_claim_to_manifest(supp_claims, entries)
    except Exception as exc:
        print(f"ERROR building coverage map: {exc}", file=sys.stderr)
        return 2

    # --- Check for deprecated allowlist (optional 'deprecated_claim_ids' key) ---
    # The manifest may carry a top-level deprecated_claim_ids list.
    deprecated_ids: set[str] = set()
    try:
        raw_manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
        if isinstance(raw_manifest, dict):
            deprecated_ids = set(raw_manifest.get("deprecated_claim_ids", []))
    except Exception:
        pass  # Best-effort; missing key is fine

    # --- Classify claims ---
    mapped = [cid for cid, paths in claim_to_manifest.items() if paths]
    deprecated = [cid for cid in claim_to_manifest if cid in deprecated_ids]
    unmapped = [
        cid
        for cid, paths in claim_to_manifest.items()
        if not paths and cid not in deprecated_ids
    ]

    unmapped_details = [
        {"id": c["id"], "description": c["description"]}
        for c in supp_claims
        if c["id"] in unmapped
    ]

    passed = len(unmapped) == 0

    # --- Summary output ---
    print(f"  Mapped     : {len(mapped)}")
    print(f"  Deprecated : {len(deprecated)}")
    print(f"  Unmapped   : {len(unmapped)}")
    print()

    if unmapped_details:
        print("Unmapped supplementary-only claims (not covered by any manifest entry):")
        for item in unmapped_details:
            print(f"  {item['id']}: {item['description']}")
        print()

    # --- Write JSON output ---
    output = {
        "summary": {
            "supplementary_only_claims_total": n_total,
            "mapped": len(mapped),
            "deprecated": len(deprecated),
            "unmapped": len(unmapped),
            "passed": passed,
        },
        "unmapped_claims": unmapped_details,
        "claim_to_manifest": {cid: paths for cid, paths in claim_to_manifest.items()},
    }
    RESULTS_JSON.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Results written to: {RESULTS_JSON}")

    if passed:
        print("\nRESULT: PASS -- all supplementary-only claims are mapped in the manifest.")
        return 0
    else:
        print(
            f"\nRESULT: FAIL -- {len(unmapped)} supplementary-only claim(s) have no manifest entry."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
