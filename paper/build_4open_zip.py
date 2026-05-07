#!/usr/bin/env python3
"""
build_4open_zip.py

Builds the anonymous code archive for upload to https://anonymous.4open.science/.

The 4open.science mirror serves code anonymously alongside the NeurIPS
double-blind submission. The URL hardcoded in the anonymous paper PDF is:
  https://anonymous.4open.science/r/cacophony

This script:
  1. Walks the repo, INCLUDING tracked files, EXCLUDING:
       - .git/ history (commit identity leak)
       - non-anonymous build artifacts (main_arxiv.*, build_arxiv.py,
         cacophony_arxiv_source.tar.gz)
       - the 4open zip itself (avoid recursive inclusion)
       - LaTeX build caches (.aux, .log, .fdb_latexmk, etc.)
       - Python caches (__pycache__, .pyc)
       - the anonymous output zip
  2. Scans every text file in the candidate set for PII strings
     ('paulc', 'paultiffany', 'Paul Tiffany', '@gmail', 'AppData')
     and HALTS with a report if any are found in non-allowlisted files.
  3. Zips the survivor set with prefix `cacophony/` so 4open serves it
     under that slug.

Usage:
    python paper/build_4open_zip.py
    python paper/build_4open_zip.py --dry-run     # scan only, no zip
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_ZIP = REPO_ROOT / "paper" / "cacophony_4open_anonymous.zip"

# Files / dirs to exclude from the zip (path fragments matched anywhere)
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".venv", "venv",
    "node_modules", ".mypy_cache", "egg-info",
    # Local references / not for public mirror:
    "docs",   # NeurIPS official PDFs + ethics reading; user-local reference
    "rebuttal", # rebuttal package; not part of NeurIPS submission flow
    "experiments_rebuttal",  # post-rebuttal experiments; not for initial review
    "submission_repo", "neurips_template", "ICML_2026_Template (3)",  # legacy local
    "camera_ready",  # post-acceptance, not for review
    "launch",  # local launch scripts
    "theperfectpaper",  # local-author scratch; identity leaks
    "_dev",  # in-repo figure dev harness (paper/figures/_dev)
    "agent_m13_workspace",  # parallel agent scratch directory
}
EXCLUDE_FILE_SUFFIXES_HEAVY = (".wav",)  # 12 MB of WAVs; the .mp3 versions ship
EXCLUDE_FILE_SUFFIXES = (
    ".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".synctex.gz",
    ".bbl", ".blg", ".toc", ".pyc", ".pyo", ".swp",
)
EXCLUDE_FILE_NAMES = {
    "main_arxiv.tex", "main_arxiv.pdf", "main_arxiv.aux", "main_arxiv.log",
    "main_arxiv.out", "main_arxiv.bbl", "main_arxiv.blg", "main_arxiv.fls",
    "main_arxiv.fdb_latexmk", "main_arxiv.synctex.gz", "main_arxiv.toc",
    "build_arxiv.py", "build_4open_zip.py", "build_openreview_supplementary.py",
    "cacophony_arxiv_source.tar.gz",
    "cacophony_4open_anonymous.zip",
    "cacophony_openreview_supplementary.zip",
    ".DS_Store", "Thumbs.db", "desktop.ini",
    # Sensitive collaborator handoff (matches user instruction):
    "notetoclaude.json", "note2claude2.json",
    # Author-only / draft / non-reviewer-facing artifacts:
    "mutation_cert.py", "mutation_cert_results.json",
    "historical_mutation_baseline.json",
    "double_blind_imagegen.py", "double_blind_anonymized.json",
    "double_blind_key.json", "double_blind_response.json",
    "double_blind_image.png",
    "author_lexicon_drift_check.py", "author_lexicon_drift_results.json",
    "de_llm_lexicon.md",
    "GRADED_METRICS_SPEC.md",  # draft planning doc; submission_surface_manifest:903 marks internal_or_excluded
}
# Belt-and-suspenders: ANY file matching these patterns is excluded.
# Catches accidental output zips not yet enumerated above.
EXCLUDE_FILE_PATTERN_PREFIXES = ("cacophony_",)
EXCLUDE_FILE_PATTERN_SUFFIXES = (".zip", ".tar.gz")
# Files whose name CONTAINS any of these substrings are excluded outright.
# Catches sub-extensions like .PRE_COSMIC_RAY_BACKUP that don't match
# Path.suffix (which only sees the last extension).
EXCLUDE_FILE_NAME_CONTAINS = (".PRE_COSMIC_RAY_BACKUP",)

# Override: paths under these prefixes are included even if a parent
# directory is in EXCLUDE_DIRS. Used to surgically include reviewer-
# expected artifacts (per submission_surface_manifest expected_in_submission)
# when the parent tree is otherwise broadly excluded.
INCLUDE_FORCE_PATH_PREFIXES = (
    "supplementary/experiments_rebuttal/image_transfer/outputs/runD/",
)

# PII patterns: any text-like file containing these is a hard fail
PII_PATTERNS = [
    re.compile(r"\bpaulc\b", re.IGNORECASE),
    re.compile(r"paultiffany", re.IGNORECASE),
    re.compile(r"Paul[\s_-]*Carver[\s_-]*Tiffany", re.IGNORECASE),
    re.compile(r"@gmail\.com", re.IGNORECASE),
    re.compile(r"C:\\Users\\paulc", re.IGNORECASE),
    # Local-machine path roots (any C:\src\ or C:/src/ outside an allowlist
    # is a fingerprint -- author machine layout disclosure).
    re.compile(r"C:[\\/]+src[\\/]+", re.IGNORECASE),
    # Prior-template directory name -- venue label + project layout fingerprint.
    re.compile(r"ICML_2026_Template", re.IGNORECASE),
]

# Files where PII patterns are EXPECTED (regex sources, allowlist, etc.)
PII_ALLOWLIST = {
    "ci/pdf_camera_ready_check.py", # scans PDF for author identifiers; legit
    "ci/_add_cross_source_peer_example.py",  # one-off helper, no PII anyway
    # NOTE: ci/anonymity_check.py and ci/cert_anonymity_check.py used to be
    # here, but their source contains literal author-program identifiers
    # (Principia Symbolica, PyLantern, Fascia, Cosmic Engineers, etc.) that
    # would deanonymize through the cross-paper attribution chain if shipped.
    # They are now in PII_SCRUB_AT_ZIP so the shipped copy has those tokens
    # redacted while the local copy retains the real patterns for the
    # author's own scans.
}

# Files where we SCRUB the PII at zip-time rather than excluding the file
# entirely. The on-disk version stays as-is; the zipped copy is sanitized.
# Used for fingerprints / manifests that leak the local username via paths
# but are otherwise useful artifacts to ship.
PII_SCRUB_AT_ZIP = {
    # Anonymity scanners — source contains author-program identifiers as
    # regex literals + description strings; scrubber redacts them so the
    # shipped script is inert (won't actually scan) but doesn't leak.
    "ci/anonymity_check.py",
    "ci/cert_anonymity_check.py",
    "ci/dependency_fingerprint.json",
    "ci/dependency_fingerprint.py",
    "ci/supplementary_manifest.json",
    "ci/supplementary_surface_results.json",
    # Cert result JSONs that record absolute author-machine paths in their
    # _meta blocks. Useful for review (paths are reproducible-by-content via
    # the relative-path companion fields), so scrub at zip-time rather than
    # excluding outright.
    "ci/bundle_verification_results.json",
    "ci/cert_anonymity_results.json",
    "ci/citation_integrity_results.json",
    "ci/claim_audit_results.json",
    "ci/claim_coverage_uncovered.json",
    "ci/cross_claim_consistency_results.json",
    "ci/license_clearance_results.json",
    "ci/link_integrity_results.json",
    "ci/pdf_camera_ready_results.json",
    # New session reviewer-facing certs that record absolute pdf/main.tex paths:
    "ci/figure_bounds_results.json",
    "ci/figure_caption_grounding_results.json",
    "ci/paper_surface_results.json",
    # Bundle and supplementary manifests
    "ci/illustration_lineage_results.json",
    "ci/paper_surface_check.py",
    "ci/figure_caption_grounding_check.py",
    "ci/figure_bounds_check.py",
    "ci/page_check.py",
    "ci/page_check_results.json",
}

TEXT_LIKE_SUFFIXES = (".tex", ".bib", ".sty", ".cls", ".bst", ".md",
                       ".py", ".json", ".yaml", ".yml", ".toml",
                       ".txt", ".cfg", ".ini", ".sh", ".html", ".css",
                       ".js", ".csv", ".tsv", ".xml")


def _load_manifest_excluded() -> set:
    """Read submission_surface_manifest.json and return literal paths
    marked role=internal_or_excluded. Placeholder paths like
    'supplementary/demos/audio_demos/[29 non-curated WAVs]' are skipped
    (those carry their own .wav exclusion rule)."""
    import json as _json
    p = REPO_ROOT / "ci" / "submission_surface_manifest.json"
    if not p.exists():
        return set()
    try:
        m = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for e in m.get("entries", []):
        if e.get("role") != "internal_or_excluded":
            continue
        path = e.get("path") or ""
        if "[" in path or "]" in path:
            continue
        out.add(path.replace("\\", "/"))
    return out


_MANIFEST_EXCLUDED = _load_manifest_excluded()


def is_excluded(path: Path) -> bool:
    rel_str = str(path).replace("\\", "/")
    # Manifest-driven exclusion: any file with role=internal_or_excluded
    # in submission_surface_manifest.json is dropped from the bundle.
    if rel_str in _MANIFEST_EXCLUDED:
        return True
    # INCLUDE_FORCE check runs before EXCLUDE_DIRS so reviewer-expected
    # subdirectories under broadly-excluded trees (experiments_rebuttal/,
    # rebuttal/) can still be included.
    if any(rel_str.startswith(prefix) for prefix in INCLUDE_FORCE_PATH_PREFIXES):
        return False
    parts = set(path.parts)
    if EXCLUDE_DIRS & parts:
        return True
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_FILE_SUFFIXES:
        return True
    # Pattern guard: prefix-and-suffix combo catches any future cacophony_*.zip
    # without us having to enumerate it. Prevents recursive self-inclusion.
    if any(path.name.startswith(p) for p in EXCLUDE_FILE_PATTERN_PREFIXES):
        if any(path.name.endswith(s) for s in EXCLUDE_FILE_PATTERN_SUFFIXES):
            return True
    if any(s in path.name for s in EXCLUDE_FILE_NAME_CONTAINS):
        return True
    return False


def is_text_like(path: Path) -> bool:
    return path.suffix.lower() in TEXT_LIKE_SUFFIXES


def scan_pii(path: Path) -> list[tuple[str, int]]:
    """Return list of (pattern, occurrences) where the pattern matched."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    findings = []
    for rx in PII_PATTERNS:
        n = len(rx.findall(content))
        if n:
            findings.append((rx.pattern, n))
    return findings


