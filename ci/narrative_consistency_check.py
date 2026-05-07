"""
narrative_consistency_check.py
-------------------------------
Verify that headline numbers appear with consistent phrasing across the
abstract / Approach paragraph / Contributions paragraph / conclusion summary.

Exit code: always 0 (advisory). Prints a summary and writes JSON.
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

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = REPO_ROOT / "ci" / "narrative_consistency_results.json"

# ---------------------------------------------------------------------------
# Headline patterns — only genuine, load-bearing claims.
# Each value is a compiled regex that is searched (not matched) against the
# section text.  Use raw strings; LaTeX backslashes are literal in the source.
# ---------------------------------------------------------------------------
HEADLINE_PATTERNS: dict[str, str] = {
    # 0/4,272 smooth-regime failures
    "smooth_regime_zero_failures": r"0/4[,{\\}]*272",
    # Spearman r_s = 1.0
    "rho_spearman_unity": r"r_s\s*[{=}]+\s*1\.0",
    # Regret ≤ 2 % (abstract says "under 2%", intro says "1.8 ± 0.4%")
    "regret_under_2pct": r"[<{\\}]*\s*2\s*\\?%\s*regret|1\.8\s*\\?pm\s*0\.4\s*\\?%",
    # 11% pivot regime
    "pivot_regime_11pct": r"11\s*\\?%\s*pivot",
    # 89% displacement contract
    "displacement_contract_89pct": r"89\s*\\?%",
    # 94% router agreement
    "router_agreement_94pct": r"94\s*\\?%\s*router",
}

# Sections where every headline claim is expected to appear.
# A claim is "consistent" if it appears in NONE or ALL of these sections.
# "drift" = appears in SOME but not all.
EXPECTED_SECTIONS = {"abstract", "approach", "contributions", "conclusion_summary"}


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

def _extract_sections(tex_lines: list[str]) -> dict[str, str]:
    """
    Returns a dict mapping section name -> concatenated text.

    Sections extracted:
      abstract           -- begin{abstract} ... end{abstract}
      approach           -- textbf{Approach.} paragraph
      contributions      -- noindent textbf{Contributions.} paragraph
      conclusion_summary -- body of section{Related Work, Limitations, ...}
                           up to begin{ack} or appendix
    """
    text: dict[str, list[str]] = {s: [] for s in EXPECTED_SECTIONS}

    in_abstract = False
    in_approach = False
    in_contributions = False
    in_conclusion = False

    for raw in tex_lines:
        stripped = raw.strip()

        # ---- abstract ------------------------------------------------
        if r"\begin{abstract}" in stripped:
            in_abstract = True
        if in_abstract:
            text["abstract"].append(raw)
        if r"\end{abstract}" in stripped:
            in_abstract = False
            continue
        if in_abstract:
            continue

        # ---- approach paragraph -------------------------------------
        if r"\textbf{Approach.}" in raw:
            in_approach = True
            in_contributions = False
            in_conclusion = False
        if in_approach:
            # Ends on blank line or next \paragraph / \section / \noindent\textbf
            if stripped == "" or (
                stripped.startswith(r"\paragraph") or
                stripped.startswith(r"\section") or
                stripped.startswith(r"\noindent\textbf")
            ):
                in_approach = False
            else:
                text["approach"].append(raw)
                continue

        # ---- contributions paragraph --------------------------------
        if r"\textbf{Contributions.}" in raw:
            in_contributions = True
            in_approach = False
            in_conclusion = False
        if in_contributions:
            if stripped == "" or stripped.startswith(r"\paragraph") or stripped.startswith(r"\section"):
                in_contributions = False
            else:
                text["contributions"].append(raw)
                continue

        # ---- conclusion summary -------------------------------------
        if re.match(r"\\section\s*\{Related Work", stripped):
            in_conclusion = True
        if in_conclusion:
            if stripped.startswith(r"\begin{ack}") or stripped.startswith(r"\appendix"):
                in_conclusion = False
            else:
                text["conclusion_summary"].append(raw)

    return {k: "\n".join(v) for k, v in text.items()}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not MAIN_TEX.exists():
        print(f"ERROR: {MAIN_TEX} not found.", file=sys.stderr)
        _write_results(
            summary={"patterns_checked": 0, "consistent": 0, "potential_drift": 0},
            per_pattern=[],
        )
        return 0  # advisory — never block

    with MAIN_TEX.open(encoding="utf-8") as f:
        tex_lines = f.readlines()

    sections = _extract_sections(tex_lines)

    # Debug: uncomment to inspect extracted text lengths
    # for k, v in sections.items():
    #     print(f"  {k}: {len(v)} chars")

    per_pattern: list[dict] = []
    consistent_count = 0
    drift_count = 0

    for name, pattern_str in HEADLINE_PATTERNS.items():
        try:
            regex = re.compile(pattern_str, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            print(f"WARNING: bad regex for {name!r}: {exc}", file=sys.stderr)
            continue

        appearances: dict[str, bool] = {}
        for section_name, section_text in sections.items():
            appearances[section_name] = bool(regex.search(section_text))

        present_in = [s for s, found in appearances.items() if found]
        absent_from = [s for s, found in appearances.items() if not found]

        if not present_in:
            verdict = "absent"
            consistent_count += 1  # not a headline here — no drift
        elif not absent_from:
            verdict = "consistent"
            consistent_count += 1
        else:
            verdict = "drift"
            drift_count += 1

        per_pattern.append({
            "name": name,
            "pattern": pattern_str,
            "appearances_by_section": appearances,
            "present_in": present_in,
            "absent_from": absent_from,
            "verdict": verdict,
        })

    summary = {
        "patterns_checked": len(per_pattern),
        "consistent": consistent_count,
        "potential_drift": drift_count,
    }

    _write_results(summary=summary, per_pattern=per_pattern)

    print(f"narrative_consistency_check: patterns_checked={len(per_pattern)} "
          f"consistent={consistent_count} potential_drift={drift_count}")

    if drift_count:
        print("\nPOTENTIAL DRIFT (headline appears in some sections but not others):")
        for p in per_pattern:
            if p["verdict"] == "drift":
                print(f"  {p['name']}")
                print(f"    present : {p['present_in']}")
                print(f"    absent  : {p['absent_from']}")
    else:
        print("All headline patterns are consistent (present everywhere or nowhere).")

    return 0


def _write_results(summary: dict, per_pattern: list[dict]) -> None:
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "per_pattern": per_pattern}
    with RESULTS_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Results written to {RESULTS_JSON}")


if __name__ == "__main__":
    sys.exit(main())
