#!/usr/bin/env python3
"""
claim_coverage_sweep.py - Reverse-coverage: which numbers in the paper
are not covered by any registered claim?

Layer 3 of the claim certification stack:
  1. claim_audit.py           - "do registered claims appear in the paper?"
  2. claim_audit_validator.py - "is the registry self-consistent?"
  3. claim_coverage_sweep.py  - "does every interesting number in the
                                 paper have a claim covering it?"

This script walks main.tex, extracts numeric tokens with context, drops
incidentals (Section/Figure/Table refs, citation years, LaTeX dimensions),
and reports survivors that no claim pattern matches. The output is a
triage list for the human, not a verdict: each unmatched number is a
candidate for either a new claim or an "incidental, ignore" decision.

Usage:
  python claim_coverage_sweep.py             # summary + write triage CSV
  python claim_coverage_sweep.py --verbose   # print every uncovered hit
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CLAIM_AUDIT_PY = SCRIPT_DIR / "claim_audit.py"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
TRIAGE_JSON = SCRIPT_DIR / "claim_coverage_uncovered.json"

# ---------------------------------------------------------------------------
# Numeric token extractor
# ---------------------------------------------------------------------------
# Match decimals, percentages, ratios, counts, thousand-separated values.
# Examples it should catch:
#   0.023, 1.4142, 91%, 91\%, 4,800, 4{,}800, 26.4x, 26.4\times, 0.67$\times$,
#   $r_s = 1.0$, N=60, $\hat{L} = 0.025$
NUMERIC = re.compile(
    r"""
    (?<![A-Za-z_0-9])           # not preceded by word char OR digit
                                # (the digit exclusion catches e.g. '004' as
                                # tail of '2004' inside boyd2004convex citekey)
    (?:                         # the numeric token itself:
        \d+\{?,\}?\d+(?:\.\d+)?  #   4,800 / 4{,}800 / 4{,}800.5
      | \d+\.\d+                 #   0.023, 1.4142
      | \d+                      #   60, 100, 4800
    )
    """,
    re.VERBOSE,
)

# Lines / regions to skip wholesale.
COMMENT_LINE = re.compile(r"^\s*%")          # full-line LaTeX comment
COMMENT_INLINE = re.compile(r"(?<!\\)%.*$")  # trailing comment (after non-escaped %)

# Environments whose interior is graphics/pseudocode/figure code, not claims.
# We track \begin{X} ... \end{X} pairs and skip every numeric token inside.
BLOCK_SKIP_ENVS = {
    "tikzpicture",
    "algorithm",      # caption/spec wrappers (algorithmic body counted via inner)
    "algorithmic",    # pseudocode body — thresholds covered by C13/C14 patterns
    "axis",           # pgfplots
    "pgfplots",
    "scope",
    "lstlisting",
    "verbatim",
    "Verbatim",
    "minted",
    "filecontents",
    "filecontents*",
    "comment",
}
BEGIN_ENV = re.compile(r"\\begin\{([A-Za-z*]+)\}")
END_ENV = re.compile(r"\\end\{([A-Za-z*]+)\}")

# Numeric tokens we treat as incidental (not empirical claims):
INCIDENTAL_PATTERNS = [
    # \ref / \eqref / \cite / \label / \pageref / \autoref / \cref
    re.compile(r"\\(?:eq|page|auto|c|name|v)?ref\{[^}]*\}"),
    re.compile(r"\\cite[a-z]*(?:\[[^\]]*\])?\{[^}]*\}"),  # \citep[Sec.~1.2]{key} too
    re.compile(r"\\label\{[^}]*\}"),
    # Table layout: \multicolumn{N}{...}, \cmidrule(lr){N-M}, \multirow{N}
    re.compile(r"\\multicolumn\{[^}]*\}"),
    re.compile(r"\\multirow\{[^}]*\}"),
    re.compile(r"\\cmidrule(?:\([a-z]+\))?\{\d+-\d+\}"),
    # URLs: any digits inside an \href{URL}{text} or bare http(s):// link
    re.compile(r"\\href\{[^}]*\}"),
    re.compile(r"https?://[^\s}]+"),
    # Section/Figure/Table/Algorithm/Theorem/Equation/Lemma/Corollary X.Y
    re.compile(r"(?:Section|Sec\.|Figure|Fig\.|Table|Tab\.|Algorithm|Alg\.|Theorem|Thm\.|Lemma|Corollary|Cor\.|Equation|Eq\.|Definition|Def\.|Appendix|App\.|Remark)\s*\\?ref\{[^}]*\}"),
    re.compile(r"(?:Section|Sec\.|Figure|Fig\.|Table|Tab\.|Algorithm|Alg\.|Theorem|Thm\.|Lemma|Corollary|Cor\.|Equation|Eq\.|Definition|Def\.|Appendix|App\.|Remark)\s*\d+(?:\.\d+)?"),
    # \input{...}, \includegraphics{...}, \usepackage[...]
    re.compile(r"\\(?:input|include|includegraphics|usepackage)\b"),
    # LaTeX dimensions: 0.4\linewidth, 12pt, 5em, 2cm, 0.5\columnwidth
    re.compile(r"\d+(?:\.\d+)?\s*(?:pt|em|ex|cm|mm|in|bp|sp|\\linewidth|\\columnwidth|\\textwidth|\\textheight|\\baselineskip|\\hsize|\\vsize)\b"),
    # Color / RGB
    re.compile(r"\\(?:color|definecolor|rowcolor|cellcolor)\b"),
    # Document structure / setup
    re.compile(r"\\(?:documentclass|setlength|addtolength|setcounter|addtocounter|renewcommand|newcommand|def)\b"),
]

# Whole-line incidental indicators (any number on the line is incidental).
LINE_LEVEL_INCIDENTAL = [
    re.compile(r"\\definecolor\b"),
    re.compile(r"\\(?:row|cell|page)?color\{"),
    re.compile(r"\\(?:hspace|vspace|hskip|vskip|hphantom|vphantom)\b"),
    re.compile(r"\\(?:setlength|addtolength|setcounter|addtocounter)\b"),
    # Custom command definitions: \newcommand{\foo}[N]{...}, \def\foo{...}
    re.compile(r"\\(?:def|newcommand|renewcommand|providecommand|DeclareMathOperator)\b"),
    re.compile(r"^\s*\(\d+\)~\\textbf"),  # "(1)~\textbf{Foo:}" enumeration
    # Enumeration markers like "1. ", "(1)", "(a)" at start of line
    re.compile(r"^\s*\(?\d+[\.)]\s"),
    # \pgfplotsset compat strings, \usepackage version pins
    re.compile(r"\\pgfplotsset\b"),
    re.compile(r"\\usepackage\b"),
]

# Per-token math-notation filters: numeric tokens that are clearly LaTeX
# math syntax rather than empirical values.
MATH_NOTATION_PATTERNS = [
    # Numeric exponent: 2^k, 2^{k}, 10^{-3}
    re.compile(r"\d+\^\{?[A-Za-z\\\-\d]"),
    # Numeric coefficient on Greek letter or math token: 2\sigma, 2\pi, 2\delta
    re.compile(r"\d+\\[A-Za-z]+"),
    # Interval/tuple notation: (0,1], [0,1), (0,1)
    re.compile(r"[\[(]\s*\d+\s*,\s*\d+\s*[\])]"),
    # \binom{n}{k}, \frac{n}{k}, \sqrt[n]{...}
    re.compile(r"\\(?:binom|frac|sqrt|tfrac|dfrac)\{[^}]*\}\{[^}]*\}"),
    # k=2 / k{=}2 inside math (small constants in formulas)
    re.compile(r"\b[a-zA-Z]\s*\{?=\}?\s*\d+\b"),
    # \mathbf{1}, \mathbf{0}: vector-of-ones notation
    re.compile(r"\\mathbf\{\d\}"),
    # ^{-1}, ^2, _{1}, _2: superscripts and subscripts on identifiers
    re.compile(r"[A-Za-z}\)]\s*[\^_]\s*\{?[\-\+]?\d\}?"),
    # \tfrac{1}{2}, \tfrac{2}{3}, etc. (single-digit fractions in math)
    re.compile(r"\\t?frac\s*\{\s*\d\s*\}\s*\{\s*\d\s*\}"),
]

# Year-like four-digit numbers near \cite are bibliography years.
# We can't detect it perfectly without parsing references.bib, but most
# 19xx/20xx ints inside \citep{...} are years. Strip them per-line via context.
YEAR_RANGE = re.compile(r"\b(?:19\d{2}|20[0-3]\d)\b")


@dataclass
class Hit:
    line_no: int
    line: str
    value: str
    start: int
    end: int
    context: str  # ~80 chars around the hit


@dataclass
class UncoveredHit:
    line_no: int
    value: str
    context: str
    section: str  # e.g. "abstract", "section 3.1", "table tab:foo"

    def to_dict(self) -> dict:
        return asdict(self)


def import_claim_audit():
    spec = importlib.util.spec_from_file_location("claim_audit", CLAIM_AUDIT_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {CLAIM_AUDIT_PY}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claim_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def line_is_skippable(line: str) -> bool:
    """Whole-line filters before we even tokenize."""
    if COMMENT_LINE.match(line):
        return True
    stripped = line.strip()
    if not stripped:
        return True
    # Bibliography or BibTeX-style lines
    if stripped.startswith(("@", "\\bibitem", "\\bibliographystyle", "\\bibliography")):
        return True
    # Line-level incidentals (color defs, spacing, custom commands, enum)
    for pat in LINE_LEVEL_INCIDENTAL:
        if pat.search(line):
            return True
    return False


def strip_inline_comment(line: str) -> str:
    return COMMENT_INLINE.sub("", line)


def is_incidental_at(line: str, start: int, end: int) -> bool:
    """Decide whether a numeric token at line[start:end] is incidental."""
    # Look at a window around the hit.
    window_start = max(0, start - 20)
    window_end = min(len(line), end + 20)
    window = line[window_start:window_end]

    # Year inside \cite{...}
    if YEAR_RANGE.match(line[start:end]):
        if re.search(r"\\cite[a-z]*\{[^}]*\}", window):
            return True

    # Inside any incidental construct that overlaps the hit position
    for pat in INCIDENTAL_PATTERNS:
        for m in pat.finditer(line):
            if m.start() <= start and m.end() >= end:
                return True
            if m.start() <= start <= m.end() or m.start() <= end <= m.end():
                return True

    # Math notation patterns: check the small window around the hit.
    for pat in MATH_NOTATION_PATTERNS:
        for m in pat.finditer(window):
            # Translate window-relative match offsets back to line offsets.
            wstart_line = window_start + m.start()
            wend_line = window_start + m.end()
            if wstart_line <= start and wend_line >= end:
                return True
            if wstart_line <= start <= wend_line or wstart_line <= end <= wend_line:
                return True

    # \\ref{} preceded immediately by a number ("Theorem 3.1") — already
    # handled by INCIDENTAL_PATTERNS; this is belt-and-braces for the
    # "Section\,3" / "Section~3" form.
    immediately_before = line[max(0, start - 12):start]
    if re.search(
        r"(?:Section|Sec\.|Figure|Fig\.|Table|Tab\.|Algorithm|Alg\.|Theorem|Thm\.|Lemma|Corollary|Cor\.|Equation|Eq\.|Definition|Def\.|Appendix|App\.|Remark|Chapter|Ch\.|Step)\s*[~\\,]?\s*$",
        immediately_before,
    ):
        return True

    return False


def walk_paper_via_parser(path: Path) -> list[Hit] | None:
    """Optional: walk main.tex via pylatexenc to distinguish math-mode
    notation from text-mode claims at the AST level.

    Returns None if pylatexenc is not installed; the caller falls back
    to walk_paper (regex-only).

    Math-mode handling rationale: the previous regex sweep had to
    guess at math notation via heuristics (\\mathbf{1}, ^N, _N, etc.)
    and inevitably leaked single-digit ints from formulas as 'uncovered
    claims'. The parser knows what's math vs text by structure, so we
    can:
      - In TEXT mode: emit every numeric (real claims).
      - In MATH mode: emit ONLY multi-digit decimals (specific values
        like rho_hat=0.05) and skip bare 1-digit ints (notation).

    Nuance: single-digit decimals in math like "0.5" are sometimes real
    (rho values) and sometimes not (intervals, fractions). We keep them
    too — false positives there are absorbed into L3's coverage % which
    is advisory, not gating.
    """
    try:
        from pylatexenc.latexwalker import (
            LatexWalker, LatexCharsNode, LatexMathNode,
            LatexEnvironmentNode, LatexMacroNode, LatexGroupNode,
        )
    except ImportError:
        return None

    text = path.read_text(encoding="utf-8", errors="replace")
    walker = LatexWalker(text)
    nodes, _, _ = walker.get_latex_nodes()

    # Section index for the parser path: pre-build via existing regex
    # walk on raw text (sections are line-anchored, easy to extract).
    sections = extract_section_index(path)

    hits: list[Hit] = []

    def _walk(nodelist, in_math: bool, env_stack: list[str]):
        for n in nodelist:
            if isinstance(n, LatexEnvironmentNode):
                env_stack.append(n.envname)
                if n.envname in BLOCK_SKIP_ENVS:
                    env_stack.pop()
                    continue
                if n.nodelist:
                    _walk(n.nodelist, in_math=in_math, env_stack=env_stack)
                env_stack.pop()
                continue
            if isinstance(n, LatexMathNode):
                if n.nodelist:
                    _walk(n.nodelist, in_math=True, env_stack=env_stack)
                continue
            if isinstance(n, LatexCharsNode):
                # Compute line number from byte position
                line_no = text[:n.pos].count("\n") + 1
                line_text = text.splitlines()[line_no - 1] if line_no <= text.count("\n") + 1 else n.chars

                for m in NUMERIC.finditer(n.chars):
                    value = m.group(0)
                    # Math-mode filter: skip bare 1-digit ints (notation)
                    if in_math and value.isdigit() and len(value) <= 1:
                        continue
                    # Math-mode: skip if part of exponent/subscript
                    # (preceded by ^ or _ in n.chars)
                    if in_math and m.start() > 0:
                        prev = n.chars[m.start() - 1]
                        if prev in "^_":
                            continue

                    ctx_start = max(0, m.start() - 30)
                    ctx_end = min(len(n.chars), m.end() + 30)
                    ctx = n.chars[ctx_start:ctx_end].strip()
                    hits.append(Hit(
                        line_no=line_no,
                        line=line_text,
                        value=value,
                        start=m.start(),
                        end=m.end(),
                        context=ctx,
                    ))
                continue
            if hasattr(n, "nodelist") and n.nodelist:
                _walk(n.nodelist, in_math=in_math, env_stack=env_stack)

    _walk(nodes, in_math=False, env_stack=[])
    return hits


def walk_paper(path: Path) -> list[Hit]:
    """Stream main.tex once, tracking which environments we're inside.

    When we're inside any BLOCK_SKIP_ENVS (tikzpicture, algorithmic, ...),
    every numeric token is treated as incidental — the body is graphics
    code or pseudocode, not an empirical claim about model behavior.

    \\begin and \\end on the same line are tracked correctly: the line
    enters the env, then leaves it; tokens on that line are skipped while
    the env is active.

    This is the regex-only fallback. Prefer walk_paper_via_parser for
    accurate math-vs-text distinction.
    """
    hits: list[Hit] = []
    env_stack: list[str] = []  # names of currently-open block-skip envs
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")

            # Update env stack first (before skip decisions). A line with
            # both \begin{tikzpicture} and \end{tikzpicture} should remain
            # skipped throughout, so we apply begins, then process the line
            # in skip mode if env_stack is non-empty, then apply ends.
            for m in BEGIN_ENV.finditer(line):
                if m.group(1) in BLOCK_SKIP_ENVS:
                    env_stack.append(m.group(1))

            inside_skip_env = bool(env_stack)

            for m in END_ENV.finditer(line):
                if m.group(1) in BLOCK_SKIP_ENVS and env_stack and env_stack[-1] == m.group(1):
                    env_stack.pop()

            if inside_skip_env:
                continue
            if line_is_skippable(line):
                continue
            line_clean = strip_inline_comment(line)
            for m in NUMERIC.finditer(line_clean):
                if is_incidental_at(line_clean, m.start(), m.end()):
                    continue
                ctx_start = max(0, m.start() - 40)
                ctx_end = min(len(line_clean), m.end() + 40)
                ctx = line_clean[ctx_start:ctx_end].strip()
                hits.append(Hit(
                    line_no=i,
                    line=line_clean,
                    value=m.group(0),
                    start=m.start(),
                    end=m.end(),
                    context=ctx,
                ))
    return hits


def section_for_line(line_no: int, sections: list[tuple[int, str]]) -> str:
    """Return the most-recent section heading at or before line_no."""
    current = "preamble"
    for sec_line, name in sections:
        if sec_line <= line_no:
            current = name
        else:
            break
    return current


def extract_section_index(path: Path) -> list[tuple[int, str]]:
    """Walk main.tex once and remember (line_no, section name) tuples."""
    out: list[tuple[int, str]] = []
    sec_pat = re.compile(r"\\(section|subsection|subsubsection|paragraph)\*?\{([^}]+)\}")
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, raw in enumerate(fh, start=1):
            m = sec_pat.search(raw)
            if m:
                level, name = m.group(1), m.group(2)
                out.append((i, f"{level}: {name}"))
    return out


def build_combined_pattern_set(mod) -> list[re.Pattern]:
    """Compile every claim pattern from CLAIMS + IMPORTANT + COMPLETE
    into one bag.

    A numeric hit counts as 'covered' if any of these patterns matches
    its context line.
    """
    compiled: list[re.Pattern] = []
    sources = [mod.CLAIMS, mod.IMPORTANT]
    if hasattr(mod, "COMPLETE"):
        sources.append(mod.COMPLETE)
    for src in sources:
        for c in src:
            for p in c.get("patterns", []):
                try:
                    compiled.append(re.compile(p))
                except re.error:
                    pass
    return compiled


def is_covered(hit: Hit, claim_patterns: list[re.Pattern]) -> bool:
    """A hit is covered if any claim pattern matches anywhere in its source line."""
    for pat in claim_patterns:
        if pat.search(hit.line):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print every uncovered hit")
    parser.add_argument("--max-print", type=int, default=40, help="max uncovered hits to print in summary (default 40)")
    args = parser.parse_args()

    if not MAIN_TEX.exists():
        print(f"ERROR: paper not found at {MAIN_TEX}", file=sys.stderr)
        return 2

    mod = import_claim_audit()
    claim_patterns = build_combined_pattern_set(mod)
    sections = extract_section_index(MAIN_TEX)

    # Prefer the AST-based walker (pylatexenc) when available for accurate
    # math-vs-text distinction; fall back to regex-only walker otherwise.
    parser_hits = walk_paper_via_parser(MAIN_TEX)
    if parser_hits is not None:
        all_hits = parser_hits
        walker_used = "pylatexenc-AST"
    else:
        all_hits = walk_paper(MAIN_TEX)
        walker_used = "regex-only"
    covered = [h for h in all_hits if is_covered(h, claim_patterns)]
    uncovered_hits = [h for h in all_hits if not is_covered(h, claim_patterns)]

    # Dedupe by (line_no, value, context) — same numeric appearing twice on
    # one line shouldn't double-count.
    seen = set()
    unique_uncovered: list[UncoveredHit] = []
    for h in uncovered_hits:
        key = (h.line_no, h.value, h.context)
        if key in seen:
            continue
        seen.add(key)
        unique_uncovered.append(UncoveredHit(
            line_no=h.line_no,
            value=h.value,
            context=h.context,
            section=section_for_line(h.line_no, sections),
        ))

    print("=" * 70)
    print("CLAIM COVERAGE SWEEP  (reverse coverage of main.tex)")
    print("=" * 70)
    print(f"Paper:           {MAIN_TEX}")
    print(f"Walker:          {walker_used}")
    n_pattern_claims = len(mod.CLAIMS) + len(mod.IMPORTANT) + len(getattr(mod, 'COMPLETE', []))
    print(f"Claim patterns:  {len(claim_patterns)} (from {n_pattern_claims} claims)")
    print()
    print(f"  Numeric tokens after incidental filter : {len(all_hits):>5}")
    print(f"  Covered by at least one claim pattern  : {len(covered):>5}")
    print(f"  Uncovered (raw)                        : {len(uncovered_hits):>5}")
    print(f"  Uncovered (deduped)                    : {len(unique_uncovered):>5}")
    print()
    coverage_pct = (len(covered) / len(all_hits) * 100) if all_hits else 0
    print(f"  Empirical coverage: {coverage_pct:.1f}%")
    print()

    # Triage prioritization: rank uncovered values by likely-claim-ness.
    #
    # Heuristics:
    #   - Specific decimals (>=2 sig figs) are almost always empirical.
    #   - Rare values (appearing 1-2 times) are likely specific claims.
    #   - Common small ints (0, 1, 2, 3) appearing 10+ times are notation.
    #
    # We score each unique value: lower score = higher priority.
    from collections import Counter
    value_freq = Counter(h.value for h in unique_uncovered)

    def is_specific_decimal(v: str) -> bool:
        cleaned = v.replace(",", "").replace("{", "").replace("}", "")
        if "." not in cleaned:
            return False
        frac = cleaned.split(".", 1)[1]
        return len(frac) >= 2  # 0.05, 0.023, 1.4142 — yes; 1.0 — no

    def priority(h: UncoveredHit) -> int:
        freq = value_freq[h.value]
        score = 0
        if is_specific_decimal(h.value):
            score -= 10
        if freq <= 2:
            score -= 5
        elif freq >= 10:
            score += 5
        # Bare small ints in math context are usually notation
        if h.value in {"0", "1", "2", "3", "4", "5"}:
            score += 3
        return score

    triage = sorted(unique_uncovered, key=priority)

    if triage:
        n_print = min(len(triage), args.max_print)
        print(f"Top {n_print} triage candidates (ranked by likely-claim-ness):")
        print("-" * 70)
        for h in triage[:n_print]:
            freq = value_freq[h.value]
            freq_marker = f"x{freq}" if freq > 1 else ""
            print(f"  L{h.line_no:>4}  [{h.value:>8}]{freq_marker:<4}  ({h.section[:38]})")
            print(f"          {h.context[:100]}")
        if len(triage) > n_print:
            print(f"  ... and {len(triage) - n_print} more (see {TRIAGE_JSON.name})")
        print()

    payload = {
        "paper_path": str(MAIN_TEX),
        "summary": {
            "total_numeric_tokens": len(all_hits),
            "covered": len(covered),
            "uncovered_raw": len(uncovered_hits),
            "uncovered_deduped": len(unique_uncovered),
            "coverage_percent": round(coverage_pct, 1),
        },
        "uncovered": [h.to_dict() for h in unique_uncovered],
    }
    TRIAGE_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full triage list written to: {TRIAGE_JSON}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
