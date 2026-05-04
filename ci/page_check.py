#!/usr/bin/env python3
"""NeurIPS 2026 page-budget compliance checker.

Verifies that paper/main.tex meets the Main Track Handbook (page 7)
single-PDF requirements:

  1. Paper body  (<= 9 pages of body content)
  2. References
  3. Optional appendices
  4. NeurIPS Paper Checklist

Assertions
----------
A1: Body content ends on page <= 9
A2: References starts on page <= 10
A3: NeurIPS Paper Checklist appears AFTER References AND AFTER any
    appendix sections
A4: No layout-hack commands in main.tex
    (\\enlargethispage, negative \\vspace*, \\vskip -, etc.)

Exit code: 0 on all-pass, 1 on any failure.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PAPER_DIR = REPO_ROOT / "paper"
TEX_FILE = PAPER_DIR / "main.tex"
PDF_FILE = PAPER_DIR / "main.pdf"
CI_DIR = SCRIPT_DIR
RESULTS_JSON = CI_DIR / "page_check_results.json"

BODY_PAGE_LIMIT = 9
REFERENCES_PAGE_LIMIT = 10


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a command, capturing output. Latex tools return non-zero on warnings;
    callers decide whether to treat that as fatal."""
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def build_paper() -> None:
    """Clean intermediates and run pdflatex / bibtex / pdflatex / pdflatex."""
    for ext in ("aux", "bbl", "blg", "log", "out"):
        f = PAPER_DIR / f"main.{ext}"
        if f.exists():
            f.unlink()

    pdflatex = ["pdflatex", "-interaction=nonstopmode", "-file-line-error", "main.tex"]
    bibtex = ["bibtex", "main"]

    for step in (pdflatex, bibtex, pdflatex, pdflatex):
        proc = _run(step, PAPER_DIR)
        # We do not hard-fail on non-zero (LaTeX warnings); we only fail if
        # the PDF was not produced or is stale.
        if proc.returncode != 0 and step is bibtex:
            # BibTeX returning non-zero with no .bbl is a real failure.
            if not (PAPER_DIR / "main.bbl").exists():
                raise RuntimeError(
                    f"bibtex failed with code {proc.returncode}\n"
                    f"stdout:\n{proc.stdout[-1000:]}\n"
                    f"stderr:\n{proc.stderr[-1000:]}"
                )

    if not PDF_FILE.exists():
        raise RuntimeError(f"PDF was not produced at {PDF_FILE}")


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def extract_pages() -> list[str]:
    """Run `pdftotext -layout main.pdf -` and split on form-feed."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        raise RuntimeError("pdftotext not found on PATH")

    proc = subprocess.run(
        [pdftotext, "-layout", str(PDF_FILE), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {proc.stderr}")

    return proc.stdout.split("\f")


# ---------------------------------------------------------------------------
# Marker location
# ---------------------------------------------------------------------------


_REFERENCES_RE = re.compile(r"^\s*\d*\s*References\s*$", re.MULTILINE)
_CHECKLIST_RE = re.compile(r"NeurIPS\s+Paper\s+Checklist", re.IGNORECASE)
# Appendix section headings: "A Title", "B Title", or "Appendix A ..." style.
# We look for a top-level appendix marker (lone capital letter at start of a
# line followed by a title), but the most reliable cue is the literal token
# "Appendix" used as a section header.
_APPENDIX_HEADER_RE = re.compile(
    r"^\s*(Appendix\s+[A-Z]\b|[A-Z]\s+[A-Z][A-Za-z].{0,80})\s*$", re.MULTILINE
)


def find_references_page(pages: list[str]) -> Optional[int]:
    """First page whose layout has 'References' as a heading-like line."""
    for i, page in enumerate(pages, 1):
        if _REFERENCES_RE.search(page):
            return i
    return None


def find_checklist_page(pages: list[str]) -> Optional[int]:
    for i, page in enumerate(pages, 1):
        if _CHECKLIST_RE.search(page):
            return i
    return None


def find_first_appendix_page(
    pages: list[str], references_page: Optional[int]
) -> Optional[int]:
    """First page after References that introduces an appendix section.

    NeurIPS appendices typeset as single-letter section headers, e.g.
    ``D Embedding-Space Details``. With line-number prefixes from the NeurIPS
    style (``361 D Embedding-Space Details``), the header appears as an
    optional integer, then a single capital letter, then a Title Case heading.
    Also accepts the literal ``Appendix X`` form.
    """
    if references_page is None:
        return None
    appendix_header_re = re.compile(
        r"^\s*(?:\d+\s+)?(?:Appendix\s+[A-Z]\b|[A-Z]\s+[A-Z][A-Za-z][^\n]{0,120})\s*$",
        re.MULTILINE,
    )
    # Start strictly AFTER the references page (the references page itself
    # frequently contains author-name initials that look like ``A Author ...``).
    for i in range(references_page + 1, len(pages) + 1):
        page = pages[i - 1]
        if appendix_header_re.search(page):
            return i
    return None


def find_body_end_page(pages: list[str], references_page: Optional[int]) -> int:
    """The last page of body content is the page just before References.

    NeurIPS counts everything before References toward the body budget, so
    body_end_page = references_page - 1 (or the last page of the doc if there
    is no References section, which would be a separate failure).
    """
    if references_page is None:
        return len(pages)
    return references_page - 1


# ---------------------------------------------------------------------------
# Layout-hack scan
# ---------------------------------------------------------------------------


_LAYOUT_HACK_PATTERNS = [
    # Pattern, human-readable name
    (re.compile(r"\\enlargethispage\b"), r"\enlargethispage"),
    (re.compile(r"\\vspace\*\s*\{\s*-"), r"\vspace*{-...}"),
    (re.compile(r"\\vspace\s*\{\s*-"), r"\vspace{-...}"),
    (re.compile(r"\\vskip\s*-"), r"\vskip -..."),
    (re.compile(r"\\addtolength\s*\{\s*\\textheight\s*\}"), r"\addtolength{\textheight}"),
    (re.compile(r"\\addtolength\s*\{\s*\\topmargin\s*\}"), r"\addtolength{\topmargin}"),
    (re.compile(r"\\setlength\s*\{\s*\\textheight\s*\}"), r"\setlength{\textheight}"),
]


def scan_layout_hacks(tex_path: Path) -> list[dict]:
    hits: list[dict] = []
    text = tex_path.read_text(encoding="utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), 1):
        # Strip comments (anything after an unescaped %).
        stripped = re.sub(r"(?<!\\)%.*$", "", line)
        for pat, name in _LAYOUT_HACK_PATTERNS:
            if pat.search(stripped):
                hits.append(
                    {"line": line_no, "pattern": name, "text": line.strip()}
                )
    return hits


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def evaluate() -> tuple[list[dict], dict]:
    pages = extract_pages()
    references_page = find_references_page(pages)
    checklist_page = find_checklist_page(pages)
    first_appendix_page = find_first_appendix_page(pages, references_page)
    body_end_page = find_body_end_page(pages, references_page)
    hacks = scan_layout_hacks(TEX_FILE)

    results: list[dict] = []

    # A1: body ends on page <= 9
    a1_pass = body_end_page <= BODY_PAGE_LIMIT
    results.append(
        {
            "assertion_id": "A1",
            "description": f"Body content ends on page <= {BODY_PAGE_LIMIT}",
            "status": "PASS" if a1_pass else "FAIL",
            "details": {
                "body_end_page": body_end_page,
                "limit": BODY_PAGE_LIMIT,
            },
        }
    )

    # A2: references starts on page <= 10
    a2_pass = references_page is not None and references_page <= REFERENCES_PAGE_LIMIT
    results.append(
        {
            "assertion_id": "A2",
            "description": f"References starts on page <= {REFERENCES_PAGE_LIMIT}",
            "status": "PASS" if a2_pass else "FAIL",
            "details": {
                "references_page": references_page,
                "limit": REFERENCES_PAGE_LIMIT,
            },
        }
    )

    # A3: checklist after references and after any appendix
    if checklist_page is None or references_page is None:
        a3_pass = False
        a3_reason = "checklist or references not found"
    else:
        after_refs = checklist_page > references_page
        after_appendix = (
            first_appendix_page is None or checklist_page >= first_appendix_page
        )
        a3_pass = after_refs and after_appendix
        a3_reason = (
            f"checklist={checklist_page}, references={references_page}, "
            f"first_appendix={first_appendix_page}"
        )
    results.append(
        {
            "assertion_id": "A3",
            "description": "NeurIPS Paper Checklist appears AFTER References AND AFTER any appendix sections",
            "status": "PASS" if a3_pass else "FAIL",
            "details": {
                "checklist_page": checklist_page,
                "references_page": references_page,
                "first_appendix_page": first_appendix_page,
                "reason": a3_reason,
            },
        }
    )

    # A4: no layout-hack commands
    a4_pass = len(hacks) == 0
    results.append(
        {
            "assertion_id": "A4",
            "description": r"No \enlargethispage, negative \vspace*/\vskip, or \textheight/\topmargin tweaks in main.tex",
            "status": "PASS" if a4_pass else "FAIL",
            "details": {"matches": hacks, "count": len(hacks)},
        }
    )

    summary = {
        "total_pages": len(pages),
        "body_pages": body_end_page,
        "references_page": references_page,
        "checklist_page": checklist_page,
        "first_appendix_page": first_appendix_page,
        "layout_hack_count": len(hacks),
        "all_pass": all(r["status"] == "PASS" for r in results),
    }
    return results, summary


def print_report(results: list[dict], summary: dict) -> None:
    print("=" * 72)
    print("NeurIPS 2026 page-budget check")
    print("=" * 72)
    for r in results:
        marker = "OK  " if r["status"] == "PASS" else "FAIL"
        print(f"[{marker}] {r['assertion_id']}: {r['description']}")
        details = r["details"]
        if r["assertion_id"] == "A4" and details["count"] > 0:
            for hit in details["matches"]:
                print(f"        line {hit['line']}: {hit['pattern']}  ->  {hit['text']}")
        else:
            for k, v in details.items():
                if k == "matches":
                    continue
                print(f"        {k}: {v}")
    print("-" * 72)
    print("Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 72)


def main() -> int:
    print("[page_check] building paper ...")
    try:
        build_paper()
    except Exception as exc:
        print(f"[page_check] BUILD FAILURE: {exc}", file=sys.stderr)
        return 1

    print("[page_check] evaluating ...")
    results, summary = evaluate()
    print_report(results, summary)

    CI_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"assertions": results, "summary": summary}
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[page_check] wrote {RESULTS_JSON}")

    return 0 if summary["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
