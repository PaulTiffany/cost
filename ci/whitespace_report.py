#!/usr/bin/env python3
"""
whitespace_report.py

Reports body-text lines in paper/main.pdf that have measurable trailing
slack, ranked by slack size. Useful for finding tiny anchor opportunities
without scanning the whole rendered PDF visually.

Method:
  1. Render PDF to fixed-width text via pdftotext -layout (preserves
     visual line structure; trailing chars are real end-of-line
     whitespace in the typeset output).
  2. Drop short lines (captions, headings, fragments under 30 chars) and
     compute the mode of the remaining lengths as the body-column width.
  3. For every body line shorter than that target, emit (page, line,
     slack_chars, preview_of_ending) so the author can match an anchor
     to a place that has room for it.

This script is a tool, not a cert layer. The report is informational and
does not gate anything.

Usage:
    python ci/whitespace_report.py
    python ci/whitespace_report.py --top 50 --min-slack 10
    python ci/whitespace_report.py --section "intro"
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PDF = REPO_ROOT / "paper" / "main.pdf"
OUTPUT = Path(__file__).resolve().parent / "whitespace_report.json"

# Skip any line whose ending matches one of these (likely caption/heading/
# table/equation/list-item; not a paragraph candidate for tiny anchors).
SKIP_ENDING_RX = re.compile(
    r"(?:Figure\s+\d+:|Table\s+\d+:|Algorithm\s+\d+:|^\s*\(\w+\)\s*$|"
    r"^\s*\d+\.\s*$|^\s*[A-Z]\.\d+\s*$)",
    re.IGNORECASE,
)


def pdftotext_layout(pdf: Path) -> str:
    res = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
    )
    if res.returncode != 0:
        raise RuntimeError(f"pdftotext exit {res.returncode}: {res.stderr[:200]}")
    return res.stdout


def _safe_print(s: str = "", file=sys.stdout) -> None:
    """Strip characters that the local console codec cannot encode (Windows
    cp1252 chokes on U+FFFD which our error='replace' subprocess emits)."""
    try:
        sys.stdout.write(s + "\n") if file is sys.stdout else file.write(s + "\n")
    except UnicodeEncodeError:
        clean = s.encode("ascii", "replace").decode("ascii")
        if file is sys.stdout:
            sys.stdout.write(clean + "\n")
        else:
            file.write(clean + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=25, help="how many findings to print to stdout")
    parser.add_argument("--min-slack", type=int, default=15, help="minimum trailing slack chars to report")
    parser.add_argument("--page-min", type=int, default=1, help="restrict to pages >= this number")
    parser.add_argument("--page-max", type=int, default=10, help="restrict to pages <= this number (default body only)")
    parser.add_argument("--all-pages", action="store_true", help="include all pages incl. appendices")
    args = parser.parse_args()

    if not MAIN_PDF.exists():
        _safe_print(f"main.pdf not found at {MAIN_PDF}", file=sys.stderr); return 1
    try:
        text = pdftotext_layout(MAIN_PDF)
    except Exception as e:
        _safe_print(f"render failed: {e}", file=sys.stderr); return 1

    pages = text.split("\f")
    page_max = len(pages) if args.all_pages else args.page_max

    # A line is "body prose" if:
    #  - it is at least 50 chars long (filters captions/headings/bullets)
    #  - it does NOT contain a run of 4+ consecutive interior spaces
    #    (filters table rows and multi-column layout)
    #  - it has at least 8 alphabetic words of length >= 3
    #    (filters numeric/table rows that survived the space check)
    interior_gap_rx = re.compile(r"\S {4,}\S")
    word_rx = re.compile(r"[A-Za-z]{3,}")
    body_lines: list[tuple[int, int, int, str]] = []  # (page, line, len, text)
    for p_no, page in enumerate(pages, 1):
        if not (args.page_min <= p_no <= page_max):
            continue
        for l_no, line in enumerate(page.splitlines(), 1):
            stripped = line.rstrip()
            if len(stripped.lstrip()) < 50:
                continue
            inner = stripped.strip()
            if interior_gap_rx.search(inner):
                continue
            if len(word_rx.findall(inner)) < 8:
                continue
            if SKIP_ENDING_RX.search(stripped[-30:]):
                continue
            body_lines.append((p_no, l_no, len(stripped), stripped))

    if not body_lines:
        _safe_print("No body lines found in the requested page range."); return 0

    # Body column width = mode of line lengths in the right tail (>= 70 chars).
    long_lengths = [l for _, _, l, _ in body_lines if l >= 70]
    if long_lengths:
        cnt = Counter(long_lengths)
        target = cnt.most_common(1)[0][0]
    else:
        target = max(l for _, _, l, _ in body_lines)

    findings = []
    for p_no, l_no, length, txt in body_lines:
        slack = target - length
        if slack < args.min_slack:
            continue
        findings.append({
            "page": p_no,
            "pdf_line": l_no,
            "slack_chars": slack,
            "line_chars": length,
            "ending": txt[-60:],
        })
    findings.sort(key=lambda f: -f["slack_chars"])

    payload = {
        "_meta": {
            "pdf": str(MAIN_PDF.relative_to(REPO_ROOT)),
            "target_column_chars": target,
            "n_body_lines_analyzed": len(body_lines),
            "n_findings_total": len(findings),
            "page_range": [args.page_min, page_max],
            "min_slack_threshold": args.min_slack,
            "note": "Tool, not a cert layer. Each finding is a candidate spot for a tiny inline claim anchor (\\appref{} / \\secref{} / Tab.~/Fig.~/Thm).",
        },
        "findings": findings,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    _safe_print(f"PDF: {MAIN_PDF.name}  pages {args.page_min}-{page_max}")
    _safe_print(f"Target body-column width (mode of long lines): {target} chars")
    _safe_print(f"Body lines analyzed: {len(body_lines)}; with slack >= {args.min_slack}: {len(findings)}")
    _safe_print()
    if findings:
        _safe_print(f"{'page':<5}{'pdf_line':<10}{'slack':<7}{'len':<5}  ending preview")
        for f in findings[: args.top]:
            _safe_print(f"  {f['page']:<3}  {f['pdf_line']:<8}{f['slack_chars']:<7}{f['line_chars']:<5}  ...{f['ending']}")
        if len(findings) > args.top:
            _safe_print(f"  ... ({len(findings) - args.top} more in {OUTPUT.name})")
    _safe_print()
    _safe_print(f"JSON: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
