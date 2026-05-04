#!/usr/bin/env python3
"""
build_openreview_supplementary.py

Builds the anonymous supplementary archive for upload to OpenReview as
the NeurIPS 2026 submission's "Supplementary Material" field. The main
PDF (paper/main.pdf) is uploaded SEPARATELY as the "Paper" field, so
it is excluded from this archive.

Reuses the exclusion + PII-scrubbing rules from build_4open_zip.py.
Difference vs the 4open zip: prefix is `supplementary/` (vs `cacophony/`
for the 4open URL slug), main.pdf is excluded, and a top-level
REVIEWER_README.md is generated to orient reviewers.

Usage:
    python paper/build_openreview_supplementary.py
    python paper/build_openreview_supplementary.py --dry-run
"""
import argparse
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "paper"))
from build_4open_zip import (  # type: ignore
    collect_files, scrub_text, EXCLUDE_FILE_NAMES,
)

OUT_ZIP = REPO_ROOT / "paper" / "cacophony_openreview_supplementary.zip"

# Additional excludes specific to the OpenReview supplementary (PDF is
# uploaded as a separate field).
ADDITIONAL_EXCLUDES = {
    "main.pdf",  # uploaded as the Paper field, not in supplementary
}


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
    scrub_set = {str(p).replace("\\", "/") for p in to_scrub}
    print(f"  {len(kept)} files survived exclusion + PII scan")
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
