#!/usr/bin/env python3
"""
bundle_verification.py -- Verify that certificate payload references exist in the
supplementary bundle and that file hashes match.

For now, "bundle" = current repo state (REPO_ROOT).
Pass --bundle-root <dir> to verify against an alternate directory (e.g., unzipped
submission supplementary).

Exit codes:
  0 = PASS (all files present, hashes match, cross-references satisfied)
  1 = FAIL (missing files, hash mismatches, or cross-ref gaps)
  2 = Invocation or configuration error

Output: ci/bundle_verification_results.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_MANIFEST_PATH = REPO_ROOT / "ci" / "bundle_manifest.json"
CLAIM_CERT_PATH = REPO_ROOT / "ci" / "claim_certificate.json"
CLAIM_DATA_TIES_PATH = REPO_ROOT / "ci" / "claim_data_ties.json"
OUT_PATH = REPO_ROOT / "ci" / "bundle_verification_results.json"


def sha256_of_file(path: Path) -> str:
    """Compute byte-level sha256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def normalise_path(raw: str) -> str:
    """Normalise a path string to forward-slash form for consistent keying."""
    return raw.replace("\\", "/")


def build_manifest_index(manifest: dict) -> dict[str, dict]:
    """Return {normalised_path: entry} for fast lookup."""
    return {normalise_path(e["path"]): e for e in manifest.get("files", [])}


def verify_manifest_files(
    manifest_index: dict[str, dict],
    bundle_root: Path,
) -> tuple[list[str], list[dict]]:
    """
    For each file in the manifest: verify it exists under bundle_root and that
    its sha256 matches the manifest record.

    Returns (missing_paths, hash_mismatch_entries).
    Hash mismatch entries are dicts with keys: path, expected, actual, note.

    Lenient on Windows line-ending differences: if sizes match we still flag
    hash mismatches but annotate them with a line-ending note so the caller can
    downgrade severity if desired.
    """
    missing: list[str] = []
    mismatches: list[dict] = []

    for norm_path, entry in manifest_index.items():
        abs_path = bundle_root / norm_path
        if not abs_path.exists():
            missing.append(norm_path)
            continue
        expected_hash = entry.get("sha256", "")
        if not expected_hash:
            # No hash recorded -- skip hash check
            continue
        actual_hash = sha256_of_file(abs_path)
        if actual_hash != expected_hash:
            expected_size = entry.get("size", -1)
            actual_size = abs_path.stat().st_size
            # Annotate if sizes differ by an amount consistent with CRLF/LF conversion
            size_diff = abs(actual_size - expected_size)
            note = ""
            if size_diff > 0:
                note = (
                    f"size differs by {size_diff} bytes "
                    "(possibly Windows CRLF vs LF line endings)"
                )
            mismatches.append(
                {
                    "path": norm_path,
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                    "expected_size": expected_size,
                    "actual_size": actual_size,
                    "note": note,
                }
            )

    return missing, mismatches


def cross_ref_artifact_hashes(
    cert: dict | None,
    manifest_index: dict[str, dict],
) -> list[str]:
    """
    claim_certificate.json artifact_hashes keys must all be in the bundle manifest.
    Returns list of paths that are in artifact_hashes but missing from manifest.
    """
    if cert is None:
        return []
    artifact_hashes = cert.get("artifact_hashes", {})
    not_in_bundle: list[str] = []
    for raw_path in artifact_hashes:
        norm = normalise_path(raw_path)
        if norm not in manifest_index:
            not_in_bundle.append(norm)
    return not_in_bundle


