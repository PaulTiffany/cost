#!/usr/bin/env python3
"""
charitable_table_consistency_check.py

Verify that the charitable feasibility table is consistent across three sources:
  1. Source JSON: supplementary/experiments/outputs/charitable/charitable_feasibility.json
  2. Paper inline table (App V, app:charity section) in paper/main.tex
  3. Supplementary tables: supplementary/experiments/outputs/charitable/charitable_table.tex
                           supplementary/experiments/outputs/charitable/charitable_table_compact.tex

Rounding policy: half-up to nearest integer percent.
  - Uses int(x * 100 + 0.5) to avoid Python banker's rounding.
  - 0.625 -> 63, 0.624 -> 62

K values checked: [2, 3, 4, 5, 6, 8, 10]

Exit codes:
  0  all cells match across all sources
  1  at least one cell mismatch detected
  2  invocation error (missing file, parse error)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

K_VALUES = [2, 3, 4, 5, 6, 8, 10]
DECLARED_ROUNDING_POLICY = "half-up to nearest integer percent"

SOURCE_JSON = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "charitable" / "charitable_feasibility.json"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
SUPP_TABLE = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "charitable" / "charitable_table.tex"
SUPP_COMPACT = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "charitable" / "charitable_table_compact.tex"
RESULTS_JSON = REPO_ROOT / "ci" / "charitable_table_consistency_results.json"


def half_up_pct(rate: float) -> int:
    """Round a rate in [0,1] to nearest integer percent using half-up rule.

    Python's built-in round() uses banker's rounding (round half to even),
    so 0.625 * 100 = 62.5 would round to 62 (even). We want 63.
    Use int(x + 0.5) to always round 0.5 up.
    """
    return int(rate * 100 + 0.5)


def load_source_json(path: Path) -> list[int]:
    """Extract per-k feasibility percentages from the source JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data["results"]
    row = []
    for k in K_VALUES:
        entry = results[str(k)]
        rate = entry["feasibility_rate"]
        row.append(half_up_pct(rate))
    return row


def parse_feasible_row_from_tex(tex_text: str, source_label: str) -> list[int]:
    """Parse the 'feasible (%)' row from a LaTeX tabular environment.

    Matches a row starting with 'feasible' (with optional % and parens)
    and extracts the integer cell values.
    """
    # Normalize: collapse multiple spaces/newlines to single space
    text = re.sub(r"\s+", " ", tex_text)

    # Match the feasible row: starts with 'feasible' or 'feasible (%)'
    # then & separated values, ending with \\ or \bottomrule or end of tabular
    match = re.search(
        r"feasible\s*(?:\(\\?%\))?\s*&\s*((?:[0-9]+\s*&\s*)*[0-9]+)\s*\\\\",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"Could not find 'feasible (%)' row in {source_label}")

    cells_str = match.group(1)
    cells = [int(c.strip()) for c in cells_str.split("&")]
    if len(cells) != len(K_VALUES):
        raise ValueError(
            f"{source_label}: expected {len(K_VALUES)} cells in feasible row, got {len(cells)}: {cells}"
        )
    return cells


def parse_paper_row(tex_path: Path) -> list[int]:
    """Extract the charitable feasibility row from main.tex.

    The paper contains an inline tabular in the app:charity section.
    There is no \\label{tab:charity_k} on the inline tabular, but
    the section is labeled app:charity. We locate the row by finding
    the 'feasible (%)' row within the section.
    """
    text = tex_path.read_text(encoding="utf-8")

    # Find the charitable section
    charity_start = text.find(r"\section{Charitable Constraint Composition}")
    if charity_start == -1:
        # Fallback: search for app:charity label
        charity_start = text.find("app:charity")
        if charity_start == -1:
            raise ValueError("Could not locate charitable section in main.tex")

    # Extract a window of text after the section heading
    # (enough to capture the table — 2000 chars is ample)
    window = text[charity_start : charity_start + 3000]

    return parse_feasible_row_from_tex(window, "paper/main.tex (app:charity)")


def compare_rows(label: str, computed: list[int], other: list[int]) -> list[dict]:
    """Return a list of mismatch dicts for cells that differ."""
    mismatches = []
    for k, expected, got in zip(K_VALUES, computed, other):
        if expected != got:
            mismatches.append({
                "k": k,
                "source": label,
                "expected": expected,
                "got": got,
            })
    return mismatches


def main() -> int:
    # --- Load all three sources ---
    try:
        if not SOURCE_JSON.exists():
            print(f"ERROR: source JSON not found: {SOURCE_JSON}", file=sys.stderr)
            return 2
        if not MAIN_TEX.exists():
            print(f"ERROR: main.tex not found: {MAIN_TEX}", file=sys.stderr)
            return 2
        if not SUPP_TABLE.exists():
            print(f"ERROR: supplementary table not found: {SUPP_TABLE}", file=sys.stderr)
            return 2
        if not SUPP_COMPACT.exists():
            print(f"ERROR: supplementary compact table not found: {SUPP_COMPACT}", file=sys.stderr)
            return 2

        computed = load_source_json(SOURCE_JSON)
        paper_row = parse_paper_row(MAIN_TEX)
        supp_row = parse_feasible_row_from_tex(
            SUPP_TABLE.read_text(encoding="utf-8"), str(SUPP_TABLE)
        )
        compact_row = parse_feasible_row_from_tex(
            SUPP_COMPACT.read_text(encoding="utf-8"), str(SUPP_COMPACT)
        )

    except ValueError as exc:
        print(f"ERROR (parse): {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # --- Cell-by-cell comparison (no numeric tolerance; exact integer match) ---
    mismatches: list[dict] = []
    mismatches.extend(compare_rows("paper vs. source JSON", computed, paper_row))
    mismatches.extend(compare_rows("supplementary_table vs. source JSON", computed, supp_row))
    mismatches.extend(compare_rows("supplementary_compact vs. source JSON", computed, compact_row))

    passed = len(mismatches) == 0

    # --- Print summary ---
    k_header = "  ".join(f"k={k}" for k in K_VALUES)
    print(f"Rounding policy : {DECLARED_ROUNDING_POLICY}")
    print(f"K values        : {K_VALUES}")
    print(f"Computed (JSON) : {computed}  (from {SOURCE_JSON.name})")
    print(f"Paper row       : {paper_row}")
    print(f"Supp table row  : {supp_row}  ({SUPP_TABLE.name})")
    print(f"Supp compact row: {compact_row}  ({SUPP_COMPACT.name})")
    print()
    if passed:
        print("RESULT: PASS -- all cells match across all sources.")
    else:
        print(f"RESULT: FAIL -- {len(mismatches)} mismatch(es) detected:")
        for m in mismatches:
            print(f"  k={m['k']} | {m['source']} | expected {m['expected']}, got {m['got']}")

    # --- Write JSON output ---
    output = {
        "summary": {
            "passed": passed,
            "n_cells": len(K_VALUES),
        },
        "rounding_policy": DECLARED_ROUNDING_POLICY,
        "k_values": K_VALUES,
        "computed_from_source": computed,
        "paper_row": paper_row,
        "supplementary_table_row": supp_row,
        "supplementary_compact_row": compact_row,
        "mismatches": mismatches,
    }
    RESULTS_JSON.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nResults written to: {RESULTS_JSON}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
