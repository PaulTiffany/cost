#!/usr/bin/env python3
"""
table_value_check.py - Parse LaTeX tabular blocks under specific labels and
verify cell values against a manifest of expected (label, row_key, col_key)
-> expected_pattern expectations.

Method
------
1. Read paper/main.tex.
2. For each label in TARGET_LABELS, find \\label{X} and walk back to the
   enclosing \\begin{tabular} ... \\end{tabular} (handles resizebox wrappers,
   tabular*, etc.).
3. Parse tabular: split by \\\\ for rows; split each row by & for cells.
   Strip whitespace, LaTeX commands, and math markers.
4. First data row's first cell is row key; header row provides column keys.
5. Load ci/table_value_expectations.json manifest.
6. For each expectation: extract cell, regex-match against expected_pattern.

Output: ci/table_value_results.json
Exit: 0 (all pass), 1 (some DRIFT), 2 (parse/load error).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
MANIFEST_JSON = SCRIPT_DIR / "table_value_expectations.json"
RESULTS_JSON = SCRIPT_DIR / "table_value_results.json"

TARGET_LABELS = [
    "tab:calibration",
    "tab:claude_family",
    "tab:llm_bridge",
    "tab:json_nl",
    "tab:lipschitz_calibration",
]


# ---------------------------------------------------------------------------
# LaTeX cleaning helpers
# ---------------------------------------------------------------------------

def _strip_latex(cell: str) -> str:
    """
    Remove common LaTeX markup to leave bare text/numbers.
    Preserves %, digits, letters, +/-, ., x (for ratios like 4.4x).
    """
    s = cell.strip()
    # Unwrap common commands: \textbf{}, \emph{}, \mathbf{}, etc.
    for _ in range(5):
        s = re.sub(r"\\(?:textbf|emph|text|mathbf|mathrm|mathit|boldsymbol)\{([^}]*)\}", r"\1", s)
    # Remove $...$ inline math markers
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    # Normalize \times -> x
    s = s.replace(r"\times", "x")
    # Normalize escaped special chars: \% -> %, \$ -> $, \& -> &
    s = re.sub(r"\\([%$&_#])", r"\1", s)
    # Remove remaining backslash commands (e.g. \pm, \hat, \rhohat) replace with space
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    # Remove braces
    s = s.replace("{", "").replace("}", "")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Tabular extraction
# ---------------------------------------------------------------------------

def _find_tabular_for_label(tex: str, label: str) -> tuple[str | None, str]:
    """
    Find the tabular block associated with \\label{label}.
    Returns (tabular_content, status_message).

    The label may appear BEFORE (in \\caption area) or INSIDE the tabular.
    Strategy: find the label, then locate the NEAREST \\begin{tabular} either
    within a window of 3000 chars before or 1000 chars after, prefer whichever
    is closest. Then walk forward to find the matching \\end{tabular}.

    Handles resizebox wrappers and tabular* / tabularx variants.
    """
    label_pat = re.compile(
        r"\\label\{" + re.escape(label) + r"\}", re.DOTALL
    )
    m = label_pat.search(tex)
    if not m:
        return None, f"label {label!r} not found in tex"

    label_pos = m.start()

    begin_pat = re.compile(
        r"\\begin\{(tabular[x*]?)\}\s*(?:\{[^}]*\}\s*)?(?:\{[^}]*\})?"
    )

    # Search backward window (3000 chars) for nearest begin{tabular}
    window_start = max(0, label_pos - 3000)
    backward_candidates = list(begin_pat.finditer(tex[window_start:label_pos]))
    backward_match = backward_candidates[-1] if backward_candidates else None
    backward_dist = (label_pos - window_start - backward_match.start()) if backward_match else None

    # Search forward window (1000 chars) for nearest begin{tabular}
    forward_slice = tex[label_pos: label_pos + 1000]
    forward_candidates = list(begin_pat.finditer(forward_slice))
    forward_match = forward_candidates[0] if forward_candidates else None
    forward_dist = forward_match.start() if forward_match else None

    # Choose nearest
    if backward_match is None and forward_match is None:
        return None, f"no \\begin{{tabular}} found near label {label!r}"

    if forward_match is None:
        chosen = backward_match
        chosen_abs_start = window_start + backward_match.start()
    elif backward_match is None:
        chosen = forward_match
        chosen_abs_start = label_pos + forward_match.start()
    else:
        if forward_dist <= backward_dist:
            chosen = forward_match
            chosen_abs_start = label_pos + forward_match.start()
        else:
            chosen = backward_match
            chosen_abs_start = window_start + backward_match.start()

    env_name = chosen.group(1)  # e.g. "tabular" or "tabular*"
    end_tag = f"\\end{{{env_name}}}"

    # Find matching \\end{tabular} after the begin
    end_pos = tex.find(end_tag, chosen_abs_start + len(chosen.group(0)))
    if end_pos == -1:
        return None, f"no \\end{{{env_name}}} found after \\begin{{{env_name}}} for label {label!r}"

    content = tex[chosen_abs_start: end_pos + len(end_tag)]
    return content, "ok"


def _parse_tabular(tabular_tex: str) -> tuple[list[list[str]] | None, str]:
    """
    Parse tabular content into a list of rows, each a list of cell strings.
    Strips rule commands (toprule/midrule/etc.) but preserves actual data rows
    even when they follow a rule command within the same \\-delimited segment.
    Returns (rows, status).
    """
    # Strip the \\begin{tabular}{...} header
    content = re.sub(r"\\begin\{tabular[x*]?\}\s*(?:\{[^}]*\}\s*)?(?:\{[^}]*\})?", "", tabular_tex)
    content = re.sub(r"\\end\{tabular[x*]?\}", "", content)
    # Remove LaTeX comment-percent at end of line (resizebox artifact)
    content = re.sub(r"%\s*\n", "\n", content)
    # Strip rule-only lines from content BEFORE splitting on \\
    # This handles \toprule, \midrule, \bottomrule, \hline, \cmidrule on their own lines
    content = re.sub(
        r"\\(toprule|midrule|bottomrule|hline|addlinespace)(\s*\n|\s*$)",
        "\n",
        content,
    )
    # Strip \cmidrule{...} inline occurrences (may span multiple on one line)
    content = re.sub(r"\\cmidrule(?:\([^)]*\))?\{[^}]*\}", "", content)

    # Split on \\  (row separator). Be careful: \\[2pt] style variants
    row_parts = re.split(r"\\\\(?:\[[^\]]*\])?", content)

    rows: list[list[str]] = []
    for part in row_parts:
        stripped = part.strip()
        if not stripped:
            continue
        cells = stripped.split("&")
        cleaned = [_strip_latex(c) for c in cells]
        # Skip entirely empty rows
        if all(c == "" for c in cleaned):
            continue
        rows.append(cleaned)

    if not rows:
        return None, "no data rows found after parsing"
    return rows, "ok"


def _build_cell_map(
    rows: list[list[str]],
) -> tuple[dict[tuple[str, str], str] | None, str]:
    """
    Build a (row_key, col_key) -> cell_value mapping.
    Assumes first row is the header row (column keys after the first cell).
    Returns (cell_map, status).
    """
    if len(rows) < 2:
        return None, "need at least 2 rows (header + data)"

    header_row = rows[0]
    col_keys = header_row  # include first header cell too
    # Treat header[0] as the row-label column name; col keys start from index 1
    col_keys_indexed = header_row  # col_keys[i] for column i

    cell_map: dict[tuple[str, str], str] = {}
    for row in rows[1:]:
        if not row or row[0] == "":
            continue
        row_key = row[0]
        for j, cell in enumerate(row[1:], start=1):
            col_key = col_keys_indexed[j] if j < len(col_keys_indexed) else f"col{j}"
            cell_map[(row_key, col_key)] = cell

    return cell_map, "ok"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Table value check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any DRIFT instead of just reporting",
    )
    args = parser.parse_args(argv)

    if not MAIN_TEX.exists():
        print(f"ERROR: {MAIN_TEX} not found", file=sys.stderr)
        return 2

    if not MANIFEST_JSON.exists():
        print(f"ERROR: {MANIFEST_JSON} not found", file=sys.stderr)
        return 2

    tex = MAIN_TEX.read_text(encoding="utf-8", errors="replace")

    with MANIFEST_JSON.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    expectations = manifest.get("expectations", [])

    # Pre-parse all target tables
    table_cache: dict[str, dict[tuple[str, str], str]] = {}
    table_status: dict[str, str] = {}

    for label in TARGET_LABELS:
        tabular_tex, status = _find_tabular_for_label(tex, label)
        if tabular_tex is None:
            table_status[label] = f"SKIP: {status}"
            print(f"INFO: {label}: {status}")
            continue

        rows, status = _parse_tabular(tabular_tex)
        if rows is None:
            table_status[label] = f"SKIP: parse failed: {status}"
            print(f"INFO: {label}: parse failed: {status}")
            continue

        cell_map, status = _build_cell_map(rows)
        if cell_map is None:
            table_status[label] = f"SKIP: cell_map failed: {status}"
            print(f"INFO: {label}: cell_map failed: {status}")
            continue

        table_cache[label] = cell_map
        table_status[label] = f"OK ({len(cell_map)} cells)"

    # Evaluate expectations
    per_expectation: list[dict] = []
    total = passed = failed = 0

    for exp in expectations:
        label = exp["label"]
        row_key = exp["row_key"]
        col_key = exp["col_key"]
        pattern = exp["expected_pattern"]

        if label not in table_cache:
            per_expectation.append({
                "label": label,
                "row": row_key,
                "col": col_key,
                "extracted_cell": None,
                "expected_pattern": pattern,
                "passed": False,
                "detail": f"table not parsed: {table_status.get(label, 'not attempted')}",
            })
            total += 1
            failed += 1
            continue

        cell_map = table_cache[label]
        # Try exact match first, then case-insensitive partial key match
        cell_value = cell_map.get((row_key, col_key))

        if cell_value is None:
            # Try partial / case-insensitive row key matching
            for (rk, ck), cv in cell_map.items():
                rk_clean = re.sub(r"\s+", " ", rk).strip()
                row_key_clean = re.sub(r"\s+", " ", row_key).strip()
                if (
                    rk_clean.lower() == row_key_clean.lower()
                    or row_key_clean.lower() in rk_clean.lower()
                ):
                    col_key_clean = re.sub(r"\s+", " ", col_key).strip()
                    ck_clean = re.sub(r"\s+", " ", ck).strip()
                    if ck_clean.lower() == col_key_clean.lower():
                        cell_value = cv
                        break

        total += 1
        if cell_value is None:
            per_expectation.append({
                "label": label,
                "row": row_key,
                "col": col_key,
                "extracted_cell": None,
                "expected_pattern": pattern,
                "passed": False,
                "detail": (
                    f"cell ({row_key!r}, {col_key!r}) not found. "
                    f"Available rows: {sorted({rk for rk, _ in cell_map})}"
                ),
            })
            failed += 1
            continue

        try:
            match = re.search(pattern, cell_value)
        except re.error as exc:
            match = None
            detail_extra = f"; regex error: {exc}"
        else:
            detail_extra = ""

        if match:
            passed += 1
            per_expectation.append({
                "label": label,
                "row": row_key,
                "col": col_key,
                "extracted_cell": cell_value,
                "expected_pattern": pattern,
                "passed": True,
                "detail": f"PASS: matched {match.group()!r}{detail_extra}",
            })
        else:
            failed += 1
            per_expectation.append({
                "label": label,
                "row": row_key,
                "col": col_key,
                "extracted_cell": cell_value,
                "expected_pattern": pattern,
                "passed": False,
                "detail": f"DRIFT: cell={cell_value!r} did not match pattern={pattern!r}{detail_extra}",
            })

    summary = {"total": total, "passed": passed, "failed": failed}
    results = {
        "summary": summary,
        "table_parse_status": table_status,
        "per_expectation": per_expectation,
    }
    RESULTS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Print summary
    print(f"table_value_check: {total} expectations, {passed} passed, {failed} failed")
    for r in per_expectation:
        status_str = "PASS" if r["passed"] else "DRIFT"
        cell_str = f"cell={r['extracted_cell']!r}" if r["extracted_cell"] is not None else "cell=<missing>"
        print(f"  [{status_str}] {r['label']} row={r['row']!r} col={r['col']!r} {cell_str}")
    print(f"Results written to {RESULTS_JSON}")

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
