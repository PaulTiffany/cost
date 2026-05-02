#!/usr/bin/env python3
"""
figure_value_check.py - Verify that numeric values shown IN figures
are covered by registered claims.

Layer 5 of the certification stack. Reviewers see numbers in figures
(heatmap cells, bar labels, axis ticks, in-panel annotations) before
they read the body prose. If a figure shows "33%" and the registry
claims "32%", the registry is wrong, the figure is wrong, or the
caption rounds differently — all three deserve a triage pass.

Method
------
For each rendered (non-TikZ) figure in figure_lineage.json:
  1. Run pdftotext -layout to extract every visible text token
  2. Extract numeric tokens (decimals, percentages, counts)
  3. Filter incidentals (axis-tick small ints, model version numbers)
  4. Check each surviving numeric against the union of all claim
     patterns (Tier 1 + Tier 2)
  5. Report per-figure: covered / uncovered values, and which uncovered
     ones look like they should be claims

This is a *cross-check* between what reviewers see and what the
registry catalogues. A figure value with no matching claim is a
candidate for either (a) a missing pattern in the registry, (b) a
genuine drift between figure and paper, or (c) an incidental display
value (axis tick, model version) that should be ignored.

Exit codes
----------
  0  every figure value was covered (or report-only mode)
  1  pdftotext missing, or --strict and any uncovered values found
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CLAIM_AUDIT_PY = SCRIPT_DIR / "claim_audit.py"
LINEAGE_JSON = SCRIPT_DIR / "figure_lineage.json"
RESULTS_JSON = SCRIPT_DIR / "figure_value_check_results.json"

# Same numeric token regex as the body-text sweep.
NUMERIC = re.compile(
    r"""
    (?<![A-Za-z_])
    (?:
        \d+\{?,\}?\d+(?:\.\d+)?
      | \d+\.\d+
      | \d+
    )
    """,
    re.VERBOSE,
)

# Figure-specific incidentals: things that show up as numbers in pdftotext
# output but are not empirical claims.
#   - Bare single-digit ints in isolation (axis ticks like 0/5/10)
#   - Numbers embedded in known model version strings (1.3 in DeepSeek-1.3B)
#   - Step-decimal axis ticks (0.0, 0.2, 0.5, 1.0, 1.5)
MODEL_VERSION_CONTEXT = re.compile(
    r"(?:DeepSeek|Qwen|TinyLlama|Llama|Gemini|GPT|opus|sonnet|haiku|claude)[-]?\d+(?:\.\d+)?[BbMm]?",
    re.IGNORECASE,
)

# Decimals that look like axis ticks: short fractional part, ending in a
# "round" digit. Conservative — matches 0.0, 0.2, 0.5, 1.0, 1.5, 2.5, 10.0
# but NOT 0.328, 4.31, 2.17 or other claim-bearing decimals.
def is_axis_tick_decimal(v: str) -> bool:
    if "." not in v:
        return False
    int_part, frac = v.split(".", 1)
    # Two-or-more fractional digits: not a tick (e.g., 0.023, 4.31, 0.067)
    if len(frac) >= 2:
        return False
    # Single fractional digit — only "round" endings count as ticks
    return frac in {"0", "2", "4", "5", "6", "8"}


def _to_float(v: str) -> float | None:
    try:
        return float(v.replace(",", ""))
    except (ValueError, TypeError):
        return None


def detect_arithmetic_progression_values(values: list[str], min_length: int = 4) -> set[str]:
    """Return the set of values that participate in an arithmetic
    progression of length >= min_length.

    Used to filter axis-tick sequences in figures: if a figure
    extracts values that include 0, 5, 10, 15, 20, 25, 30, those
    seven values are almost certainly y-axis tick labels, not
    empirical claims.

    Algorithm:
      1. Convert each value to float (skip non-numeric).
      2. Sort unique floats.
      3. Find the longest arithmetic-progression subset.
      4. If it has >= min_length elements with constant step,
         flag all those values as axis-tick.

    Conservative: requires the step to be uniform across at least
    min_length consecutive values, so two unrelated numerics that
    happen to be evenly spaced won't get filtered.
    """
    floats: dict[float, str] = {}
    for v in values:
        f = _to_float(v)
        if f is None:
            continue
        # Keep first string repr per value
        floats.setdefault(f, v)
    if len(floats) < min_length:
        return set()

    sorted_floats = sorted(floats.keys())
    n = len(sorted_floats)
    in_progression: set[float] = set()

    # Find runs of constant-step values
    i = 0
    while i < n - 1:
        step = sorted_floats[i + 1] - sorted_floats[i]
        if step <= 0:
            i += 1
            continue
        run_start = i
        run_end = i + 1
        while run_end + 1 < n:
            next_step = sorted_floats[run_end + 1] - sorted_floats[run_end]
            # Use relative tolerance to handle float fuzz
            if abs(next_step - step) <= max(1e-9, abs(step) * 1e-6):
                run_end += 1
            else:
                break
        run_length = run_end - run_start + 1
        if run_length >= min_length:
            for j in range(run_start, run_end + 1):
                in_progression.add(sorted_floats[j])
        i = run_end + 1 if run_length >= 2 else i + 1

    # Map back to string representations
    return {floats[f] for f in in_progression}


@dataclass
class FigureReport:
    name: str
    asset_path: str
    pdftotext_chars: int
    raw_numerics: int
    filtered_numerics: int
    covered: int
    uncovered_values: list[str]  # unique uncovered value strings

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "asset_path": self.asset_path,
            "pdftotext_chars": self.pdftotext_chars,
            "raw_numerics": self.raw_numerics,
            "filtered_numerics": self.filtered_numerics,
            "covered": self.covered,
            "uncovered_count": len(self.uncovered_values),
            "uncovered_values": self.uncovered_values,
        }


def import_claim_audit():
    spec = importlib.util.spec_from_file_location("claim_audit", CLAIM_AUDIT_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {CLAIM_AUDIT_PY}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claim_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def build_combined_pattern_set(mod) -> list[re.Pattern]:
    out: list[re.Pattern] = []
    sources = [mod.CLAIMS, mod.IMPORTANT]
    if hasattr(mod, "COMPLETE"):
        sources.append(mod.COMPLETE)
    for src in sources:
        for c in src:
            for p in c.get("patterns", []):
                try:
                    out.append(re.compile(p))
                except re.error:
                    pass
    return out


def run_pdftotext(asset: Path) -> str:
    """Extract layout-preserving text from a figure PDF."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(asset), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def is_figure_incidental(value: str, line: str, start: int, end: int) -> bool:
    """Filter values that appear in figures but aren't empirical claims."""
    # Step-decimal axis ticks (0.0, 0.2, 0.5, 1.0, 1.5, etc.)
    if is_axis_tick_decimal(value):
        return True
    # Numbers inside model version strings (DeepSeek-1.3B, opus-4.1 etc.)
    window = line[max(0, start - 20):min(len(line), end + 20)]
    for m in MODEL_VERSION_CONTEXT.finditer(window):
        if m.start() <= (start - max(0, start - 20)) <= m.end():
            return True
        if m.start() <= (end - max(0, start - 20)) <= m.end():
            return True
    return False


