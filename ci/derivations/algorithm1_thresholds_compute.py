#!/usr/bin/env python3
"""
algorithm1_thresholds_compute.py

Derives the canonical Algorithm 1 routing-threshold constants used throughout
the paper into a single JSON manifest that L15 can tie paper claims against.

These constants are config-source: they live as numeric literals in:
  - paper/main.tex  (Algorithm 1 pseudocode, App app:transfer, App tab:hyperparams)
  - supplementary/experiments_rebuttal/unconditional_pivot_analysis.py
    (pivot detection rule: step > 2.5*L_hat or drift > 15 deg)
  - paper Table tab:lipschitz_calibration (default L_hat ~ 0.025)

We extract them from the paper's pre-registered statement at App:transfer
(line 824 of main.tex):
    "We pre-registered routing thresholds (rho_stage = 0.15, rho_fail = 0.5)"
and from the pivot script's documented detection rule.

The output JSON is the single source of truth that L15 ties paper claims
against. If anyone edits the paper without updating this script (or vice
versa), L15 will detect the drift.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
PIVOT_SCRIPT = REPO_ROOT / "supplementary" / "experiments_rebuttal" / "unconditional_pivot_analysis.py"
OUTPUT = SCRIPT_DIR / "algorithm1_thresholds.json"


def extract_pre_registered_thresholds(tex: str) -> tuple[float, float]:
    """Pull rho_stage and rho_fail from the App:transfer pre-registration line."""
    pattern = (
        r"\\hat\{\\rho\}_\{\\text\{stage\}\}\s*=\s*([\d.]+).*?"
        r"\\hat\{\\rho\}_\{\\text\{fail\}\}\s*=\s*([\d.]+)"
    )
    m = re.search(pattern, tex, re.DOTALL)
    if not m:
        raise RuntimeError("could not locate pre-registered thresholds in paper/main.tex")
    return float(m.group(1)), float(m.group(2))


def extract_pivot_detection_rule(src: str) -> tuple[float, float]:
    """Pull pivot step multiplier and drift threshold from the pivot script docstring."""
    # Docstring line: "step > 2.5L̂ or drift > 15°"
    m_step = re.search(r"step\s*>\s*([\d.]+)\s*L", src)
    m_drift = re.search(r"drift\s*>\s*([\d.]+)\s*[°˚]", src)
    if not m_step or not m_drift:
        raise RuntimeError("could not locate pivot detection rule in unconditional_pivot_analysis.py")
    return float(m_step.group(1)), float(m_drift.group(1))


def extract_default_l_hat(tex: str) -> float:
    """Pull default L_hat from Lipschitz calibration table caption."""
    # "Default $\hat{L} \approx 0.025$"
    m = re.search(r"[Dd]efault\s+\$?\\hat\{L\}\$?\s*\\?approx\s*([\d.]+)", tex)
    if not m:
        raise RuntimeError("could not locate default L_hat in paper/main.tex")
    return float(m.group(1))


def main() -> None:
    tex = PAPER_TEX.read_text(encoding="utf-8")
    pivot_src = PIVOT_SCRIPT.read_text(encoding="utf-8")

    rho_stage, rho_fail = extract_pre_registered_thresholds(tex)
    pivot_step_mult, drift_deg = extract_pivot_detection_rule(pivot_src)
    default_l_hat = extract_default_l_hat(tex)

    payload = {
        "_meta": {
            "description": (
                "Algorithm 1 routing-threshold constants, derived from canonical "
                "sources (paper/main.tex App:transfer, unconditional_pivot_analysis.py, "
                "Table tab:lipschitz_calibration). Single source of truth for L15."
            ),
            "sources": {
                "rho_hat_stage": "paper/main.tex line 824 (App:transfer pre-registration)",
                "rho_hat_fail": "paper/main.tex line 824 (App:transfer pre-registration)",
                "pivot_step_mult": "supplementary/experiments_rebuttal/unconditional_pivot_analysis.py docstring line 10",
                "drift_threshold_deg": "supplementary/experiments_rebuttal/unconditional_pivot_analysis.py docstring line 10",
                "default_L_hat": "paper/main.tex Table tab:lipschitz_calibration caption",
            },
        },
        "rho_hat_stage": rho_stage,
        "rho_hat_fail": rho_fail,
        "pivot_step_mult": pivot_step_mult,
        "drift_threshold_deg": drift_deg,
        "default_L_hat": default_l_hat,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    for k, v in payload.items():
        if k != "_meta":
            print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
