"""Author-only lexicon drift cert.

Verifies that every protected phrase in `ci/de_llm_lexicon.md` (sections
1a, 1b, 1c) still appears in `paper/main.tex` at non-zero frequency.
Catches the failure mode where a protected back-quote, an area-signal
phrase, or a conceptual-scaffold term silently drops out of the paper
during edit cycles.

This cert is AUTHOR-ONLY. It is not part of the reviewer-facing cert
chain (`ci/claim_certificate.py`) and should not be packaged into the
reviewer-facing supplementary bundle. The lexicon file references the
in-flight follow-on paper, which is author-only context.

Usage:
    python ci/author_lexicon_drift_check.py
    python ci/author_lexicon_drift_check.py --json-out ci/author_lexicon_drift_results.json

Exit code:
    0 if all protected phrases present
    1 if any protected phrase has zero occurrences in paper/main.tex
    2 on invocation error (missing lexicon or paper)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEXICON = REPO / "ci" / "de_llm_lexicon.md"
PAPER = REPO / "paper" / "main.tex"
DEFAULT_OUT = REPO / "ci" / "author_lexicon_drift_results.json"

# Section headings that introduce protected phrase tables.
PROTECTED_SECTIONS = {
    "1a": "1a. the follow-on paper inheritance",
    "1b": "1b. NeurIPS area-signal",
    "1c": "1c. Conceptual scaffold",
}


@dataclass
class ProtectedPhrase:
    section: str
    raw: str
    pattern: str
    count: int = 0
    present: bool = False


def _extract_phrases_from_section(lexicon_text: str, section_heading: str) -> list[str]:
    """Pull backtick-quoted phrases from the table under one section."""
    sections = re.split(r"^### ", lexicon_text, flags=re.MULTILINE)
    target_block = ""
    for s in sections:
        if s.startswith(section_heading):
            target_block = s
            break
    if not target_block:
        return []

    end_idx = target_block.find("\n### ")
    if end_idx < 0:
        end_idx = target_block.find("\n## ")
    if end_idx > 0:
        target_block = target_block[:end_idx]

    phrases: list[str] = []
    seen: set[str] = set()
    for line in target_block.splitlines():
        if not line.startswith("|"):
            continue
        if line.startswith("| Phrase") or line.startswith("|---"):
            continue
        if line.startswith("| Phrase / pattern"):
            continue
        first_cell_match = re.match(r"^\|\s*([^|]+)\s*\|", line)
        if not first_cell_match:
            continue
        cell = first_cell_match.group(1).strip()
        for m in re.finditer(r"`([^`]+)`", cell):
            raw = m.group(1).strip()
            if raw and raw not in seen:
                phrases.append(raw)
                seen.add(raw)
    return phrases


def _normalize_for_math_match(text: str) -> str:
    """Aggressively normalize text for math-phrase substring comparison.

    The same normalization is applied to both the lexicon phrase and the
    paper text, so any rendering difference that survives this transform
    represents a true drift.
    """
    s = text
    # Order matters. Longer macros must be replaced before their shorter
    # prefixes; otherwise `\ge` consumes the prefix of `\geq` and leaves
    # an orphaned `q` that will not match a clean `GEQ`.
    s = s.replace("\\geq", "GEQ")
    s = s.replace("\\ge", "GEQ")
    s = s.replace("\\leq", "LEQ")
    s = s.replace("\\le", "LEQ")
    s = s.replace("\\propto", "PROPTO")
    s = s.replace("\\rhohat", "RHOHAT")
    s = s.replace("\\hat{\\rho}", "RHOHAT")
    s = s.replace("\\hat\\rho", "RHOHAT")
    s = s.replace("{-}", "-")
    s = s.replace("$", "")
    s = re.sub(r"[\s~]+", "", s)
    s = s.lower()
    return s


def _build_search_pattern(raw_phrase: str) -> str:
    """Convert a raw lexicon phrase into a regex tolerant of LaTeX renderings.

    Tolerances:
    - Math wrappers stripped from the search target.
    - Whitespace becomes flexible whitespace, also tolerant of LaTeX kerning.
    - `\\hat{\\rho}` accepts `\\rhohat`, `\\hat\\rho`, and the macro alias.
    - Curly braces around math kerning are optional.
    - Comma kerning `{,}` is accepted as `,` or `{,}`.
    - `\\geq` and `\\le` accept both bare and macro forms.
    - `\\propto` and `\\geq` and `\\le` are accepted as substitutes for one
      another in formula spines (the paper may state the bound as
      proportionality or as inequality with a leading constant).
    """
    p = raw_phrase
    p = p.strip("$")
    p = re.escape(p)
    p = p.replace(r"\ ", r"[\s~]+")

    p = p.replace(r"\\hat\\\{\\rho\\\}", r"(?:\\hat\{\\rho\}|\\rhohat|\\hat\\rho)")
    p = p.replace(r"\\hat\{\\rho\}", r"(?:\\hat\{\\rho\}|\\rhohat|\\hat\\rho)")
    p = p.replace(r"\{,\}", r"[,\{\}]?")
    p = p.replace(r"/", r"\s*/\s*")

    p = p.replace(r"\\geq", r"(?:\\geq|\\ge|>=)")
    p = p.replace(r"\\le", r"(?:\\le|\\leq|<=)")

    # LaTeX math kerning braces around hyphens: `1-rho` may appear as
    # `1{-}\rho`. Accept both renderings.
    p = p.replace(r"1\\-", r"1\{?-\}?")
    p = p.replace(r"k\\-", r"k\{?-\}?")
    p = p.replace(r"-\\rho", r"-\\rho")
    p = p.replace(r"\(k\\-1\)", r"\(k\{?-\}?1\)")
    p = p.replace(r"-1\)", r"\{?-\}?1\)")
    p = p.replace(r"1-\\rho", r"1\{?-\}?\\rho")

    p = p.replace(r",", r"[,\s]*")

    p = p.replace(r"\\delta_\{\\min\}", r"\\delta_\{?\\min\}?")

    return p


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json-out", default=str(DEFAULT_OUT),
                        help="path for JSON results")
    parser.add_argument("--paper", default=str(PAPER),
                        help="path to main.tex")
    parser.add_argument("--lexicon", default=str(LEXICON),
                        help="path to de_llm_lexicon.md")
    args = parser.parse_args()

    paper_path = Path(args.paper)
    lex_path = Path(args.lexicon)
    if not paper_path.exists():
        print(f"ERROR: paper not found at {paper_path}", file=sys.stderr)
        return 2
    if not lex_path.exists():
        print(f"ERROR: lexicon not found at {lex_path}", file=sys.stderr)
        return 2

    paper_text = paper_path.read_text(encoding="utf-8")
    lex_text = lex_path.read_text(encoding="utf-8")

    # Normalize the paper text once for math-heavy phrase substring matching.
    # The normalization strips whitespace, math kerning braces, math
    # delimiters, and unifies inequality macros so that lexicon strings
    # can be compared as canonical token streams.
    paper_normalized = _normalize_for_math_match(paper_text)

    phrases: list[ProtectedPhrase] = []
    for section_id, heading in PROTECTED_SECTIONS.items():
        for raw in _extract_phrases_from_section(lex_text, heading):
            count = 0
            # Math-heavy phrase: try normalized substring match first.
            if "$" in raw or "\\" in raw:
                raw_norm = _normalize_for_math_match(raw)
                if raw_norm and raw_norm in paper_normalized:
                    count = paper_normalized.count(raw_norm)
            # Fall through to regex even if math-match found; regex captures
            # the actual count in the original-rendered text where possible.
            if count == 0:
                pattern = _build_search_pattern(raw)
                try:
                    count = len(re.findall(pattern, paper_text, re.IGNORECASE))
                except re.error:
                    count = 0
            phrases.append(ProtectedPhrase(
                section=section_id,
                raw=raw,
                pattern=_build_search_pattern(raw),
                count=count,
                present=(count > 0),
            ))

    n_total = len(phrases)
    n_present = sum(1 for p in phrases if p.present)
    n_missing = n_total - n_present
    missing = [p for p in phrases if not p.present]

    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    out = {
        "_meta": {
            "script": "ci/author_lexicon_drift_check.py",
            "script_hash": script_hash,
            "audience": "author-only; not for reviewer bundle",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "paper_path": str(paper_path),
            "lexicon_path": str(lex_path),
        },
        "status": "PASS" if n_missing == 0 else "FAIL",
        "summary": {
            "status": "PASS" if n_missing == 0 else "FAIL",
            "total_protected_phrases": n_total,
            "present_count": n_present,
            "missing_count": n_missing,
        },
        "phrases": [asdict(p) for p in phrases],
    }
    Path(args.json_out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("=" * 70)
    print("AUTHOR LEXICON DRIFT CHECK (author-only; not for reviewer bundle)")
    print("=" * 70)
    print(f"  protected phrases: {n_total}")
    print(f"  present:           {n_present}")
    print(f"  missing:           {n_missing}")
    print()
    if missing:
        print("  Missing phrases (drift detected):")
        for p in missing:
            print(f"    [{p.section}] {p.raw}")
        print()
    print(f"  Results JSON: {args.json_out}")
    print(f"  Verdict: {out['status']}")
    return 0 if n_missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
