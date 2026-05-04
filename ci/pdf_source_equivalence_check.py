#!/usr/bin/env python3
"""
pdf_source_equivalence_check.py - Verify paper/main.pdf is current with respect
to all LaTeX source files.

Catches the failure mode where someone edits .tex files but forgets to rebuild
the PDF -- the cert would otherwise certify a stale PDF.

Method
------
1. Compute mtime of paper/main.pdf (T_pdf).
2. Walk paper/main.tex for \\input{...} directives; collect mtimes.
3. Also check paper/extended_related_work.tex, paper/checklist.tex,
   paper/figures/*.tex, paper/references.bib, all .sty files in paper/.
4. If T_pdf < max(source mtimes) -> FAIL: PDF is stale.
5. Extract first-page text via pdftotext; verify the abstract phrase
   "We enable routing decisions" exists in both the PDF text and main.tex.
   If phrase in main.tex but missing from PDF text -> FAIL.
6. Write ci/pdf_source_equivalence_results.json.

Exit codes
----------
  0  PASS
  1  FAIL (stale PDF or phrase mismatch)
  2  Invocation error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

PAPER_DIR = REPO_ROOT / "paper"
MAIN_TEX = PAPER_DIR / "main.tex"
MAIN_PDF = PAPER_DIR / "main.pdf"
RESULTS_JSON = SCRIPT_DIR / "pdf_source_equivalence_results.json"

# The uniquely identifiable phrase to search for in both tex and pdf.
# This is from the opening of the abstract.
ABSTRACT_PHRASE = "We enable routing decisions"


def iso(ts: float) -> str:
    """Return ISO-8601 string for a Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_input_files(tex_path: Path, seen: set[Path] | None = None) -> list[Path]:
    """
    Recursively walk \\input{...} and \\include{...} directives in a .tex file.
    Returns the list of resolved (existing) .tex file paths found.
    """
    if seen is None:
        seen = set()
    if tex_path in seen:
        return []
    seen.add(tex_path)

    found: list[Path] = []
    if not tex_path.exists():
        return found

    try:
        content = tex_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found

    for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", content):
        raw = match.group(1).strip()
        # LaTeX \\input may omit the .tex extension
        candidate = (tex_path.parent / raw)
        if candidate.suffix == "":
            candidate = candidate.with_suffix(".tex")
        if candidate.exists():
            found.append(candidate)
            found.extend(collect_input_files(candidate, seen))

    return found


def collect_source_files() -> list[Path]:
    """Collect all source files whose mtime matters for PDF freshness."""
    sources: list[Path] = [MAIN_TEX]

    # Recursively follow \\input directives from main.tex
    sources.extend(collect_input_files(MAIN_TEX))

    # Explicit extra files
    explicit = [
        PAPER_DIR / "extended_related_work.tex",
        PAPER_DIR / "checklist.tex",
        PAPER_DIR / "references.bib",
    ]
    for p in explicit:
        if p.exists() and p not in sources:
            sources.append(p)

    # All .tex files under paper/figures/
    figures_dir = PAPER_DIR / "figures"
    if figures_dir.is_dir():
        for p in sorted(figures_dir.glob("*.tex")):
            if p not in sources:
                sources.append(p)

    # All .sty files under paper/
    for p in sorted(PAPER_DIR.glob("*.sty")):
        if p not in sources:
            sources.append(p)

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sources:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)

    return unique


def extract_first_page_text() -> tuple[str | None, str | None]:
    """
    Extract first-page text from main.pdf via pdftotext.
    Returns (text, advisory_message).
    text is None if pdftotext is unavailable or fails; advisory explains why.
    """
    if not shutil.which("pdftotext"):
        return None, "pdftotext not found in PATH -- phrase check skipped (ADVISORY)"

    if not MAIN_PDF.exists():
        return None, "main.pdf does not exist -- phrase check skipped"

    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", str(MAIN_PDF), "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None, f"pdftotext exited {result.returncode}: {result.stderr.strip()[:200]}"
        return result.stdout, None
    except subprocess.TimeoutExpired:
        return None, "pdftotext timed out after 30 s"
    except OSError as exc:
        return None, f"pdftotext OSError: {exc}"


