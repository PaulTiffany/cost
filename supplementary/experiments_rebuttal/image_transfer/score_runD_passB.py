#!/usr/bin/env python3
"""
score_runD_passB.py - Apply the Pass B rubric to Run D images.

Rubric (cumulative; tier strictly extends):

  CONTROL: 1 demand
    C1. Image is a clear schematic illustration of the requested subject.

  LOW: CONTROL + 3 demands
    L1. <=5 visible primary elements (each visually distinct labeled
        block; a sequence of 7 terms counts as 7 elements).
    L2. <=3 distinct chromatic colors (excluding white background and
        black text/lines; gray is neutral, not chromatic).
    L3. Directional arrows for flow/ordering.

  MODERATE: LOW + 3 demands
    M1. Each primary element has a text label.
    M2. Consistent sans-serif font throughout.
    M3. ONE accent color used for emphasis (not multiple chromatic
        colors used as accents).

  HIGH: MODERATE + 4 demands
    H1. Structured legend at bottom listing visual symbols.
    H2. All primary elements horizontally aligned on a single baseline.
    H3. Consistent stroke weight throughout.
    H4. One-line title at top.

Pass B = ALL accumulated demands satisfied (binary, strict).

Scoring is hand-coded based on inspection of each PNG. Each cell records
per-demand binary and a rationale; the JSON is the auditable rubric trail.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_JSON = SCRIPT_DIR / "image_transfer_results_runD.json"
SCORE_JSON = SCRIPT_DIR / "image_transfer_runD_passB.json"


SCORES = {
    # ---- CONTROL TIER (only C1) -------------------------------------
    "D-factorial-control_t0":   {"C1": True,  "rationale": "Clear factorial recursion schematic with call/return phases."},
    "D-factorial-control_t1":   {"C1": True,  "rationale": "Clear factorial schematic, two-phase layout."},
    "D-fibonacci-control_t0":   {"C1": True,  "rationale": "Clear Fibonacci recurrence + sequence layout."},
    "D-fibonacci-control_t1":   {"C1": True,  "rationale": "Clear Fibonacci sequence with arcs showing sums."},
    "D-binary_search-control_t0": {"C1": True, "rationale": "Clear binary search across iterations on a sorted array."},
    "D-binary_search-control_t1": {"C1": True, "rationale": "Clear binary search with iteration panels."},

    # ---- LOW TIER (C1 + L1 L2 L3) -----------------------------------
    "D-factorial-low_t0":   {"C1": True, "L1": True,  "L2": True,  "L3": True,
                              "rationale": "5 boxes (PASS L1); blue+orange = 2 chrom (PASS L2); arrows (PASS L3)."},
    "D-factorial-low_t1":   {"C1": True, "L1": True,  "L2": True,  "L3": True,
                              "rationale": "4 boxes; blue+orange = 2 chrom; arrows."},
    "D-fibonacci-low_t0":   {"C1": True, "L1": False, "L2": True,  "L3": True,
                              "rationale": "7 distinct F(n) labeled terms (FAIL L1 >5); blue = 1 chrom; arcs/arrows."},
    "D-fibonacci-low_t1":   {"C1": True, "L1": False, "L2": True,  "L3": True,
                              "rationale": "7 sequence terms + equation = 8 distinct elements (FAIL L1); blue = 1; arrows."},
    "D-binary_search-low_t0": {"C1": True, "L1": True,  "L2": True,  "L3": True,
                              "rationale": "5 distinct stages/panels; blue+gray+green = ~3 chrom (borderline pass); arrows."},
    "D-binary_search-low_t1": {"C1": True, "L1": True,  "L2": True,  "L3": True,
                              "rationale": "3 iteration panels + sorted-list overview = ~4 elements; blue+orange+gray = <=3 chrom; arrows."},

    # ---- MODERATE TIER (LOW + M1 M2 M3) -----------------------------
    "D-factorial-moderate_t0": {"image_returned": False,
                                 "rationale": "Pass A FAIL: API returned no image (single failure in Run D)."},
    "D-factorial-moderate_t1": {"C1": True, "L1": True, "L2": True, "L3": True,
                                 "M1": True, "M2": True, "M3": True,
                                 "rationale": "5 boxes; blue is single accent; sans-serif; labels on every element."},
    "D-fibonacci-moderate_t0": {"C1": True, "L1": True, "L2": True, "L3": True,
                                 "M1": True, "M2": True, "M3": True,
                                 "rationale": "5 numbered compositional panels (Recurrence/Sequence/Build/Rule/Flow); blue accent + gray neutral; sans-serif; labeled."},
    "D-fibonacci-moderate_t1": {"C1": True, "L1": True, "L2": True, "L3": True,
                                 "M1": True, "M2": True, "M3": True,
                                 "rationale": "4 panels; blue single accent; sans-serif; labels."},
    "D-binary_search-moderate_t0": {"C1": True, "L1": True, "L2": True, "L3": True,
                                     "M1": True, "M2": True, "M3": True,
                                     "rationale": "5 numbered steps; blue accent + gray neutral; sans-serif; labels."},
    "D-binary_search-moderate_t1": {"C1": True, "L1": True, "L2": True, "L3": True,
                                     "M1": True, "M2": True, "M3": True,
                                     "rationale": "3 iteration panels + sorted-list header; blue accent + gray; sans-serif; labels."},

    # ---- HIGH TIER (MODERATE + H1 H2 H3 H4) -------------------------
    "D-factorial-high_t0": {"C1": True, "L1": True, "L2": True, "L3": True,
                            "M1": True, "M2": True, "M3": False,
                            "H1": True, "H2": True, "H3": True, "H4": True,
                            "rationale": "5 boxes; blue + RED highlight 'x4=24' = MULTIPLE accent colors (FAIL M3); legend + horizontal alignment + title all met."},
    "D-factorial-high_t1": {"C1": True, "L1": True, "L2": True, "L3": True,
                            "M1": True, "M2": True, "M3": False,
                            "H1": True, "H2": True, "H3": True, "H4": True,
                            "rationale": "5 boxes; blue text/box + ORANGE math labels = MULTIPLE accents (FAIL M3); legend, alignment, title all met."},
    "D-fibonacci-high_t0": {"C1": True, "L1": True, "L2": True, "L3": True,
                            "M1": True, "M2": True, "M3": True,
                            "H1": True, "H2": True, "H3": True, "H4": True,
                            "rationale": "5 boxes horizontally aligned; blue is single accent (gray is neutral); sans-serif; legend at bottom; title; consistent stroke."},
    "D-fibonacci-high_t1": {"C1": True, "L1": True, "L2": True, "L3": True,
                            "M1": True, "M2": True, "M3": True,
                            "H1": True, "H2": True, "H3": True, "H4": True,
                            "rationale": "5 boxes horizontally aligned; blue single accent; sans-serif; legend; title; consistent stroke."},
    "D-binary_search-high_t0": {"C1": True, "L1": True, "L2": True, "L3": True,
                                 "M1": True, "M2": True, "M3": False,
                                 "H1": True, "H2": True, "H3": True, "H4": True,
                                 "rationale": "5 panels; blue + RED 'Target=21' + ORANGE 'Found' = MULTIPLE accents (FAIL M3); structure otherwise met."},
    "D-binary_search-high_t1": {"C1": True, "L1": True, "L2": True, "L3": True,
                                 "M1": True, "M2": True, "M3": False,
                                 "H1": True, "H2": True, "H3": True, "H4": True,
                                 "rationale": "5 panels; blue accent + ORANGE label = multiple accents (FAIL M3); structure otherwise met."},
}


# Tier -> required demands for Pass B
TIER_DEMANDS = {
    "control":  ["C1"],
    "low":      ["C1", "L1", "L2", "L3"],
    "moderate": ["C1", "L1", "L2", "L3", "M1", "M2", "M3"],
    "high":     ["C1", "L1", "L2", "L3", "M1", "M2", "M3", "H1", "H2", "H3", "H4"],
}


def main() -> int:
    payload = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    # Build trial -> tier from cell_id (D-{task}-{tier}_t{n}.png lives in trials)
    trial_tier = {}
    for tr in payload["trials"]:
        cell = tr["cell_id"]  # e.g. "D-factorial-control"
        tier = cell.rsplit("-", 1)[1]
        trial_tier[(cell, tr["trial"])] = tier

    detailed = []
    for trial_key, scores in SCORES.items():
        # Parse "D-{task}-{tier}_t{n}"
        cell_part, tnum = trial_key.rsplit("_t", 1)
        tier = cell_part.rsplit("-", 1)[1]
        required = TIER_DEMANDS[tier]
        if scores.get("image_returned") is False:
            pass_b = False
            unsatisfied = ["(no image returned)"]
        else:
            unsatisfied = [d for d in required if not scores.get(d, False)]
            pass_b = len(unsatisfied) == 0
        detailed.append({
            "cell_id": cell_part,
            "trial": int(tnum),
            "tier": tier,
            "required_demands": required,
            "scores_by_demand": {d: scores.get(d, False) for d in required},
            "unsatisfied": unsatisfied,
            "pass_b": pass_b,
            "rationale": scores.get("rationale", ""),
        })

    # Aggregate per-tier
    by_tier = {}
    for tier in ["control", "low", "moderate", "high"]:
        rows = [d for d in detailed if d["tier"] == tier]
        n_pass = sum(1 for r in rows if r["pass_b"])
        by_tier[tier] = {
            "n_trials": len(rows),
            "n_pass_b": n_pass,
            "pass_b_rate": (n_pass / len(rows)) if rows else 0.0,
            "demands_required": TIER_DEMANDS[tier],
            "n_demands": len(TIER_DEMANDS[tier]),
        }

    out = {
        "rubric_doc": __doc__,
        "scoring_methodology": "Hand-coded by single rater, strict rubric, cumulative tiers. Borderline cases are noted in rationale; rater chose strict reading where ambiguous to maintain consistency.",
        "by_tier": by_tier,
        "by_trial": detailed,
    }
    SCORE_JSON.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {SCORE_JSON.name}")
    print()
    print("=== Per-tier Pass B (Run D) ===")
    for tier, agg in by_tier.items():
        pct = 100 * agg["pass_b_rate"]
        print(f"  {tier:10s} {agg['n_pass_b']}/{agg['n_trials']} ({pct:.0f}%)  ({agg['n_demands']} demands required)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
