"""
text_normalization.py — Shared LaTeX normalization for regex-based checks.

Maps common macro aliases to a single canonical form so claim regexes
do not need to enumerate every spelling. Use this BEFORE running pattern
matches in claim_audit.py / claim_coverage_sweep.py.
"""
from __future__ import annotations
import sys as _sys  # UTF-8 stdout (Windows cp1252 mojibake fix)
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(_sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
import re

# Maps: pattern → canonical form
# Order matters: more specific aliases first.
_MACRO_ALIASES = [
    # \rhohat → \hat{\rho}
    (re.compile(r"\\rhohat\b"), r"\\hat{\\rho}"),
    # \dmin → \delta_{\min}
    (re.compile(r"\\dmin\b"), r"\\delta_{\\min}"),
    # \Lhat → \hat{L}
    (re.compile(r"\\Lhat\b"), r"\\hat{L}"),
]


def normalize_latex(text: str) -> str:
    """Replace known macro aliases with their canonical \\hat{...} form.

    Idempotent. Safe to call on text that has no aliases.
    """
    for pat, repl in _MACRO_ALIASES:
        text = pat.sub(repl, text)
    return text


def normalize_thousands(text: str) -> str:
    r"""Normalize LaTeX thousand separators to plain digits inside number tokens.

    4{,}800  -> 4800
    4\,800   -> 4800
    4,800    -> 4800
    1{,}920  -> 1920
    """
    # \d{1,3}({,}|\,|,)\d{3} forms inside numeric context
    text = re.sub(r"(\d)\\?\{,\\?\}(\d)", r"\1\2", text)
    text = re.sub(r"(\d)\\,(\d)", r"\1\2", text)
    return text


if __name__ == "__main__":
    # quick sanity demo
    samples = [
        r"$\rhohat{=}0.50$",
        r"$\delta = \dmin / \Lhat$",
        r"4{,}800 trials and 4\,272 smooth and 4,800 total",
    ]
    for s in samples:
        print(f"  {s!r}")
        print(f"  -> macros:    {normalize_latex(s)!r}")
        print(f"  -> thousands: {normalize_thousands(normalize_latex(s))!r}")
        print()