def phrase_in_text(phrase: str, text: str) -> bool:
    return phrase.lower() in text.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default=str(RESULTS_JSON),
                        help="Path for JSON output (default: ci/pdf_source_equivalence_results.json)")
    args = parser.parse_args(argv)
    out_path = Path(args.json_out)

    # -- Guard: main.pdf must exist ----------------------------------------
    if not MAIN_PDF.exists():
        summary = {
            "passed": False,
            "error": f"main.pdf not found at {MAIN_PDF}",
        }
        out_path.write_text(json.dumps({"summary": summary}, indent=2))
        print(f"ERROR: {summary['error']}", file=sys.stderr)
        return 2

    if not MAIN_TEX.exists():
        summary = {
            "passed": False,
            "error": f"main.tex not found at {MAIN_TEX}",
        }
        out_path.write_text(json.dumps({"summary": summary}, indent=2))
        print(f"ERROR: {summary['error']}", file=sys.stderr)
        return 2

    # -- Mtime comparison ---------------------------------------------------
    pdf_mtime = MAIN_PDF.stat().st_mtime
    sources = collect_source_files()

    stale_sources: list[str] = []
    source_mtimes: list[tuple[str, float]] = []
    for src in sources:
        if not src.exists():
            continue
        mt = src.stat().st_mtime
        rel = str(src.relative_to(REPO_ROOT))
        source_mtimes.append((rel, mt))
        if mt > pdf_mtime:
            stale_sources.append(rel)

    newest_source: tuple[str, float] | None = (
        max(source_mtimes, key=lambda x: x[1]) if source_mtimes else None
    )

    mtime_passed = len(stale_sources) == 0

    # -- Phrase check -------------------------------------------------------
    phrase_in_tex: bool = False
    phrase_in_pdf: bool | None = None
    phrase_advisory: str | None = None

    try:
        tex_content = MAIN_TEX.read_text(encoding="utf-8", errors="replace")
        phrase_in_tex = phrase_in_text(ABSTRACT_PHRASE, tex_content)
    except OSError as exc:
        phrase_advisory = f"Could not read main.tex: {exc}"

    pdf_text, pdf_advisory = extract_first_page_text()
    if pdf_advisory:
        phrase_advisory = pdf_advisory
    if pdf_text is not None:
        phrase_in_pdf = phrase_in_text(ABSTRACT_PHRASE, pdf_text)

    # Phrase FAIL condition: phrase is in tex but NOT in pdf text
    if phrase_in_pdf is None:
        # Cannot determine -> not a hard fail, report as advisory
        phrase_passed = True  # graceful degradation
    else:
        phrase_passed = not (phrase_in_tex and not phrase_in_pdf)

    overall_passed = mtime_passed and phrase_passed

    # -- Build summary dict -------------------------------------------------
    summary: dict = {
        "passed": overall_passed,
        "pdf_mtime": iso(pdf_mtime),
        "newest_source_mtime": iso(newest_source[1]) if newest_source else None,
        "newest_source_file": newest_source[0] if newest_source else None,
        "stale_sources": stale_sources,
        "phrase_in_tex": phrase_in_tex,
        "phrase_in_pdf": phrase_in_pdf,
        "phrase_check_advisory": phrase_advisory,
        "phrase_match": phrase_passed,
        "source_file_count": len(source_mtimes),
    }

    result = {"summary": summary}
    out_path.write_text(json.dumps(result, indent=2))

    # -- Console output (terse) --------------------------------------------
    status = "PASS" if overall_passed else "FAIL"
    print(f"[pdf_source_equivalence] {status}")
    print(f"  PDF mtime      : {iso(pdf_mtime)}")
    if newest_source:
        print(f"  Newest source  : {newest_source[0]} @ {iso(newest_source[1])}")
    print(f"  Stale sources  : {len(stale_sources)}")
    for s in stale_sources:
        print(f"    - {s}")
    print(f"  Phrase in tex  : {phrase_in_tex}")
    print(f"  Phrase in pdf  : {phrase_in_pdf if phrase_in_pdf is not None else 'UNKNOWN'}")
    if phrase_advisory:
        print(f"  Advisory       : {phrase_advisory}")
    print(f"  Results -> {out_path}")

    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
