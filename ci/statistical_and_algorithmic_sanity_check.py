#!/usr/bin/env python3
"""
statistical_and_algorithmic_sanity_check.py - L27 of the certification stack.

Mechanically scans main.tex for the highest-value classes of preventable
mathematical / statistical drift that the upstream layers do not catch.
This layer was added in response to Google PAT auto-feedback that surfaced:

  - A reported Spearman p < 0.001 with N=4 (mathematically impossible:
    Spearman exact at N=4 has min p = 0.083).
  - Algorithm 1 line 4 computing sqrt(k/(1-rho_hat(k-1))) without a guard
    against the inner argument going non-positive at k>=3, rho_hat>=1/(k-1).
  - A formula sign slip in a 2-constraint Gram-inverse expansion (-2rho
    where +2rho was meant).
  - A linear-independence claim under "rho_max < 1" that is only valid
    for "rho_max < 1/(k-1)".
  - Preservation overhead claimed at 33% (squared-norm 1/(1-rho^2) factor)
    where the linear-norm bound is 1/sqrt(1-rho^2) = 15.5%.
  - A table cross-reference (Tab.~\\ref{tab:overhead}) pointing at a
    non-table label.

L8 (link_integrity_check.py) verifies labels resolve. This layer is one
notch deeper: that the rendered prefix matches the target's environment
type, that p-values are achievable at the inferred sample size, that
algorithmic operations have a guard that constrains the operand to the
safe domain, and that headline numbers in the abstract are restated
consistently in the body.

Sub-checks
----------
  1. impossible_p_values
       Find every "p < / = / > X" in main.tex; infer N from local context;
       compare X against the minimum-achievable p-value at that N for
       common small-N tests (Spearman exact, Pearson exact). Blocker if
       N is unambiguous; warn if inferred.

  2. algorithm_guards
       Parse every \\begin{algorithmic}...\\end{algorithmic} environment.
       For each \\sqrt{...} and \\frac{}{} (or "/" in math mode), check
       whether a preceding \\IF / \\IF{...} guard tests the operand for
       the safe domain. Heuristic: warn-only.

  3. cross_reference_type_match
       For every Tab.~\\ref / Table~\\ref / Theorem~\\ref / Sec.~\\ref /
       App.~\\ref / Fig.~\\ref / Algorithm~\\ref / Eq.~\\ref / Lemma~\\ref
       / Cor.~\\ref / Def.~\\ref, verify the target label resolves AND
       sits inside an environment whose type matches the prefix
       (e.g. tab:overhead -> \\begin{table}). Warn.

  4. headline_consistency
       For a small hand-curated registry of canonical numbers stated in
       the abstract / introduction (regret, smooth-regime denominator,
       pivot rate, Spearman r_s, etc.), search the rest of the paper for
       matching descriptive context and compare values. Blocker on
       mismatch.

Exit codes
----------
  0  no blockers found (warns may still be present)
  1  one or more blocker findings
  2  invocation error
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = SCRIPT_DIR / "statistical_and_algorithmic_sanity_results.json"


# ---------------------------------------------------------------------------
# Sub-check 1: minimum achievable p-values at small N
# ---------------------------------------------------------------------------
# Source: Spearman exact 2-tailed p at small N is bounded below by 2/N!
# for the most extreme rank permutation (one of N! gives r_s = +-1).
# Pearson exact (under bivariate normal): also bounded below by an
# achievable-permutation argument; we use the same lookup as a
# defensible lower bound. For N <= 10 these are the values cited in
# every nonparametric stats reference.
#
# We deliberately under-claim (use the larger of the two lookups) so a
# borderline case is not flagged unless it is genuinely impossible.

MIN_P_SPEARMAN_2TAIL: dict[int, float] = {
    3:  0.333,   # 2/6
    4:  0.0833,  # 2/24
    5:  0.0167,  # 2/120
    6:  0.00278, # 2/720
    7:  0.000397,
    8:  0.0000496,
    9:  5.51e-6,
    10: 5.51e-7,
}

# Pearson exact (2-tailed) under permutation has the same denominator
# bound for ties-free data. For mixed test types we take the more
# permissive (smaller) of the two so we never over-flag.
MIN_P_PEARSON_2TAIL: dict[int, float] = MIN_P_SPEARMAN_2TAIL


def _min_achievable_p(n: int) -> float | None:
    """Return the minimum 2-tailed p achievable at sample size n.

    Returns None if n is outside the lookup range (we don't flag at
    large n; the false-positive cost outweighs the gain)."""
    if n is None or n < 3:
        return None
    if n in MIN_P_SPEARMAN_2TAIL:
        return MIN_P_SPEARMAN_2TAIL[n]
    return None


# ---------------------------------------------------------------------------
# Headline registry: hand-curated canonical claims from the abstract /
# intro. Each entry says: this number, with this surrounding description,
# is the single source of truth. If the body restates the same paired
# (description, value) construction with a different value, that is a
# blocker.
#
# Each entry pairs:
#   - canonical_value (textual form for reporting)
#   - numeric_value (for comparison)
#   - extract_pattern: regex with one capture group (the value as it
#     would appear in a restatement)
#   - The pattern must encode the descriptor PLUS the value-paired
#     construction, so we only fire on actual paraphrases, not on every
#     number that happens to occur near the description text. This is
#     deliberately conservative: better to miss a noisy paraphrase than
#     to drown the cert in false positives.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HeadlineClaim:
    name: str
    canonical_value: str
    numeric_value: float
    # Each pattern's group(1) yields the numeric value to compare.
    extract_patterns: list[re.Pattern] = field(default_factory=list)
    tolerance: float = 0.0


HEADLINE_REGISTRY: list[HeadlineClaim] = [
    # The smooth-regime denominator: "0/4,272" (and its LaTeX form
    # 0/4{,}272). Any other "0/N trials" with N != 4272 in a context
    # that explicitly says "smooth-regime" is a contradiction.
    HeadlineClaim(
        name="high_tier_refutation_denominator",
        canonical_value="1,365",
        numeric_value=1365,
        extract_patterns=[
            re.compile(r"0\s*/\s*([\d,{}]+)\s+high[- ]tier", re.IGNORECASE),
            re.compile(r"high[- ]tier\s+(?:trial|refutation)s?[^.]{0,30}?\(\s*0\s*/\s*([\d,{}]+)", re.IGNORECASE),
        ],
    ),
    # Unconditional pivot rate: "unconditional success is 1.7%" or
    # "(1.7\% unconditional rate)". Tightened to require the keyword
    # "unconditional" PLUS the construction "rate" or "success" PLUS the
    # numeric within a few tokens.
    HeadlineClaim(
        name="high_tier_unconditional_pass_rate_pct",
        canonical_value="67.6",
        numeric_value=67.6,
        extract_patterns=[
            re.compile(r"unconditional\s+(?:high[- ]tier\s+)?pass\s+rate\s+is\s+\$?(\d+(?:\.\d+)?)\s*\\?%", re.IGNORECASE),
            re.compile(r"high[- ]tier\s+pass\s+rate[^.]{0,40}?\$?(\d+(?:\.\d+)?)\s*\\?%", re.IGNORECASE),
        ],
        tolerance=1.0,
    ),
    # Regret vs oracle: "1.8 \pm 0.4 % regret" or "regret of 1.8%".
    # Restricted to constructions that pair the value to the word
    # "regret" with "vs oracle" or "+/-" present.
    HeadlineClaim(
        name="regret_vs_oracle_pct",
        canonical_value="1.8",
        numeric_value=1.8,
        extract_patterns=[
            re.compile(r"\$?(\d+(?:\.\d+)?)\s*\\pm\s*\d+(?:\.\d+)?\\?%\s*regret", re.IGNORECASE),
            re.compile(r"regret\s+of\s+\$?(\d+(?:\.\d+)?)\s*\\?%", re.IGNORECASE),
        ],
        tolerance=0.1,
    ),
    # Contract compliance: "89% of trajectories obey ... displacement
    # contract" — require both "obey" or "Lipschitz" AND "trajectories"
    # to disambiguate from "94% of trajectories show drift" (a different
    # statistic).
    HeadlineClaim(
        name="contract_compliance_pct",
        canonical_value="4",
        numeric_value=4,
        extract_patterns=[
            re.compile(r"(\d+(?:\.\d+)?)\s*\\?%\s+of\s+(?:generation\s+)?trajectories\s+obey", re.IGNORECASE),
            re.compile(r"(\d+(?:\.\d+)?)\s*\\?%\s+of\s+trajectories\s+(?:satisfy|comply\s+with)\s+(?:the\s+)?(?:displacement\s+)?contract", re.IGNORECASE),
        ],
        tolerance=0.5,
    ),
    # Pivot fraction under audit-observer measurement: 96% pivot regime.
    HeadlineClaim(
        name="pivot_fraction_pct",
        canonical_value="96",
        numeric_value=96,
        extract_patterns=[
            re.compile(r"remaining\s+(\d+(?:\.\d+)?)\s*\\?%\s+(?:are\s+)?(?:detectable\s+)?pivot\s+event", re.IGNORECASE),
            re.compile(r"(\d+(?:\.\d+)?)\s*\\?%\s+pivot\s+regime\b", re.IGNORECASE),
        ],
        tolerance=1.0,
    ),
    # High-tier pass numerator under audit-observer run: "923/1,365".
    # Tightened: must say "pass" (not "violation rate", which references the
    # Rule-of-Three 3/1365 upper-bound calculation, not the pass count).
    HeadlineClaim(
        name="high_tier_pass_numerator",
        canonical_value="923/1,365",
        numeric_value=923,
        extract_patterns=[
            re.compile(r"high[- ]tier\s+pass(?:es)?[^.]{0,40}?(\d+)\s*/\s*1[,{}]?365", re.IGNORECASE),
            re.compile(r"(\d+)\s*/\s*528\s+(?:pivot|success)", re.IGNORECASE),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Finding container
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    subcheck: str
    severity: str            # "blocker" | "warn"
    location: str            # "line N" or "lines N-M"
    snippet: str             # short excerpt (truncated)
    detail: str              # human-readable explanation


# ---------------------------------------------------------------------------
# Sub-check 1: impossible p-values
# ---------------------------------------------------------------------------
# Patterns: p < 0.001, p = 1.2e-5, p < 10^{-20}, p > 0.4, $p < 0.05$
# We capture the operator and the numeric value. Numbers may be plain
# (0.001), scientific (2.12e-23, 1.2 * 10^{-5}), or LaTeX-typeset
# (10^{-20}, 2.12 \times 10^{-23}).

_P_VALUE_RE = re.compile(
    r"""\bp\s*               # the symbol p
        (?P<op>[<=>])\s*     # comparator
        (?P<val>             # the value, in any of three forms:
            \d+(?:\.\d+)?(?:\s*\\?times?\s*10\^?\{?-?\d+\}?)?  # 2.12 \times 10^{-23}
          | \d+(?:\.\d+)?[eE]-?\d+                              # 1.2e-5
          | \d+(?:\.\d+)?                                       # 0.001
          | 10\^\{?-?\d+\}?                                     # 10^{-20}
        )
    """,
    re.VERBOSE,
)

# N inference: look in surrounding context for cues. We accept several
# common phrasings:  N = 48, n = 4, 48 points, 4,272 trials, etc.
_N_PATTERNS = [
    re.compile(r"\bN\s*=\s*([\d,]+)"),
    re.compile(r"\bn\s*=\s*([\d,]+)"),
    # "48 points", "48-point analysis", "12 tasks", etc.
    re.compile(r"([\d,]+)[\s\-]+(?:tier|task|point|sample|trial|observation|combination|pair|task[\-\s]tier)s?\b", re.IGNORECASE),
    re.compile(r"across\s+([\d,]+)", re.IGNORECASE),
]


def _parse_p_value(raw: str) -> float | None:
    """Parse a p-value string into a float. Handles plain, sci, and LaTeX forms."""
    s = raw.strip()
    # 10^{-20}  or  10^-20
    m = re.fullmatch(r"10\^\{?(-?\d+)\}?", s)
    if m:
        return 10 ** int(m.group(1))
    # X \times 10^{-N}  or  X * 10^-N
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\\?times?\s*10\^?\{?(-?\d+)\}?", s)
    if m:
        return float(m.group(1)) * (10 ** int(m.group(2)))
    # 1.2e-5
    try:
        return float(s)
    except ValueError:
        return None


def _infer_n_near(text: str, char_pos: int, window: int = 300) -> tuple[int | None, str]:
    """Look in a +-window-character window around char_pos for an N cue.

    Returns (n, cue_text). When multiple cues exist, preference order:
      1. Explicit 'N = X' or 'n = X' that is the CLOSEST to the p-value.
      2. Descriptor 'X items/points/tasks/...' closest to the p-value.

    Distance is measured from the cue position to char_pos. If two cues
    are equidistant, the explicit one wins. When no explicit cue exists
    and multiple descriptors compete, choose the closest (not the
    smallest) -- a paper that mixes N=4 and N=48 statements in the
    same paragraph is reporting two distinct statistics, and the one
    paired to the p-value is the one nearest to it lexically.
    """
    lo = max(0, char_pos - window)
    hi = min(len(text), char_pos + window)
    chunk = text[lo:hi]
    chunk_clean = chunk.replace("{", "").replace("}", "")
    # Track best candidate as (distance, is_explicit, n, cue_text)
    best: tuple[int, int, int, str] | None = None
    target_in_chunk = char_pos - lo

    def _consider(n: int, cue_pos: int, cue: str, explicit: bool) -> None:
        nonlocal best
        if not (3 <= n <= 1_000_000):
            return
        dist = abs(cue_pos - target_in_chunk)
        # is_explicit=0 means explicit (sorts first since smaller key wins)
        key = (dist, 0 if explicit else 1, n, cue)
        if best is None or key < best:
            best = key

    # Pass 1: explicit "N = X" / "n = X"
    for pat in _N_PATTERNS[:2]:
        for m in pat.finditer(chunk_clean):
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            _consider(n, m.start(), m.group(0), explicit=True)
    # Pass 2: descriptor cues
    for pat in _N_PATTERNS[2:]:
        for m in pat.finditer(chunk_clean):
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            _consider(n, m.start(), m.group(0), explicit=False)
    if best is None:
        return None, ""
    _, _, n, cue = best
    return n, cue


def check_impossible_p_values(text: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    # Build line-offset map for converting char position -> line number
    line_offsets: list[int] = [0]
    for ln in lines:
        line_offsets.append(line_offsets[-1] + len(ln) + 1)  # +1 for newline

    def char_to_line(pos: int) -> int:
        # binary search would be cleaner; linear is fine for ~3k lines
        for i, off in enumerate(line_offsets):
            if off > pos:
                return i  # 1-indexed: previous offset is line i
        return len(lines)

    for m in _P_VALUE_RE.finditer(text):
        raw = m.group("val")
        op = m.group("op")
        p = _parse_p_value(raw)
        if p is None:
            continue
        # Skip the trivial "p > 0.X" reports of non-significance: those are
        # not at risk of being mathematically impossible.
        if op == ">":
            continue
        n, cue = _infer_n_near(text, m.start(), window=300)
        if n is None:
            # Without N, we cannot judge. Skip (don't even warn — too noisy).
            continue
        min_p = _min_achievable_p(n)
        if min_p is None:
            continue
        # Compare. p<X is impossible if X < min_p; p=X is impossible if X < min_p
        if p < min_p:
            line_no = char_to_line(m.start())
            snippet = lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else ""
            # Severity: blocker if N inference was unambiguous (a single
            # "N=" or "n=" cue), warn if it was inferred from "X points"
            # or similar.
            severity = "blocker" if re.search(r"\b[Nn]\s*=", cue) else "warn"
            findings.append(Finding(
                subcheck="impossible_p_values",
                severity=severity,
                location=f"line {line_no}",
                snippet=snippet,
                detail=(
                    f"Reported p {op} {raw} (= {p:.3g}) is below the "
                    f"minimum achievable 2-tailed p-value at N={n} "
                    f"({min_p:.3g}). N inferred from cue: '{cue}'."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# Sub-check 2: algorithm guards
# ---------------------------------------------------------------------------
_ALGO_BLOCK_RE = re.compile(
    r"\\begin\{algorithmic\}.*?\\end\{algorithmic\}",
    re.DOTALL,
)

# An "operation at risk": sqrt of an expression, or division.
# For each, we want to know whether the operand can become invalid.
_SQRT_RE = re.compile(r"\\sqrt\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
_FRAC_RE = re.compile(r"\\frac\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")


def _has_guard_above(algo_text: str, op_pos: int, lookback_lines: int = 5) -> bool:
    """Heuristic: does the pseudocode have an \\IF or \\REQUIRE in the
    `lookback_lines` lines preceding `op_pos`?"""
    pre = algo_text[:op_pos]
    pre_lines = pre.splitlines()
    window = pre_lines[-lookback_lines:] if len(pre_lines) >= lookback_lines else pre_lines
    joined = "\n".join(window)
    # Match \IF, \ELSIF, \REQUIRE, \ENSURE - any of these tests/declares
    # the safe domain.
    return bool(re.search(r"\\(IF|ELSIF|REQUIRE|ENSURE|ASSERT)\b", joined))


def check_algorithm_guards(text: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for block_m in _ALGO_BLOCK_RE.finditer(text):
        block_start = block_m.start()
        block = block_m.group(0)

        # Find the line number of the algorithmic block start
        block_line = text[:block_start].count("\n") + 1

        # SQRT operations
        for op_m in _SQRT_RE.finditer(block):
            operand = op_m.group(1)
            # Only flag sqrts whose operand contains subtraction or 1- or
            # division — these are the ones at risk of going non-positive.
            if not re.search(r"[-]|/|\\frac", operand):
                continue
            if _has_guard_above(block, op_m.start()):
                continue
            # Compute approximate line number within main.tex
            char_in_text = block_start + op_m.start()
            line_no = text[:char_in_text].count("\n") + 1
            findings.append(Finding(
                subcheck="algorithm_guards",
                severity="warn",
                location=f"line {line_no}",
                snippet=f"\\sqrt{{{operand}}}",
                detail=(
                    f"\\sqrt operand contains subtraction or division "
                    f"(could go non-positive); no \\IF/\\REQUIRE guard "
                    f"found in 5 preceding pseudocode lines. Algorithm "
                    f"block at line {block_line}."
                ),
            ))

        # FRAC operations: flag if denominator contains subtraction
        for op_m in _FRAC_RE.finditer(block):
            denom = op_m.group(2)
            if "-" not in denom and "1" not in denom:
                continue
            if _has_guard_above(block, op_m.start()):
                continue
            char_in_text = block_start + op_m.start()
            line_no = text[:char_in_text].count("\n") + 1
            findings.append(Finding(
                subcheck="algorithm_guards",
                severity="warn",
                location=f"line {line_no}",
                snippet=f"\\frac{{...}}{{{denom}}}",
                detail=(
                    f"\\frac denominator contains subtraction (could go "
                    f"to zero); no guard found in 5 preceding lines. "
                    f"Algorithm block at line {block_line}."
                ),
            ))

    return findings


# ---------------------------------------------------------------------------
# Sub-check 3: cross-reference type match
# ---------------------------------------------------------------------------
# Map prefix -> expected environment names that the label should be
# defined inside.
_PREFIX_EXPECTED_ENV = {
    "tab":      {"table", "table*"},
    "table":    {"table", "table*"},
    "thm":      {"theorem"},
    "theorem":  {"theorem"},
    "lem":      {"lemma"},
    "lemma":    {"lemma"},
    "cor":      {"corollary"},
    "corollary": {"corollary"},
    "fig":      {"figure", "figure*"},
    "figure":   {"figure", "figure*"},
    "alg":      {"algorithm"},
    "algorithm": {"algorithm"},
    "sec":      {"section"},
    "section":  {"section"},
    "app":      {"section"},   # appendices are sections inside \appendix
    "appendix": {"section"},
    "eq":       {"equation", "equation*", "align", "align*", "gather"},
    "equation": {"equation", "equation*"},
    "def":      {"definition"},
    "definition": {"definition"},
    "rem":      {"remark"},
    "remark":   {"remark"},
    "prop":     {"proposition"},
}

# Match prefixed refs:  Tab.~\ref{tab:foo}, Table~\ref{tab:foo}, Thm~\ref{thm:foo}
# Capture (prefix, label).
_TYPED_REF_RE = re.compile(
    r"(?P<prefix>Tab|Table|Thm|Theorem|Lem|Lemma|Cor|Corollary|Fig|Figure|"
    r"Alg|Algorithm|Sec|Section|App|Appendix|Eq|Equation|Def|Definition|"
    r"Rem|Remark|Prop)"
    r"\.?~?\\(?:c?ref|autoref|nameref)\{(?P<label>[^}]+)\}",
    re.IGNORECASE,
)

# Also catch \appref / \secref macros defined in the preamble.
_MACRO_REF_RE = re.compile(
    r"\\(?P<macro>appref|secref)\{(?P<label>[^}]+)\}"
)


def _build_label_environment_map(text: str) -> dict[str, str]:
    """For every \\label{key}, determine which environment owns it.

    Resolution rules (in priority order):
      - If the label appears DIRECTLY ON the same line/sentence as a
        \\section{...} / \\subsection{...} command (within ~120 chars
        after the section opener), the label belongs to a 'section'.
      - Else, if the label is inside a \\begin{env}...\\end{env} block,
        it belongs to that innermost env.
      - Else, 'document'.

    The first rule is what \\label semantics in LaTeX actually do for
    section anchors; my earlier 'innermost begin' approach mis-classified
    every section label as 'document'."""
    label_env: dict[str, str] = {}

    # Step 1: section anchors. A label is a section anchor only if it
    # immediately follows the closing brace of \section{...} with no
    # intervening LaTeX command (only whitespace allowed). LaTeX convention:
    #   \section{Foo}\label{sec:foo}
    # We do NOT match labels that appear later, because those would be
    # caught by \begin{theorem}\label{thm:foo} which is the actual owner.
    section_label_positions: set[int] = set()
    for m in re.finditer(
        r"\\(?P<kind>section|subsection|subsubsection|paragraph|chapter)\*?\{",
        text,
    ):
        # Find matching close brace
        depth = 1
        i = m.end()
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        # Look at the immediately-following content: skip whitespace
        # (incl. newlines), then accept any chain of consecutive
        # \label{...} commands as section anchors. LaTeX permits
        # multiple labels on one section line (e.g. for compatibility
        # aliases). Anything else (\begin{}, text, etc.) means the
        # chain has ended.
        j = i
        while j < len(text):
            # skip whitespace
            while j < len(text) and text[j].isspace():
                j += 1
            lm = re.match(r"\\label\{([^}]+)\}", text[j:])
            if not lm:
                break
            label_env[lm.group(1)] = "section"
            section_label_positions.add(j)
            j += lm.end()

    # Step 2: env-stack walk for the remaining labels
    events: list[tuple[int, str, str]] = []
    for m in re.finditer(r"\\begin\{([^}]+)\}", text):
        events.append((m.start(), "begin", m.group(1)))
    for m in re.finditer(r"\\end\{([^}]+)\}", text):
        events.append((m.start(), "end", m.group(1)))
    for m in re.finditer(r"\\label\{([^}]+)\}", text):
        events.append((m.start(), "label", m.group(1)))
    events.sort(key=lambda e: e[0])

    env_stack: list[str] = []
    for pos, kind, name in events:
        if kind == "begin":
            env_stack.append(name)
        elif kind == "end":
            if env_stack and env_stack[-1] == name:
                env_stack.pop()
        elif kind == "label":
            if name in label_env:
                continue  # already resolved as a section anchor
            if env_stack:
                label_env[name] = env_stack[-1]
            else:
                label_env[name] = "document"
    return label_env


def check_cross_reference_types(text: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    label_env = _build_label_environment_map(text)

    def _line_of(char_pos: int) -> int:
        return text[:char_pos].count("\n") + 1

    def _check(prefix_raw: str, label: str, char_pos: int) -> None:
        prefix = prefix_raw.lower().rstrip(".")
        expected = _PREFIX_EXPECTED_ENV.get(prefix)
        if not expected:
            return
        actual_env = label_env.get(label)
        if actual_env is None:
            # L8 already reports unresolved labels; don't double-report
            return
        if actual_env not in expected:
            line_no = _line_of(char_pos)
            snippet = lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else ""
            findings.append(Finding(
                subcheck="cross_reference_type_match",
                severity="warn",
                location=f"line {line_no}",
                snippet=snippet,
                detail=(
                    f"Cross-reference '{prefix_raw}.~\\ref{{{label}}}' "
                    f"expects environment in {sorted(expected)} but "
                    f"target label '{label}' is defined inside "
                    f"'{actual_env}'."
                ),
            ))

    for m in _TYPED_REF_RE.finditer(text):
        _check(m.group("prefix"), m.group("label"), m.start())
    for m in _MACRO_REF_RE.finditer(text):
        # appref -> app prefix; secref -> sec prefix
        macro = m.group("macro")
        prefix = "app" if macro == "appref" else "sec"
        _check(prefix, m.group("label"), m.start())

    return findings


# ---------------------------------------------------------------------------
# Sub-check 4: headline consistency
# ---------------------------------------------------------------------------
def _parse_loose_numeric(raw: str) -> float | None:
    """Parse a numeric token that may include LaTeX braces and commas."""
    cleaned = raw.replace("{", "").replace("}", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def check_headline_consistency(text: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for claim in HEADLINE_REGISTRY:
        for pat in claim.extract_patterns:
            for m in pat.finditer(text):
                raw = m.group(1)
                found_num = _parse_loose_numeric(raw)
                if found_num is None:
                    continue
                tol = max(claim.tolerance, abs(claim.numeric_value) * 0.01)
                if abs(found_num - claim.numeric_value) <= tol:
                    continue
                line_no = text[:m.start()].count("\n") + 1
                snippet = lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else ""
                findings.append(Finding(
                    subcheck="headline_consistency",
                    severity="blocker",
                    location=f"line {line_no}",
                    snippet=snippet,
                    detail=(
                        f"Headline claim '{claim.name}' canonical value "
                        f"is {claim.canonical_value} (={claim.numeric_value}); "
                        f"matched restatement '{m.group(0)[:80]}' yields "
                        f"{found_num}. Mismatch > tol {tol:.3g}."
                    ),
                ))
    return findings


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main-tex", default=str(MAIN_TEX),
                        help="path to main.tex (default: paper/main.tex)")
    parser.add_argument("--json-out", default=str(RESULTS_JSON),
                        help="path for JSON output")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print every finding to stdout")
    args = parser.parse_args(argv)

    main_tex = Path(args.main_tex)
    if not main_tex.exists():
        print(f"ERROR: main.tex not found at {main_tex}", file=sys.stderr)
        return 2

    text = main_tex.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    print("=" * 70)
    print("L27 STATISTICAL & ALGORITHMIC SANITY CHECK")
    print("=" * 70)
    print(f"main.tex: {main_tex.relative_to(REPO_ROOT) if main_tex.is_absolute() else main_tex}")
    print(f"lines:    {len(lines)}")
    print()

    sub_results: dict[str, list[Finding]] = {
        "impossible_p_values":      check_impossible_p_values(text, lines),
        "algorithm_guards":         check_algorithm_guards(text, lines),
        "cross_reference_type_match": check_cross_reference_types(text, lines),
        "headline_consistency":     check_headline_consistency(text, lines),
    }

    total_blocker = 0
    total_warn = 0
    for name, findings in sub_results.items():
        n_block = sum(1 for f in findings if f.severity == "blocker")
        n_warn = sum(1 for f in findings if f.severity == "warn")
        total_blocker += n_block
        total_warn += n_warn
        status = "PASS" if n_block == 0 else "FAIL"
        print(f"  [{status}] {name:<30}  blocker={n_block:<3} warn={n_warn:<3}")
        if args.verbose or n_block > 0:
            for f in findings:
                marker = "!!" if f.severity == "blocker" else " ~"
                print(f"     {marker} ({f.severity:7s}) {f.location}: {f.detail}")
                if f.snippet:
                    print(f"          | {f.snippet[:160]}")

    print()
    print("-" * 70)
    print(f"  Total blockers: {total_blocker}")
    print(f"  Total warnings: {total_warn}")
    print("-" * 70)

    payload = {
        "summary": {
            "total_blocker": total_blocker,
            "total_warn": total_warn,
            "passed": total_blocker == 0,
            "subchecks": {
                name: {
                    "blocker": sum(1 for f in findings if f.severity == "blocker"),
                    "warn":    sum(1 for f in findings if f.severity == "warn"),
                    "total":   len(findings),
                }
                for name, findings in sub_results.items()
            },
        },
        "findings": [
            {**asdict(f)} for findings in sub_results.values() for f in findings
        ],
    }
    Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  Results -> {args.json_out}")
    return 0 if total_blocker == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
