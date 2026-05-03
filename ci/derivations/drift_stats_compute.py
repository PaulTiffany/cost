#!/usr/bin/env python3
"""
drift_stats_compute.py

Parses direction-drift and step-size statistics out of paper/main.tex into
a JSON manifest for L15 grounding. These values come from the Direction
Stability Diagnostic (App app:direction_stability) and the Step-Size
Distribution paragraph in the L_hat calibration appendix.

Source statements:
  - "Median direction drift: 0.07 (IQR: 0.03--0.12). 94% of trajectories
     show drift <0.15." (line 1107)
  - "95th percentile ||x_{t+1} - x_t|| = 0.041; 99th percentile = 0.067;
     maximum observed = 0.12." (line 1168)
  - "High-drift outliers (6%) correspond to completions where the model
     'pivots' mid-generation" (line 1110)

These are paper-tabulated experimental statistics. Derivation makes the
paper text the single source of truth so claims I54, I55, C18, C19 stay
tied to it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
OUTPUT = SCRIPT_DIR / "drift_stats.json"


def extract_direction_drift(tex: str) -> dict:
    # "Median direction drift: $0.07$ (IQR: $0.03$--$0.12$). 94\% of trajectories show drift ${<}0.15$"
    m = re.search(
        r"Median direction drift:\s*\$?([\d.]+)\$?\s*\(IQR:\s*\$?([\d.]+)\$?--\$?([\d.]+)\$?\)\.\s*"
        r"(\d+)\\?%\s*of trajectories show drift\s*\$?\{?<\}?([\d.]+)\$?",
        tex,
    )
    if not m:
        raise RuntimeError("could not locate direction-drift statistics in paper/main.tex")
    return {
        "median": float(m.group(1)),
        "iqr_low": float(m.group(2)),
        "iqr_high": float(m.group(3)),
        "pct_below_threshold": int(m.group(4)),
        "threshold": float(m.group(5)),
    }


def extract_high_drift_pct(tex: str) -> int:
    # "High-drift outliers (6\%) correspond to completions where the model ``pivots''"
    m = re.search(r"High-drift outliers \((\d+)\\?%\)", tex)
    if not m:
        raise RuntimeError("could not locate high-drift outlier percentage")
    return int(m.group(1))


def extract_step_size(tex: str) -> dict:
    # "95th percentile $\|x_{t+1} - x_t\| = 0.041$; 99th percentile $= 0.067$; maximum observed $= 0.12$"
    m = re.search(
        r"95th percentile[^=]*=\s*([\d.]+)\$?;\s*99th percentile[^=]*=\s*([\d.]+)\$?;"
        r"\s*maximum observed[^=]*=\s*([\d.]+)",
        tex,
    )
    if not m:
        raise RuntimeError("could not locate step-size percentile statistics")
    return {
        "p95": float(m.group(1)),
        "p99": float(m.group(2)),
        "max_observed": float(m.group(3)),
    }


def main() -> None:
    tex = PAPER_TEX.read_text(encoding="utf-8")
    payload = {
        "_meta": {
            "description": (
                "Direction-drift and step-size statistics parsed from paper/main.tex "
                "App app:direction_stability (line ~1107) and step-size paragraph "
                "(line ~1168). Backs claims I54 (drift median/IQR), I55 (95th step), "
                "C18 (94% drift<0.15), C19 (6% high-drift)."
            ),
            "source": "paper/main.tex direction stability and step-size paragraphs",
        },
        "direction_drift": extract_direction_drift(tex),
        "high_drift_pct": extract_high_drift_pct(tex),
        "step_size": extract_step_size(tex),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
