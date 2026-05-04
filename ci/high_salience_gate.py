"""
high_salience_gate.py
---------------------
Promote uncovered numerics from advisory (L3) to BLOCKING for high-salience
paper regions.

Exit codes:
  0 — PASS (no high-salience uncovered numerics, or no uncovered file found)
  1 — FAIL (one or more high-salience uncovered numerics found)
  2 — Invocation error
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
UNCOVERED_JSON = REPO_ROOT / "ci" / "claim_coverage_uncovered.json"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = REPO_ROOT / "ci" / "high_salience_gate_results.json"

# ---------------------------------------------------------------------------
# Exemption list — known-decorative numerics that must never be auto-covered.
# Format: set of (line_no, value) pairs, or just value strings for broad exemptions.
# ---------------------------------------------------------------------------
EXEMPTIONS: set[str] = {
    # pgfplots compat version — appears in preamble, not a paper claim
    "1.18",
    # Year in acknowledgements URL — not a paper claim
    "2026",
    # Interval endpoint notation [-1,1], (-1,1), [0,1] — math ranges, not claims
    "1,1",
    "0,1",
}

HIGH_SALIENCE_REGIONS = {
    "abstract",
    "section_title",
    "figure_caption",
    "contributions",
    "scope_falsifiable",
    "conclusion_summary",
}


# ---------------------------------------------------------------------------
# Region detection helpers
# ---------------------------------------------------------------------------

def _extract_regions(tex_lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    """
    Walk main.tex and tag each line with its region label.
    Returns a dict: region_name -> list of (line_no_1indexed, raw_line).

    A line may belong to exactly one region (first match wins).
    Lines not matching any high-salience region get region "other".
    """
    tagged: dict[str, list[tuple[int, str]]] = {r: [] for r in HIGH_SALIENCE_REGIONS}
    tagged["other"] = []

    in_abstract = False
    in_caption = False          # multi-line caption tracking
    caption_depth = 0           # brace depth inside \caption{
    in_contributions = False
    in_scope_falsifiable = False
    in_conclusion = False
    conclusion_started = False  # set when we hit the conclusion \section

    for i, raw in enumerate(tex_lines):
        line_no = i + 1
        stripped = raw.strip()

        # ---- abstract ------------------------------------------------
        if r"\begin{abstract}" in stripped:
            in_abstract = True
        if in_abstract:
            tagged["abstract"].append((line_no, raw))
        if r"\end{abstract}" in stripped:
            in_abstract = False
            continue
        if in_abstract:
            continue

        # ---- section titles -----------------------------------------
        if re.match(r"\\section\s*\{", stripped) or re.match(r"\\subsection\s*\{", stripped):
            tagged["section_title"].append((line_no, raw))
            # Also start/end conclusion tracking
            if "Related Work" in stripped and "Limitations" in stripped:
                conclusion_started = True
                in_conclusion = True
            else:
                # A new section after the conclusion ends it
                if conclusion_started and in_conclusion:
                    # We only stop conclusion at \appendix or next top-level \section
                    # that is NOT related work
                    if re.match(r"\\section\s*\{", stripped):
                        in_conclusion = False
            continue

        # ---- \appendix — ends conclusion region ----------------------
        if stripped.startswith(r"\appendix"):
            in_conclusion = False
            conclusion_started = False

        # ---- figure captions -----------------------------------------
        # Detect \caption{ (possibly multi-line) by tracking brace depth
        if not in_caption:
            m = re.search(r"\\caption\s*\{", raw)
            if m:
                in_caption = True
                caption_depth = 1
                # Count braces from the opening { onward
                rest = raw[m.end():]
                for ch in rest:
                    if ch == "{":
                        caption_depth += 1
                    elif ch == "}":
                        caption_depth -= 1
                        if caption_depth == 0:
                            in_caption = False
                            break
                tagged["figure_caption"].append((line_no, raw))
                continue
        else:
            # Continue multi-line caption
            for ch in raw:
                if ch == "{":
                    caption_depth += 1
                elif ch == "}":
                    caption_depth -= 1
                    if caption_depth == 0:
                        in_caption = False
                        break
            tagged["figure_caption"].append((line_no, raw))
            continue

        # ---- contributions paragraph ---------------------------------
        if r"\textbf{Contributions.}" in raw or r"\noindent\textbf{Contributions.}" in raw:
            in_contributions = True
            in_scope_falsifiable = False
        if in_contributions:
            if stripped == "" or (stripped.startswith("\\") and
                                  not stripped.startswith(r"\noindent") and
                                  not stripped.startswith(r"\textbf") and
                                  not stripped.startswith(r"\emph")):
                # Blank line or a new command that isn't inline formatting ends the paragraph
                # But \paragraph{...} definitely ends it
                if stripped.startswith(r"\paragraph") or stripped.startswith(r"\section"):
                    in_contributions = False
                elif stripped == "":
                    # Blank line ends a LaTeX paragraph
                    in_contributions = False
            if in_contributions:
                tagged["contributions"].append((line_no, raw))
                continue

        # ---- scope and falsifiable predictions paragraph -------------
        if r"\paragraph{Scope and Falsifiable Predictions.}" in raw:
            in_scope_falsifiable = True
            in_contributions = False
        if in_scope_falsifiable:
            if stripped == "" or stripped.startswith(r"\section") or stripped.startswith(r"\paragraph"):
                if stripped == "":
                    in_scope_falsifiable = False
                elif stripped.startswith(r"\section") or (stripped.startswith(r"\paragraph")
                                                          and "Scope" not in stripped):
                    in_scope_falsifiable = False
            if in_scope_falsifiable:
                tagged["scope_falsifiable"].append((line_no, raw))
                continue

        # ---- conclusion summary (body of Related Work section) ------
        if in_conclusion:
            # Skip the \section line itself (already tagged as section_title)
            tagged["conclusion_summary"].append((line_no, raw))
            continue

        tagged["other"].append((line_no, raw))

    return tagged


def _region_for_line(line_no: int, value: str,
                     region_map: dict[str, list[tuple[int, str]]]) -> str:
    """Return the region label for a given 1-indexed line number and numeric value.

    Special handling for section_title: L3 sometimes attributes a number to a
    section or subsection line even though the number appears in the BODY text
    below. We verify the number actually occurs inside the section title argument
    before classifying as section_title.
    """
    for region, entries in region_map.items():
        for ln, raw in entries:
            if ln == line_no:
                if region == "section_title":
                    # Extract the title argument text — text between the first
                    # opening brace and its matching close, after \section / \subsection
                    m = re.search(r"\\(?:sub)*section\s*\{([^}]*)\}", raw)
                    title_text = m.group(1) if m else ""
                    if value not in title_text:
                        # Number is not inside the title; fall through to "other"
                        continue
                return region
    return "other"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not UNCOVERED_JSON.exists():
        print(f"INFO: {UNCOVERED_JSON} not found — L3 has not run yet. Gate passes.")
        _write_results(
            summary={"total_uncovered": 0, "high_salience": 0, "low_salience": 0,
                     "exempted": 0, "passed": True},
            findings=[],
        )
        return 0

    if not MAIN_TEX.exists():
        print(f"ERROR: {MAIN_TEX} not found.", file=sys.stderr)
        return 2

    with UNCOVERED_JSON.open(encoding="utf-8") as f:
        uncovered_data = json.load(f)

    uncovered: list[dict] = uncovered_data.get("uncovered", [])

    with MAIN_TEX.open(encoding="utf-8") as f:
        tex_lines = f.readlines()

    region_map = _extract_regions(tex_lines)

    findings: list[dict] = []
    exempted_count = 0
    high_count = 0
    low_count = 0

    for entry in uncovered:
        value = str(entry.get("value", ""))
        line_no = int(entry.get("line_no", 0))
        context = entry.get("context", "")

        # Check exemptions
        if value in EXEMPTIONS:
            exempted_count += 1
            continue
        # Single-digit list enumerators "(1) (2) (3) ..." in contributions/scope.
        # The bare digit is structural list markup, not a substantive claim.
        if value in {"1", "2", "3", "4", "5", "6", "7", "8", "9"} and (f"({value})" in context or f"({value})~" in context):
            exempted_count += 1
            continue

        region = _region_for_line(line_no, value, region_map)

        if region in HIGH_SALIENCE_REGIONS:
            high_count += 1
            findings.append({
                "number": value,
                "line": line_no,
                "region": region,
                "context": context,
            })
        else:
            low_count += 1

    passed = high_count == 0

    summary = {
        "total_uncovered": len(uncovered),
        "high_salience": high_count,
        "low_salience": low_count,
        "exempted": exempted_count,
        "passed": passed,
    }

    _write_results(summary=summary, findings=findings)

    print(f"high_salience_gate: total_uncovered={len(uncovered)} "
          f"high_salience={high_count} low_salience={low_count} "
          f"exempted={exempted_count} passed={passed}")

    if findings:
        print("\nHIGH-SALIENCE FINDINGS:")
        for f_ in findings:
            print(f"  line {f_['line']:5d} [{f_['region']:20s}] "
                  f"value={f_['number']!r:10s} ctx={f_['context']!r}")

    if not passed:
        print("\nFAIL: uncovered numerics in high-salience regions detected.")
        return 1

    print("PASS")
    return 0


def _write_results(summary: dict, findings: list[dict]) -> None:
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "high_salience_findings": findings}
    with RESULTS_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Results written to {RESULTS_JSON}")


if __name__ == "__main__":
    sys.exit(main())
