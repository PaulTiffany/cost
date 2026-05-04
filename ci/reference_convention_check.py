#!/usr/bin/env python3
"""
reference_convention_check.py

L26 cert layer (suite: submission_hygiene). Lints paper/main.tex for
drift from the reference convention:

  - Mid-sentence parenthetical refs use \\appref{X} / \\secref{X} macros
    (which expand to "App.~\\ref{X}" / "Sec.~\\ref{X}").
  - Sentence-start refs may stay spelled out as "Appendix~\\ref{X}" /
    "Section~\\ref{X}" because English readability prefers it.

Failure = a mid-sentence spelled-out form. Sentence-start, title, and
caption occurrences are exempt. Also exempt: \\Cref{}, \\cref{} (cleveref
auto-formatters; the package is loaded in the preamble even though the
paper currently uses \\ref{} directly).
"""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_PATH = Path(__file__).resolve().parent / "reference_convention_results.json"

TITLE_CMD_RX = re.compile(
    r"\\(?:section|subsection|subsubsection|paragraph|subparagraph|chapter|caption|title)\*?\s*\{"
)
REF_RX = re.compile(r"(?P<lead>.)(?P<word>Appendix|Section)~\\ref\{(?P<lbl>[^}]+)\}")


def title_skip_ranges(s: str):
    skips = []
    for m in TITLE_CMD_RX.finditer(s):
        i = m.end() - 1
        depth = 0
        while i < len(s):
            c = s[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    skips.append((m.start(), i + 1))
                    break
            i += 1
    return skips


def line_for(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def main() -> int:
    if not MAIN_TEX.exists():
        print(f"main.tex not found at {MAIN_TEX}", file=sys.stderr)
        return 1
    text = MAIN_TEX.read_text(encoding="utf-8")
    doc_start = text.find(r"\begin{document}")
    body = text[doc_start:]
    body_offset = doc_start
    skips = title_skip_ranges(body)

    def in_skip(idx):
        for s, e in skips:
            if s <= idx < e:
                return True
        return False

    findings = []
    n_total = 0
    n_mid_sentence_ok = 0   # sentence-start spelled, allowed
    n_mid_sentence_drift = 0  # mid-sentence spelled, NOT allowed
    n_in_title = 0

    for m in REF_RX.finditer(body):
        n_total += 1
        if in_skip(m.start("word")):
            n_in_title += 1
            continue
        lead = m.group("lead")
        word = m.group("word")
        label = m.group("lbl")
        is_mid = False
        if lead in "(,;-":
            is_mid = True
        elif lead in " \t":
            k = m.start("lead") - 1
            while k >= 0 and body[k] in " \t":
                k -= 1
            prev = body[k] if k >= 0 else "\n"
            if prev.isalnum() and (prev.islower() or prev.isdigit()):
                is_mid = True
            elif prev in ",;)]}-":
                is_mid = True
        if is_mid:
            n_mid_sentence_drift += 1
            findings.append({
                "kind": "mid_sentence_spelled_form",
                "line": line_for(text, body_offset + m.start("word")),
                "word": word,
                "label": label,
                "fix": f"replace with \\{'appref' if word == 'Appendix' else 'secref'}{{{label}}}",
            })
        else:
            n_mid_sentence_ok += 1

    payload = {
        "passed": n_mid_sentence_drift == 0,
        "n_refs_total": n_total,
        "n_in_title_or_caption": n_in_title,
        "n_sentence_start_spelled_ok": n_mid_sentence_ok,
        "n_mid_sentence_drift": n_mid_sentence_drift,
        "convention": (
            "Mid-sentence parenthetical refs use \\appref{} / \\secref{} macros. "
            "Sentence-start refs may stay spelled out as Appendix~\\ref{} / Section~\\ref{}."
        ),
        "findings": findings,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"reference_convention_check: scanned {n_total} Appendix/Section refs in main.tex")
    print(f"  in title or caption (exempt):    {n_in_title}")
    print(f"  sentence-start spelled (allowed): {n_mid_sentence_ok}")
    print(f"  mid-sentence spelled (drift):     {n_mid_sentence_drift}")
    if n_mid_sentence_drift:
        print()
        print("Drift findings (replace with the macro):")
        for f in findings[:10]:
            print(f"  line {f['line']:>5}: {f['word']}~\\ref{{{f['label']}}}  ->  {f['fix']}")
        if len(findings) > 10:
            print(f"  ... ({len(findings) - 10} more, see {RESULTS_PATH.name})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
