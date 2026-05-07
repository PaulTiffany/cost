"""Figure-caption grounding cert.

Verifies that every panel of every figure in `paper/main.tex` has at
least one grounding cross-reference in its caption segment. A "grounding
reference" is any of: \\ref, \\Cref, \\cref, \\autoref, \\eqref, \\pageref,
\\citep, \\cite, \\citet, \\appref, \\hyperref, \\hyperlink, or
\\nameref. The check protects against the failure mode where a sub-panel
is added to a figure but the main caption is not updated to describe it
or to bind it to a theorem / table / data source.

Algorithm:
    For each \\begin{figure*?}...\\end{figure*?} block:
        1. Extract \\caption{...} body via brace matching.
        2. Count visible panels in the figure body. A panel is detected
           as either a top-level \\begin{minipage} or a \\subfloat.
        3. Segment the caption into header + (a) + (b) + (c) ... by
           parenthesized lowercase letter markers.
        4. Per-figure assertions:
             - if N body panels >= 2: caption must declare at least N
               panel markers (otherwise some panel is undocumented)
             - each panel segment must contain at least 1 grounding ref
             - single-panel figures: caption must contain >=1 ref
        5. Exemption: a figure source containing the comment
             % cert:caption-grounding-exempt
           is skipped (with the exemption recorded in the JSON output).

Usage:
    python ci/figure_caption_grounding_check.py
    python ci/figure_caption_grounding_check.py --json-out path

Exit:
    0 if every non-exempt figure has full caption grounding
    1 if any figure fails
    2 on invocation error (missing main.tex)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAPER_TEX = REPO / "paper" / "main.tex"
DEFAULT_OUT = REPO / "ci" / "figure_caption_grounding_results.json"

EXEMPT_MARKER = "% cert:caption-grounding-exempt"

GROUNDING_COMMANDS = (
    "ref", "Cref", "cref", "autoref", "eqref", "pageref",
    "citep", "cite", "citet", "citeyear",
    "appref", "secref", "thmref", "lemref", "corref",
    "hyperref", "hyperlink", "nameref",
)


@dataclass
class PanelOutcome:
    label: str
    n_refs: int
    grounded: bool
    text_preview: str


@dataclass
class FigureOutcome:
    fig_index: int
    line_start: int
    line_end: int
    label: str | None
    exempt: bool
    issues: list[str] = field(default_factory=list)
    n_body_panels: int = 0
    panels: list[PanelOutcome] = field(default_factory=list)


_FIGURE_RE = re.compile(
    r"\\begin\{figure\*?\}(?P<body>.*?)\\end\{figure\*?\}",
    re.DOTALL,
)


def _extract_brace_arg(text: str, start: int) -> tuple[str, int] | None:
    """Given text containing `{...}` starting at `start`, return (content, end_pos).
    Handles nested braces. Returns None if unbalanced.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 1
    i = start + 1
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return None


def extract_caption(figure_body: str) -> str | None:
    m = re.search(r"\\caption\b\s*", figure_body)
    if not m:
        return None
    i = m.end()
    if i >= len(figure_body) or figure_body[i] != "{":
        return None
    arg = _extract_brace_arg(figure_body, i)
    if arg is None:
        return None
    return arg[0]


def extract_label(figure_body: str) -> str | None:
    m = re.search(r"\\label\b\s*\{([^}]+)\}", figure_body)
    if not m:
        return None
    return m.group(1)


def count_body_panels(figure_body: str) -> int:
    """Top-level panel count. We use the maximum of two simple signals:
    minipages (the dominant idiom in this paper) and \\subfloat usage."""
    n_mini = len(re.findall(r"\\begin\{minipage\}", figure_body))
    n_sub = len(re.findall(r"\\subfloat\b", figure_body))
    return max(n_mini, n_sub)


_PANEL_MARKER_RE = re.compile(
    r"(?:(?<=^)|(?<=[.;])|(?<=\\\\)|(?<=\}\s)|(?<=\}))\s*\(([a-f])\)"
)


def segment_caption(caption: str) -> list[tuple[str, str]]:
    """Split caption into [(label, text), ...]. The first segment may be the
    header (text before the first panel marker), labelled 'header'."""
    markers = list(_PANEL_MARKER_RE.finditer(caption))
    if not markers:
        return [("header", caption.strip())]
    segments: list[tuple[str, str]] = []
    if markers[0].start() > 0:
        head = caption[:markers[0].start()].strip()
        if head:
            segments.append(("header", head))
    for i, m in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(caption)
        segments.append((m.group(0), caption[m.start():end].strip()))
    return segments