def cross_ref_data_ties_sources(
    data_ties: dict | None,
    manifest_index: dict[str, dict],
) -> list[str]:
    """
    claim_data_ties.json source_files must all be in the bundle manifest.
    Returns list of source_file paths missing from manifest.
    """
    if data_ties is None:
        return []
    claims = data_ties.get("claims", {})
    not_in_bundle: list[str] = []
    seen: set[str] = set()
    for entry in claims.values():
        raw_path = entry.get("source_file", "")
        if not raw_path:
            continue
        norm = normalise_path(raw_path)
        if norm in seen:
            continue
        seen.add(norm)
        if norm not in manifest_index:
            not_in_bundle.append(norm)
    return not_in_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify cert payload files against the bundle manifest."
    )
    parser.add_argument(
        "--bundle-root",
        metavar="DIR",
        default=None,
        help=(
            "Root directory to verify against (default: repo root). "
            "Use this to verify against an unzipped submission bundle."
        ),
    )
    args = parser.parse_args(argv)

    # Resolve bundle root
    if args.bundle_root:
        bundle_root = Path(args.bundle_root).resolve()
        if not bundle_root.is_dir():
            print(
                f"ERROR: --bundle-root '{bundle_root}' is not a directory.",
                file=sys.stderr,
            )
            return 2
    else:
        bundle_root = REPO_ROOT

    # Load manifest
    manifest = load_json(BUNDLE_MANIFEST_PATH)
    if manifest is None:
        print(
            f"ERROR: {BUNDLE_MANIFEST_PATH} not found or not parseable. "
            "Run ci/bundle_manifest.py first.",
            file=sys.stderr,
        )
        return 2

    manifest_index = build_manifest_index(manifest)
    n_manifest_files = len(manifest_index)

    # Step 1: verify manifest files exist and hash-match
    missing_from_bundle, hash_mismatches = verify_manifest_files(
        manifest_index, bundle_root
    )
    n_verified = n_manifest_files - len(missing_from_bundle) - len(hash_mismatches)

    # Step 2: cross-reference artifact_hashes from claim_certificate.json
    cert = load_json(CLAIM_CERT_PATH)
    cert_missing = cross_ref_artifact_hashes(cert, manifest_index)
    cert_note = (
        "WARNING: claim_certificate.json not found; skipping artifact_hashes cross-ref."
        if cert is None
        else None
    )

    # Step 3: cross-reference source_files from claim_data_ties.json
    data_ties = load_json(CLAIM_DATA_TIES_PATH)
    ties_missing = cross_ref_data_ties_sources(data_ties, manifest_index)
    ties_note = (
        "WARNING: claim_data_ties.json not found; skipping source_file cross-ref."
        if data_ties is None
        else None
    )

    # Aggregate all missing (deduped)
    all_missing_set: set[str] = set(missing_from_bundle)
    all_missing_set.update(cert_missing)
    all_missing_set.update(ties_missing)

    passed = (
        len(missing_from_bundle) == 0
        and len(hash_mismatches) == 0
        and len(cert_missing) == 0
        and len(ties_missing) == 0
    )

    result: dict = {
        "summary": {
            "bundle_root": str(bundle_root),
            "manifest_files": n_manifest_files,
            "verified_present": n_verified,
            "missing_from_bundle": len(missing_from_bundle),
            "hash_mismatches": len(hash_mismatches),
            "cert_artifact_hashes_not_in_manifest": len(cert_missing),
            "data_ties_sources_not_in_manifest": len(ties_missing),
            "passed": passed,
        },
        "missing_from_bundle": sorted(missing_from_bundle),
        "hash_mismatches": hash_mismatches,
        "cross_ref": {
            "cert_artifact_hashes_not_in_manifest": sorted(cert_missing),
            "data_ties_sources_not_in_manifest": sorted(ties_missing),
        },
        "notes": [n for n in [cert_note, ties_note] if n],
    }

    OUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")

    verdict = "PASS" if passed else "FAIL"
    print(f"Bundle verification: {verdict}")
    print(
        f"  manifest files: {n_manifest_files}, "
        f"verified present: {n_manifest_files - len(missing_from_bundle)}, "
        f"missing: {len(missing_from_bundle)}, "
        f"hash mismatches: {len(hash_mismatches)}"
    )
    if cert_missing:
        print(
            f"  cert artifact_hashes not in manifest ({len(cert_missing)}): "
            + ", ".join(cert_missing[:5])
            + (" ..." if len(cert_missing) > 5 else "")
        )
    if ties_missing:
        print(
            f"  data_ties source_files not in manifest ({len(ties_missing)}): "
            + ", ".join(ties_missing[:5])
            + (" ..." if len(ties_missing) > 5 else "")
        )
    if cert_note:
        print(f"  {cert_note}")
    if ties_note:
        print(f"  {ties_note}")
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