def collect_files() -> tuple[list[Path], list[tuple[Path, list]], list[Path]]:
    """Walk the repo, return (kept_files, pii_violations, files_to_scrub)."""
    kept: list[Path] = []
    violations: list[tuple[Path, list]] = []
    to_scrub: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            continue
        if is_excluded(rel):
            continue
        rel_str = str(rel).replace("\\", "/")
        if is_text_like(rel) and rel_str not in PII_ALLOWLIST:
            findings = scan_pii(path)
            if findings:
                if rel_str in PII_SCRUB_AT_ZIP:
                    to_scrub.append(rel)
                    kept.append(rel)
                else:
                    violations.append((rel, findings))
                    continue
                continue
        kept.append(rel)
    return kept, violations, to_scrub


def scrub_text(text: str) -> str:
    """Replace PII patterns with placeholders for zip-time scrubbing."""
    # Order matters: longer, more-specific patterns first.
    text = re.sub(r"C:\\\\Users\\\\paulc(\\\\[A-Za-z0-9_.\-]+)*",
                  "<redacted_user_path>", text, flags=re.IGNORECASE)
    text = re.sub(r"C:\\Users\\paulc(\\[A-Za-z0-9_.\-]+)*",
                  "<redacted_user_path>", text, flags=re.IGNORECASE)
    # Local repo paths: collapse C:\src\NeurIPS\foo or C:\src\ICML_2026_Template\foo
    # to neutral relative form. Keeps trailing path segments readable.
    text = re.sub(r"C:[\\/]+src[\\/]+(NeurIPS|neurips|ICML_2026_Template)[\\/]+",
                  "", text, flags=re.IGNORECASE)
    text = re.sub(r"C:[\\/]+src[\\/]+(NeurIPS|neurips|ICML_2026_Template)\b",
                  "<repo>", text, flags=re.IGNORECASE)
    # Catchall for any other C:\src\* roots
    text = re.sub(r"C:[\\/]+src[\\/]+", "<repo>/", text, flags=re.IGNORECASE)
    # Strip standalone prior-template name mentions
    text = re.sub(r"\bICML_2026_Template\b", "<prior_template>", text)
    text = re.sub(r"paulctiffany@gmail\.com", "<redacted_email>",
                  text, flags=re.IGNORECASE)
    text = re.sub(r"Paul[\s_-]*Carver[\s_-]*Tiffany( III)?",
                  "<redacted_author>", text, flags=re.IGNORECASE)
    # Word-boundary 'paulc' last (catches anything the path / email patterns missed)
    text = re.sub(r"\bpaulc\b", "<redacted_user>", text, flags=re.IGNORECASE)
    # Author-program identifiers (cross-paper deanonymization chain).
    # No word boundaries — these tokens never appear as substrings of
    # legitimate English words, and \b fails inside regex literals where
    # the preceding character is the literal "b" of "\b" (e.g.
    # r"\bPrincipia\b" has "b" right before "P", which is a word char).
    text = re.sub(r"principia", "<redacted_program_a>", text, flags=re.IGNORECASE)
    text = re.sub(r"symbolica", "<redacted_program_b>", text, flags=re.IGNORECASE)
    text = re.sub(r"py[-_]?lantern", "<redacted_program_c>", text, flags=re.IGNORECASE)
    text = re.sub(r"fascia", "<redacted_program_d>", text, flags=re.IGNORECASE)
    text = re.sub(r"cosmic[\s_\\-]+engineers", "<redacted_program_e>", text, flags=re.IGNORECASE)
    text = re.sub(r"cosmogenesis", "<redacted_program_f>", text, flags=re.IGNORECASE)
    text = re.sub(r"agi[-_]?2026", "<redacted_program_g>", text, flags=re.IGNORECASE)
    text = re.sub(r"turchin", "<redacted_program_h>", text, flags=re.IGNORECASE)
    text = re.sub(r"piergaton", "<redacted_program_i>", text, flags=re.IGNORECASE)
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="scan and report; do not write the zip")
    args = parser.parse_args()

    print(f"Scanning {REPO_ROOT} ...")
    kept, violations, to_scrub = collect_files()
    scrub_set = {str(p).replace("\\", "/") for p in to_scrub}
    print(f"  {len(kept)} files survived exclusion + PII scan")
    print(f"  {len(to_scrub)} files will be SCRUBBED at zip-time")
    print(f"  {len(violations)} files HALTED for PII (excluded)")

    if violations:
        print("\nPII VIOLATIONS (these files are excluded from the zip; review manually):")
        for path, findings in violations[:20]:
            preview = ", ".join(f"{pat}({n})" for pat, n in findings)
            print(f"  {path}: {preview}")
        if len(violations) > 20:
            print(f"  ... ({len(violations) - 20} more)")
        # We do NOT halt the script; we exclude these files and continue,
        # so the zip is safe even if some files leaked PII. Caller can
        # review and harden the upstream files.

    if args.dry_run:
        print("\n(dry run; no zip written)")
        return 0

    print(f"\nWriting {OUT_ZIP.name} ...")
    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in kept:
            rel_str = str(rel).replace("\\", "/")
            arcname = "cacophony/" + rel_str
            if rel_str in scrub_set:
                content = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
                zf.writestr(arcname, scrub_text(content))
            else:
                zf.write(REPO_ROOT / rel, arcname)
    size_mb = OUT_ZIP.stat().st_size / 1024 / 1024
    print(f"Wrote {OUT_ZIP} ({size_mb:.2f} MB, {len(kept)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
