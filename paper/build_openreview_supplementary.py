#!/usr/bin/env python3
"""
build_openreview_supplementary.py

Builds the anonymous supplementary archive for upload to OpenReview as
the NeurIPS 2026 submission's "Supplementary Material" field. The main
PDF (paper/main.pdf) is uploaded SEPARATELY as the "Paper" field, so
it is excluded from this archive.

Reuses the exclusion + PII-scrubbing rules from build_4open_zip.py, with
OpenReview-specific additions so certificate-referenced reviewer evidence
ships inside the archive without pulling in broad local-reference directories.
Difference vs the 4open zip: prefix is `supplementary/` (vs `cacophony/`
for the 4open URL slug), and a top-level REVIEWER_README.md is generated
to orient reviewers.

Usage:
    python paper/build_openreview_supplementary.py
    python paper/build_openreview_supplementary.py --dry-run
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "paper"))
from build_4open_zip import (  # type: ignore
    collect_files,
    is_text_like,
    scan_pii,
    scrub_text,
    EXCLUDE_FILE_NAMES,
    PII_ALLOWLIST,
    PII_SCRUB_AT_ZIP,
)

OUT_ZIP = REPO_ROOT / "paper" / "cacophony_openreview_supplementary.zip"
BUNDLE_MANIFEST = REPO_ROOT / "ci" / "bundle_manifest.json"
SUPPLEMENTARY_MANIFEST = REPO_ROOT / "ci" / "supplementary_manifest.json"
SUBMISSION_SURFACE_MANIFEST = REPO_ROOT / "ci" / "submission_surface_manifest.json"

ADDITIONAL_EXCLUDES = set()


REVIEWER_README = """# Supplementary Materials

This archive accompanies the NeurIPS 2026 submission and contains all
code, data, and verifier artifacts referenced in the paper.

## Quickstart for reviewers

The fastest path:

1. **Read the certificate** -- `ci/claim_certificate.md` is a
   human-readable summary of the 25 mechanical checks that verify the
   paper's numeric claims, citations, figure provenance, and anonymity.
   Latest verdict: PASS.

2. **Look up a specific claim** -- `CLAIM_AUDIT.md` indexes every
   numeric claim in the paper and points to the source data file plus
   the verification script.

3. **Spot-check from data** -- `python ci/claim_data_ties_check.py`
   regenerates the 325-of-325 data-tied verification from scratch
   (reads JSONs, evaluates value expressions, compares against expected
   values). No API keys required.

4. **Reproduce a single experiment** -- harnesses live under
   `supplementary/experiments/`. Each has a docstring with reproduction
   instructions; results land in `supplementary/experiments/outputs/`.

## Layout

- `paper/` -- main.tex, references, figures, NeurIPS style file
- `supplementary/experiments/` -- experiment harnesses + result JSONs
- `supplementary/bridges/` -- domain bridges (audio, IF-DSL, etc.)
- `supplementary/demos/` -- audio sonification suite (browser-playable
  HTML index in `audio_demos/INDEX.html`), interactive notebook
- `supplementary/illustrations/` -- schematic illustrations + provenance
- `ci/` -- the multi-suite mechanical certificate (six suites, 25 layer
  scripts) and supporting tooling
- `CLAIM_AUDIT.md` -- canonical claim registry
- `REVIEWER_QUICKSTART.md` -- short tour of the reviewer surface
- `AGENTS.md` -- agent-provenance disclosure
- `requirements.lock.txt`, `Dockerfile` -- hermetic reproduction surface

## Reproducibility

- `python ci/claim_certificate.py` -- run the full cert (no API calls)
- `python ci/cost_report.py` -- compute API cost reconstruction from
  recorded token totals (no API calls)
- `python ci/whitespace_report.py` -- find page-layout slack (utility)

## Anonymity note