def count_refs(text: str) -> int:
    n = 0
    for cmd in GROUNDING_COMMANDS:
        n += len(re.findall(r"\\" + cmd + r"\b", text))
    return n


def _line_of(offset: int, line_offsets: list[int]) -> int:
    """Binary-search the line number (1-indexed) of a character offset."""
    import bisect
    return bisect.bisect_right(line_offsets, offset)


def check(tex: str) -> list[FigureOutcome]:
    line_offsets: list[int] = [0]
    for i, c in enumerate(tex):
        if c == "\n":
            line_offsets.append(i + 1)

    outcomes: list[FigureOutcome] = []
    for idx, m in enumerate(_FIGURE_RE.finditer(tex), start=1):
        body = m.group("body")
        label = extract_label(body)
        exempt = EXEMPT_MARKER in body
        outcome = FigureOutcome(
            fig_index=idx,
            line_start=_line_of(m.start(), line_offsets),
            line_end=_line_of(m.end(), line_offsets),
            label=label,
            exempt=exempt,
        )
        if exempt:
            outcomes.append(outcome)
            continue

        caption = extract_caption(body)
        if caption is None:
            outcome.issues.append("no \\caption{} found in figure")
            outcomes.append(outcome)
            continue

        n_body = count_body_panels(body)
        outcome.n_body_panels = n_body
        segments = segment_caption(caption)
        caption_panels = [s for s in segments if s[0] != "header"]

        if n_body >= 2 and len(caption_panels) < n_body:
            outcome.issues.append(
                f"{n_body} panels in figure body but only "
                f"{len(caption_panels)} panel markers ({sorted(s[0] for s in caption_panels)}) "
                f"in caption; some panel is undocumented"
            )

        if not caption_panels:
            n = count_refs(caption)
            outcome.panels.append(PanelOutcome(
                label="single",
                n_refs=n,
                grounded=(n >= 1),
                text_preview=caption[:80].replace("\n", " "),
            ))
            if n == 0:
                outcome.issues.append(
                    "single-panel caption has no grounding reference"
                )
        else:
            for label, segment_text in caption_panels:
                n = count_refs(segment_text)
                outcome.panels.append(PanelOutcome(
                    label=label,
                    n_refs=n,
                    grounded=(n >= 1),
                    text_preview=segment_text[:80].replace("\n", " "),
                ))
                if n == 0:
                    outcome.issues.append(
                        f"panel {label}: no grounding reference in its caption segment"
                    )

        outcomes.append(outcome)

    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--paper", default=str(PAPER_TEX))
    parser.add_argument("--json-out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    paper_path = Path(args.paper)
    if not paper_path.exists():
        print(f"ERROR: paper not found at {paper_path}", file=sys.stderr)
        return 2

    tex = paper_path.read_text(encoding="utf-8")
    outcomes = check(tex)

    n_total = len(outcomes)
    n_exempt = sum(1 for o in outcomes if o.exempt)
    n_failed = sum(1 for o in outcomes if (not o.exempt) and o.issues)
    n_passed = n_total - n_exempt - n_failed
    verdict = "PASS" if n_failed == 0 else "FAIL"

    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = {
        "_meta": {
            "script": "ci/figure_caption_grounding_check.py",
            "script_hash": script_hash,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "paper_path": str(paper_path),
            "grounding_commands": list(GROUNDING_COMMANDS),
            "exempt_marker": EXEMPT_MARKER,
        },
        "status": verdict,
        "summary": {
            "status": verdict,
            "n_total_figures": n_total,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "n_exempt": n_exempt,
        },
        "figures": [asdict(o) for o in outcomes],
    }
    Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("=" * 70)
    print("FIGURE CAPTION GROUNDING CHECK")
    print("=" * 70)
    print(f"  paper:           {paper_path}")
    print(f"  total figures:   {n_total}")
    print(f"  passed:          {n_passed}")
    print(f"  failed:          {n_failed}")
    print(f"  exempt:          {n_exempt}")
    if n_failed:
        print()
        for o in outcomes:
            if o.exempt or not o.issues:
                continue
            ident = o.label or f"#{o.fig_index}"
            print(f"  [FAIL] figure {ident} (lines {o.line_start}-{o.line_end}):")
            for iss in o.issues:
                line = f"      {iss}"
                print(line.encode("ascii", "replace").decode("ascii"))
    print()
    print(f"  Results JSON: {args.json_out}")
    print(f"  Verdict: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
