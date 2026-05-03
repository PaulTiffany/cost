#!/usr/bin/env python3
"""
author_claims_check.py - L16: author-judgment claim audit (advisory).

Greps paper/main.tex for first-person interpretive claim markers
("we observe", "we find", "demonstrates", "is consistent with", etc.)
and for each occurrence, checks whether a data anchor appears in a
600-character window around the claim. Anchors are: \\ref{}, \\cite{},
numeric values, \\texttt{} references to JSON or .py files, or any L15
data-tied claim's source file path.

A judgment claim that has no anchor in its window is flagged as
UNTIED. The layer reports tied/untied counts and a list of UNTIED
locations for author review.

Design constraints:
  - Advisory only (always returns 0). A hard pass/fail threshold on
    "tied ratio" would be Goodhart-prone in the same way the ICML
    paper_quality.py check_results_discussion was: it would incentivize
    sprinkling \\ref{} and numbers near judgment verbs without
    actually tying the claim to data.
  - The metric exists to surface candidate untied claims for the
    author. The author decides whether to fix (add anchor), accept
    (the claim genuinely doesn't need a data tie), or rephrase.
  - L16 explicitly does NOT count discussion-word density. It counts
    untied judgment claims. The unit is the claim, not the word.

Complements:
  - L1/L3/L5: claim text appears in paper artifact (presence)
  - L9:       formula evaluates to declared value (math identity)
  - L15:      claim value matches recomputation from source (data identity)
  - L16:      author-judgment claims have anchors in their context (this)

Exit codes
----------
  0  always (advisory layer; non-blocking)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = SCRIPT_DIR / "author_claims_results.json"


# Judgment markers: first-person interpretive verbs and inferential phrases.
# Word boundaries on each so we don't match substrings.
JUDGMENT_PATTERNS = [
    r"\bwe\s+observe\b",
    r"\bwe\s+find\b",
    r"\bwe\s+judge\b",
    r"\bwe\s+(?:hand-?rated|hand-?coded)\b",
    r"\bwe\s+treat\s+(?:this|these|the\s+\w+)\s+as\s+evidence\b",
    r"\bwe\s+treat\s+this\s+as\b",
    r"\bwe\s+interpret\b",
    r"\bwe\s+infer\b",
    r"\bwe\s+expect\b",
    r"\bwe\s+conclude\b",
    r"\bwe\s+(?:rate|rated)\b",
    r"\bwe\s+(?:argue|claim)\b",
    r"\bis\s+consistent\s+with\b",
    r"\bdemonstrates?\b",
    r"\bshows?\s+that\b",
    r"\bestablish(?:es|ed)\b",
    r"\bthis\s+is\s+itself\b",
    r"\bsuggests?\s+that\b",
    r"\bis\s+suggestive\b",
    r"\bqualitatively\b",
]


# Anchor patterns: presence of any of these in the 600-char window
# around the judgment marker indicates the claim is tied to something
# verifiable. Citations, refs, numbers, and code-path mentions all
# qualify.
ANCHOR_PATTERNS = [
    r"\\(?:ref|eqref|hyperref|cref|Cref|autoref)\{[^}]+\}",
    r"\\cite[a-z]*\{[^}]+\}",
    r"\\(?:input|includegraphics)\{[^}]+\}",
    r"\\texttt\{[^}]*\.(?:json|py|tex|md|sh|ps1|csv|npy|pdf)[^}]*\}",
    r"\\texttt\{[^}]*/[^}]+\}",  # any path-like texttt{} reference
    r"\b\d+(?:\.\d+)?%?\b",     # numeric value (decimal or percent)
    r"\bN\s*[=\\{]\s*\d",        # N=... declarations
]


WINDOW_CHARS = 300  # before and after the judgment marker => 600-char window


@dataclass
class ClaimRecord:
    pattern: str
    line: int
    col: int
    context: str
    has_anchor: bool
    matched_anchors: list[str]


def strip_comments(tex: str) -> str:
    """Strip LaTeX comments (% to end of line, unless escaped)."""
    out_lines = []
    for line in tex.split("\n"):
        i = 0
        n = len(line)
        while i < n:
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                line = line[:i]
                break
            i += 1
        out_lines.append(line)
    return "\n".join(out_lines)


def line_col_at(text: str, offset: int) -> tuple[int, int]:
    """Return (line_number, column) for the given byte offset (1-indexed line)."""
    line = text.count("\n", 0, offset) + 1
    last_nl = text.rfind("\n", 0, offset)
    col = offset - last_nl if last_nl >= 0 else offset + 1
    return line, col


def find_judgment_claims(tex: str) -> list[ClaimRecord]:
    records: list[ClaimRecord] = []
    for pat in JUDGMENT_PATTERNS:
        for m in re.finditer(pat, tex, flags=re.IGNORECASE):
            start = max(0, m.start() - WINDOW_CHARS)
            end = min(len(tex), m.end() + WINDOW_CHARS)
            window = tex[start:end]

            matched = []
            for apat in ANCHOR_PATTERNS:
                hits = re.findall(apat, window)
                # Don't credit the matched judgment marker itself if it
                # accidentally matches an anchor pattern (none of ours
                # do, but be defensive).
                matched.extend(hits[:3])  # cap to avoid noise

            line, col = line_col_at(tex, m.start())
            # Compact one-line context: collapse whitespace, truncate
            ctx_raw = tex[max(0, m.start() - 60): m.end() + 60]
            ctx = re.sub(r"\s+", " ", ctx_raw).strip()
            if len(ctx) > 200:
                ctx = ctx[:200] + "..."

            records.append(ClaimRecord(
                pattern=pat,
                line=line,
                col=col,
                context=ctx,
                has_anchor=len(matched) > 0,
                matched_anchors=matched[:5],
            ))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                       formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print every claim, not just untied ones")
    args = parser.parse_args()

    if not MAIN_TEX.exists():
        print(f"ERROR: {MAIN_TEX} not found", file=sys.stderr)
        return 0  # advisory: don't block

    tex_raw = MAIN_TEX.read_text(encoding="utf-8")
    tex = strip_comments(tex_raw)

    records = find_judgment_claims(tex)
    n_total = len(records)
    n_tied = sum(1 for r in records if r.has_anchor)
    n_untied = n_total - n_tied
    ratio = (n_tied / n_total) if n_total else 1.0

    print("=" * 70)
    print("AUTHOR-JUDGMENT CLAIM AUDIT  (advisory)")
    print("=" * 70)
    print(f"main.tex:           {MAIN_TEX.relative_to(REPO_ROOT)}")
    print(f"judgment markers:   {n_total}")
    print(f"  tied (anchor):    {n_tied}")
    print(f"  untied:           {n_untied}")
    print(f"  ratio:            {100 * ratio:.1f}%")
    print(f"  window:           +/-{WINDOW_CHARS} chars (600-char window total)")
    print()

    if n_untied > 0:
        print("UNTIED claims (no anchor in surrounding context):")
        print("-" * 70)
        for r in records:
            if r.has_anchor:
                continue
            print(f"  line {r.line:5d}  pattern={r.pattern[:30]!r}")
            print(f"               {r.context}")
        print()

    if args.verbose and n_tied > 0:
        print("TIED claims (anchor present):")
        print("-" * 70)
        for r in records:
            if not r.has_anchor:
                continue
            anchors = ", ".join(repr(a)[:30] for a in r.matched_anchors[:2])
            print(f"  line {r.line:5d}  pattern={r.pattern[:30]!r}  anchors=[{anchors}]")
        print()

    payload = {
        "summary": {
            "total": n_total,
            "tied": n_tied,
            "untied": n_untied,
            "tied_ratio": ratio,
            "window_chars": WINDOW_CHARS,
        },
        "judgment_patterns": JUDGMENT_PATTERNS,
        "anchor_patterns": ANCHOR_PATTERNS,
        "records": [asdict(r) for r in records],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON.relative_to(REPO_ROOT)}")
    print()
    print("Note: L16 is advisory. The metric exists to surface candidate untied")
    print("claims for author review; it does not block the cert. The author")
    print("decides per-claim whether to add an anchor, rephrase, or accept.")

    return 0  # always pass; advisory only


if __name__ == "__main__":
    sys.exit(main())