Local-machine paths and usernames have been scrubbed from the artifacts
in this archive. The cert layer L9 sub-check `cert_anonymity` verifies
zero PII findings in cert-shipped artifacts at every cert run.
"""


def certificate_payload_paths() -> list[Path]:
    """Return repo-relative paths that ci/bundle_manifest.json says must ship."""
    if not BUNDLE_MANIFEST.exists():
        return []

    manifest = json.loads(BUNDLE_MANIFEST.read_text(encoding="utf-8"))
    paths: list[Path] = []
    for entry in manifest.get("files", []):
        if entry.get("absent"):
            continue
        raw = entry.get("path")
        if raw:
            paths.append(Path(raw.replace("\\", "/")))
    return paths


def surface_manifest_paths() -> list[Path]:
    """Return exact reviewer-surface paths from the supplementary manifests."""
    paths: list[str] = []

    if SUPPLEMENTARY_MANIFEST.exists():
        manifest = json.loads(SUPPLEMENTARY_MANIFEST.read_text(encoding="utf-8"))
        for artifact in manifest.get("artifacts", []):
            checks_required = artifact.get("checks_required", []) or []
            if not artifact.get("expected_in_bundle") and "exists" not in checks_required:
                continue
            paths.append(artifact.get("path", ""))
            source_script = artifact.get("source_script")
            if source_script:
                paths.append(source_script)
            paths.extend(artifact.get("source_data", []) or [])

    if SUBMISSION_SURFACE_MANIFEST.exists():
        manifest = json.loads(SUBMISSION_SURFACE_MANIFEST.read_text(encoding="utf-8"))
        for entry in manifest.get("entries", []):
            if not entry.get("expected_in_submission"):
                continue
            paths.append(entry.get("path", ""))
            paths.extend(entry.get("source_scripts", []) or [])
            paths.extend(entry.get("source_data_or_results", []) or [])

    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw or "[" in raw or "]" in raw:
            continue
        norm = raw.replace("\\", "/")
        if norm in seen:
            continue
        seen.add(norm)
        out.append(Path(norm))
    return out


def add_certificate_payload(
    kept: list[Path],
    violations: list[tuple[Path, list]],
    to_scrub: list[Path],
) -> int:
    """
    Add exact certificate-manifest paths that broad 4open exclusions omit.

    This avoids re-including whole local-reference directories such as docs/ or
    rebuttal/ while keeping reviewer verification self-contained.
    """
    kept_set = {str(path).replace("\\", "/") for path in kept}
    scrub_set = {str(path).replace("\\", "/") for path in to_scrub}
    added = 0

    payload_paths = certificate_payload_paths() + surface_manifest_paths()
    for rel in payload_paths:
        rel_str = str(rel).replace("\\", "/")
        if rel_str in kept_set:
            continue

        abs_path = REPO_ROOT / rel
        if not abs_path.is_file():
            continue

        if is_text_like(rel) and rel_str not in PII_ALLOWLIST:
            findings = scan_pii(abs_path)
            if findings:
                if rel_str in PII_SCRUB_AT_ZIP:
                    if rel_str not in scrub_set:
                        to_scrub.append(rel)
                        scrub_set.add(rel_str)
                else:
                    violations.append((rel, findings))
                    continue

        kept.append(rel)
        kept_set.add(rel_str)
        added += 1

    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="scan only, do not write zip")
    args = parser.parse_args()

    print(f"Scanning {REPO_ROOT} ...")
    # Patch the imported EXCLUDE_FILE_NAMES with our additional excludes
    # for this build only.
    EXCLUDE_FILE_NAMES.update(ADDITIONAL_EXCLUDES)
    kept, violations, to_scrub = collect_files()
    added_payload = add_certificate_payload(kept, violations, to_scrub)
    scrub_set = {str(p).replace("\\", "/") for p in to_scrub}
    print(f"  {len(kept)} files survived exclusion + PII scan")
    print(f"  {added_payload} certificate payload files added explicitly")
    print(f"  {len(to_scrub)} files will be SCRUBBED at zip-time")
    print(f"  {len(violations)} files HALTED for PII (excluded)")
    if violations:
        print("\nViolations (excluded from zip; review):")
        for path, findings in violations[:10]:
            preview = ", ".join(f"{pat}({n})" for pat, n in findings)
            print(f"  {path}: {preview}")

    if args.dry_run:
        print("\n(dry run; no zip written)")
        return 0

    print(f"\nWriting {OUT_ZIP.name} ...")
    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Top-level README oriented for reviewers
        zf.writestr("supplementary/REVIEWER_README.md", REVIEWER_README)
        for rel in kept:
            rel_str = str(rel).replace("\\", "/")
            arcname = "supplementary/" + rel_str
            if rel_str in scrub_set:
                content = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
                zf.writestr(arcname, scrub_text(content))
            else:
                zf.write(REPO_ROOT / rel, arcname)
    size_mb = OUT_ZIP.stat().st_size / 1024 / 1024
    print(f"Wrote {OUT_ZIP} ({size_mb:.2f} MB, {len(kept) + 1} files including REVIEWER_README)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