def extract_numerics_from_text(text: str) -> list[tuple[str, str]]:
    """Return list of (value, surrounding_context) tuples."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        for m in NUMERIC.finditer(line):
            value = m.group(0)
            if is_figure_incidental(value, line, m.start(), m.end()):
                continue
            ctx_start = max(0, m.start() - 30)
            ctx_end = min(len(line), m.end() + 30)
            out.append((value, line[ctx_start:ctx_end].strip()))
    return out


def is_covered_by_claims(value: str, context: str, patterns: list[re.Pattern]) -> bool:
    """A figure value is covered if any claim pattern matches the context."""
    for pat in patterns:
        if pat.search(context):
            return True
    # Also try matching the value alone — covers cases where the figure
    # context is sparse but the value itself is distinctive.
    for pat in patterns:
        if pat.fullmatch(value):
            return True
    return False


def check_figure(name: str, asset: Path, claim_patterns: list[re.Pattern]) -> FigureReport:
    text = run_pdftotext(asset)
    raw_numerics = extract_numerics_from_text(text)

    # Detect arithmetic-progression sequences in this figure's value set.
    # Values participating in an AP of length >= 4 are almost certainly
    # axis ticks, not empirical claims.
    all_values = [v for v, _ in raw_numerics]
    ap_values = detect_arithmetic_progression_values(all_values, min_length=4)

    covered_count = 0
    ap_filtered_count = 0
    uncovered: dict[str, str] = {}  # value -> example context (one per unique value)
    for value, ctx in raw_numerics:
        if value in ap_values:
            ap_filtered_count += 1
            covered_count += 1  # treat AP values as "covered" (they're axis ticks)
            continue
        if is_covered_by_claims(value, ctx, claim_patterns):
            covered_count += 1
        else:
            if value not in uncovered:
                uncovered[value] = ctx

    return FigureReport(
        name=name,
        asset_path=str(asset.relative_to(REPO_ROOT) if asset.is_relative_to(REPO_ROOT) else asset),
        pdftotext_chars=len(text),
        raw_numerics=len(raw_numerics),
        filtered_numerics=len(raw_numerics) - ap_filtered_count,
        covered=covered_count,
        uncovered_values=sorted(uncovered.keys(), key=lambda v: (len(v), v)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print every uncovered value with its context")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any figure has uncovered values")
    args = parser.parse_args()

    if shutil.which("pdftotext") is None:
        print("ERROR: pdftotext not found on PATH; required for figure value extraction.", file=sys.stderr)
        return 1

    if not LINEAGE_JSON.exists():
        print(f"ERROR: figure lineage manifest not found at {LINEAGE_JSON}", file=sys.stderr)
        return 1

    manifest = json.loads(LINEAGE_JSON.read_text(encoding="utf-8"))
    mod = import_claim_audit()
    claim_patterns = build_combined_pattern_set(mod)

    reports: list[FigureReport] = []
    for name, fig in manifest["figures"].items():
        if fig.get("tikz_source"):
            continue  # text in TikZ figures is rendered into main.pdf; covered by L3
        if fig.get("in_use") is False:
            continue
        asset = (REPO_ROOT / fig["asset"]).resolve()
        if not asset.exists():
            continue
        reports.append(check_figure(name, asset, claim_patterns))

    print("=" * 70)
    print("FIGURE VALUE CHECK  (in-figure numerics vs claim registry)")
    print("=" * 70)
    print(f"Manifest:        {LINEAGE_JSON}")
    print(f"Claim patterns:  {len(claim_patterns)} (from {len(mod.CLAIMS) + len(mod.IMPORTANT)} claims)")
    print()

    total_raw = sum(r.raw_numerics for r in reports)
    total_covered = sum(r.covered for r in reports)
    total_uncovered = sum(len(r.uncovered_values) for r in reports)

    print(f"{'Figure':<35} {'Numerics':>9} {'Covered':>8} {'Uncov':>6}  Coverage")
    print("-" * 70)
    for r in reports:
        cov_pct = (r.covered / r.raw_numerics * 100) if r.raw_numerics else 0
        flag = "" if not r.uncovered_values else "  <"
        print(f"  {r.name:<33} {r.raw_numerics:>9} {r.covered:>8} {len(r.uncovered_values):>6}  {cov_pct:>5.1f}%{flag}")
    print("-" * 70)
    overall = (total_covered / total_raw * 100) if total_raw else 0
    print(f"  {'OVERALL':<33} {total_raw:>9} {total_covered:>8} {total_uncovered:>6}  {overall:>5.1f}%")
    print()

    # Per-figure uncovered detail
    figs_with_uncovered = [r for r in reports if r.uncovered_values]
    if figs_with_uncovered:
        print("Uncovered values per figure (triage candidates):")
        print("-" * 70)
        for r in figs_with_uncovered:
            print(f"  {r.name}:")
            for v in r.uncovered_values:
                print(f"      [{v}]")
        print()

    payload = {
        "summary": {
            "figures_checked": len(reports),
            "total_numerics": total_raw,
            "total_covered": total_covered,
            "total_uncovered": total_uncovered,
            "coverage_percent": round(overall, 1),
        },
        "figures": [r.to_dict() for r in reports],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report written to: {RESULTS_JSON}")

    if args.strict and total_uncovered > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
