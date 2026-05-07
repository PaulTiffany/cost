"""Anonymity check for NeurIPS 2026 double-blind submission.

Scans paper/ and supplementary/ for identity leaks before submission.
- HARD FAIL on any hit in paper/
- HARD FAIL on hits in supplementary/ as well (with a separate `warn` bucket
  reserved for legacy/comment-only contexts; this script defaults to hard
  fail per spec but still surfaces a `warn` channel for future tuning).

Exits 0 if no hard_fail entries, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# Roots to scan
PAPER_ROOT = Path(r"C:\src\neurips\paper")
SUPP_ROOT = Path(r"C:\src\neurips\supplementary")

# Output path
RESULTS_JSON = Path(r"C:\src\neurips\ci\anonymity_check_results.json")

# Text file extensions we will inspect
TEXT_EXTS = {".tex", ".py", ".json", ".md", ".yml", ".yaml", ".txt", ".bib",
             ".cfg", ".ini", ".toml", ".sty", ".bst", ".cls"}

# Build artifact / binary extensions to skip outright
SKIP_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff",
             ".pyc", ".pyo", ".so", ".dll", ".exe", ".o", ".a",
             ".aux", ".bbl", ".blg", ".log", ".out", ".synctex", ".synctex.gz",
             ".fls", ".fdb_latexmk", ".toc", ".lof", ".lot", ".nav", ".snm",
             ".vrb", ".idx", ".ilg", ".ind",
             ".zip", ".tar", ".gz", ".7z",
             ".npy", ".npz", ".pt", ".pth", ".ckpt", ".bin", ".h5"}

# Directories to skip (caches, venvs, build dirs)
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", ".pytest_cache",
             ".mypy_cache", "node_modules", ".ipynb_checkpoints",
             "build", "dist", ".tox"}

# Specific filenames that legitimately contain identity (arxiv-variant tooling
# and outputs) — these are NEVER part of the anonymous supplementary bundle
# (build_4open_zip.py and build_openreview_supplementary.py exclude them by name).
# Listing them here so the anonymity sweep gives a true signal on the anonymous
# surface only.
SKIP_FILES = {
    "build_arxiv.py",                # generates the arxiv variant with real identity
    "build_4open_zip.py",            # contains regex patterns matching identity (allowlisted)
    "build_openreview_supplementary.py",  # ships the supplementary bundle
    "main_arxiv.tex",                # arxiv variant of the paper
    "main_arxiv.pdf",                # arxiv-variant PDF
    "checklist_arxiv.tex",           # arxiv-variant checklist
    "anonymity_check.py",            # contains identity patterns it searches for (this file)
    "cert_anonymity_check.py",       # same — contains identity patterns it searches for
    "pdf_camera_ready_check.py",     # contains identity patterns it searches for
    "architecture_provenance_check.py",  # contains forbidden-pattern table for self scan
    "anonymity_check_results.json",  # results may quote matched lines
    "cert_anonymity_results.json",   # same
    "architecture_provenance_results.json",  # may quote matched patterns
}


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Hard-fail patterns. Each entry: (pattern_id, compiled_regex, description)
HARD_FAIL_PATTERNS = [
    ("paulc_username", re.compile(r"\bpaulc\b", re.IGNORECASE),
     "Windows username 'paulc'"),
    ("real_name_full", re.compile(r"Paul\s+Carver\s+Tiffany", re.IGNORECASE),
     "Real name 'Paul Carver Tiffany'"),
    ("real_name_concat", re.compile(r"\bPaul\s*Tiffany\b", re.IGNORECASE),
     "Real name variant 'PaulTiffany' / 'Paul Tiffany'"),
    ("real_name_lower", re.compile(r"\bpaultiffany\b", re.IGNORECASE),
     "Real name lowercase 'paultiffany'"),
    ("personal_email", re.compile(r"paulctiffany@gmail\.com", re.IGNORECASE),
     "Personal email 'paulctiffany@gmail.com'"),
    ("personal_github", re.compile(r"github\.com/PaulTiffany", re.IGNORECASE),
     "Personal GitHub URL 'github.com/PaulTiffany'"),
    ("icml_branding", re.compile(r"\bicml2026\b", re.IGNORECASE),
     "ICML 2026 branding (paper is now NeurIPS)"),
    ("icml_sty", re.compile(r"icml2026\.sty", re.IGNORECASE),
     "ICML 2026 stylesheet reference"),
    ("icml_bst", re.compile(r"icml2026\.bst", re.IGNORECASE),
     "ICML 2026 bibliography style reference"),
    # Broader symbolic-substrate program identifiers (would deanonymize via
    # the author's prior work on the surrounding research program). Patterns
    # added 2026-05-04 after BIS audit surfaced one ci/de_llm_lexicon.md leak.
    ("project_principia_symbolica",
     re.compile(r"\bPrincipia\s*Symbolica\b", re.IGNORECASE),
     "Principia Symbolica program identifier"),
    ("project_pylantern",
     re.compile(r"\bPy[\-]?Lantern\b", re.IGNORECASE),
     "PyLantern symbolic-substrate identifier"),
    ("project_fascia",
     re.compile(r"\bFascia\b", re.IGNORECASE),
     "Fascia substrate identifier"),
    ("project_cosmic_engineers",
     re.compile(r"(Order\s+of\s+)?\bCosmic\s+Engineers\b", re.IGNORECASE),
     "Cosmic Engineers community identifier"),
    ("project_srmf",
     re.compile(r"\bSRMF\b"),
     "SRMF (Symbolic Resonance Manifold Flow) substrate identifier"),
    ("project_agi26",
     re.compile(r"\bAGI[\-]?26\b", re.IGNORECASE),
     "AGI-26 follow-on paper identifier (allowed only in references.bib citation)"),
]

# File-level allowlist: meta-test files that intentionally contain the
# username string in order to verify the path-scrubber removes it. The
# tests would be meaningless without the literal token, and the files
# never get rendered into the paper or any reviewer-visible artifact.
ALLOWLISTED_FILE_SUFFIXES = (
    "supplementary/experiments/tests/test_exception_paths.py",
    "supplementary/experiments/tests/test_extended_environment_fingerprint.py",
    "supplementary/experiments/tests/test_verifier_structured_output.py",
)


def _is_allowlisted_meta_test(path: Path) -> bool:
    p = str(path).replace("\\", "/").lower()
    return any(p.endswith(suf.lower()) for suf in ALLOWLISTED_FILE_SUFFIXES)


# Allowed patterns. If a hit overlaps an allowed pattern on the same line, drop it.
ALLOWED_PATTERNS = [
    ("paul_christiano", re.compile(r"Paul\s+Christiano", re.IGNORECASE),
     "Paul Christiano (legitimate citation)"),
    ("anon_email", re.compile(r"anonymous@anonymous\.invalid", re.IGNORECASE),
     "Anonymous submitter email"),
    ("anon_submitter", re.compile(r"Anonymous\s+Submitter", re.IGNORECASE),
     "Anonymous Submitter identity"),
    ("anon_4open", re.compile(r"anonymous\.4open\.science", re.IGNORECASE),
     "Anonymous 4open.science URL"),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    file: str
    line: int
    pattern: str
    description: str
    snippet: str


@dataclass
class Report:
    hard_fail: list[Hit] = field(default_factory=list)
    warn: list[Hit] = field(default_factory=list)
    allowed_hits: int = 0
    files_scanned: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iter_text_files(root: Path) -> Iterable[Path]:
    """Yield candidate text files under root, skipping caches and binaries."""
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place so os.walk does not descend into them
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if fname in SKIP_FILES:
                continue
            p = Path(dirpath) / fname
            ext = p.suffix.lower()
            if ext in SKIP_EXTS:
                continue
            # Only inspect known text extensions to keep noise down
            if ext and ext not in TEXT_EXTS:
                continue
            yield p


def line_is_allowed(line: str, match_span: tuple[int, int]) -> bool:
    """Return True if the matched span overlaps any allowed pattern on the line."""
    m_start, m_end = match_span
    for _aid, regex, _desc in ALLOWED_PATTERNS:
        for am in regex.finditer(line):
            a_start, a_end = am.span()
            if a_start <= m_end and m_start <= a_end:
                return True
    return False


def scan_file(path: Path) -> tuple[list[Hit], int]:
    """Scan a single file. Returns (hits, allowed_hit_count)."""
    hits: list[Hit] = []
    allowed_count = 0
    if _is_allowlisted_meta_test(path):
        return hits, allowed_count
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                # Strip trailing newline for cleaner snippets
                clean = line.rstrip("\n").rstrip("\r")
                for pid, regex, desc in HARD_FAIL_PATTERNS:
                    for m in regex.finditer(clean):
                        if line_is_allowed(clean, m.span()):
                            allowed_count += 1
                            continue
                        snippet = clean.strip()
                        if len(snippet) > 240:
                            # Center snippet around the match
                            s, e = m.span()
                            lo = max(0, s - 80)
                            hi = min(len(clean), e + 80)
                            snippet = ("..." if lo > 0 else "") + clean[lo:hi].strip() + \
                                      ("..." if hi < len(clean) else "")
                        hits.append(Hit(
                            file=str(path),
                            line=lineno,
                            pattern=pid,
                            description=desc,
                            snippet=snippet,
                        ))
    except (OSError, UnicodeDecodeError) as exc:
        # Treat unreadable files as non-fatal; surface as a warn entry
        hits.append(Hit(
            file=str(path),
            line=0,
            pattern="_read_error",
            description=f"Could not read file: {exc}",
            snippet="",
        ))
    return hits, allowed_count


def classify(hit: Hit, paper_root: Path, supp_root: Path) -> str:
    """Return 'hard_fail' or 'warn' for a given hit.

    Per spec, paper/ hits are always hard_fail. Supplementary hits are also
    hard_fail by default (the spec's WARN rule is a softer relaxation we keep
    available via the dedicated 'warn' bucket). Read-errors go to warn.
    """
    if hit.pattern == "_read_error":
        return "warn"
    return "hard_fail"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> Report:
    report = Report()
    for root in (PAPER_ROOT, SUPP_ROOT):
        for fp in iter_text_files(root):
            report.files_scanned += 1
            hits, allowed = scan_file(fp)
            report.allowed_hits += allowed
            for h in hits:
                bucket = classify(h, PAPER_ROOT, SUPP_ROOT)
                if bucket == "hard_fail":
                    report.hard_fail.append(h)
                else:
                    report.warn.append(h)
    return report


def print_report(report: Report) -> None:
    print("=" * 72)
    print("NeurIPS 2026 Anonymity Check")
    print("=" * 72)
    print(f"Files scanned:  {report.files_scanned}")
    print(f"Allowed hits:   {report.allowed_hits} (Christiano / anon identity / 4open URLs)")
    print(f"Warnings:       {len(report.warn)}")
    print(f"Hard failures:  {len(report.hard_fail)}")
    print()

    if report.hard_fail:
        print("-" * 72)
        print("HARD FAIL (block submission)")
        print("-" * 72)
        for h in report.hard_fail:
            print(f"  [{h.pattern}] {h.file}:{h.line}")
            print(f"      {h.description}")
            if h.snippet:
                print(f"      > {h.snippet}")
        print()

    if report.warn:
        print("-" * 72)
        print("WARN (review but non-blocking)")
        print("-" * 72)
        for h in report.warn:
            print(f"  [{h.pattern}] {h.file}:{h.line}")
            print(f"      {h.description}")
            if h.snippet:
                print(f"      > {h.snippet}")
        print()

    if not report.hard_fail and not report.warn:
        print("Clean. No identity leaks detected.")


def write_json(report: Report) -> None:
    payload = {
        "files_scanned": report.files_scanned,
        "allowed_hits": report.allowed_hits,
        "hard_fail": [asdict(h) for h in report.hard_fail],
        "warn": [asdict(h) for h in report.warn],
    }
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main() -> int:
    report = run()
    print_report(report)
    write_json(report)
    print(f"\nJSON report written to: {RESULTS_JSON}")
    return 1 if report.hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
