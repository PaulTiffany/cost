#!/usr/bin/env python3
"""
bridge_baselines_compute.py
===========================
Derivation: extract verifiable structural facts from the Bytebeat and IF-DSL
baseline.json files (the only bridge artifacts checked into the repo).

The per-tier pass rates in Table A18 (Bytebeat 59% at rho=0.47) and Table A17
(IF-DSL 0% at rho=1.0) come from eval-pipeline JSONLs that are NOT tracked in
the repo. This script grounds what IS tracked: constraint counts, baseline
sample sizes, marginals, and the explicit high-rho pair values that the paper
quotes as conflict anchors.

Outputs: ci/derivations/bridge_baselines_derived.json
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BB = REPO_ROOT / "supplementary/experiments/outputs/bytebeat_baseline.json/baseline.json"
IF = REPO_ROOT / "supplementary/experiments/outputs/if_dsl_baseline.json/baseline.json"
OUT = Path(__file__).resolve().parent / "bridge_baselines_derived.json"


def main() -> int:
    bb = json.loads(BB.read_text(encoding="utf-8"))
    iff = json.loads(IF.read_text(encoding="utf-8"))

    # Bytebeat: pitch_A4 has marginal 0.0 in the baseline grammar;
    # any pair that includes pitch_A4 therefore yields rho=1.0 (max conflict).
    bb_pitch_marginal = bb["marginals"]["pitch_A4"]
    bb_pitch_max_rho_pair = bb["rho"]["pitch_A4"]["high_roughness"]  # 1.0

    # IF-DSL: paper explicitly anchors high-rho regime at
    #   one_bottleneck + pathlen_eq_5  ->  rho = 1.0
    # (Line 1511 of main.tex.)
    if_high_pair_rho = iff["rho"]["one_bottleneck"]["pathlen_eq_5"]

    derived = {
        "_provenance": {
            "script": "ci/derivations/bridge_baselines_compute.py",
            "sources": [
                "supplementary/experiments/outputs/bytebeat_baseline.json/baseline.json",
                "supplementary/experiments/outputs/if_dsl_baseline.json/baseline.json",
            ],
            "note": (
                "Extracts structural facts from baseline.json files for the "
                "bytebeat and if_dsl bridges. Per-tier pass rates from the "
                "eval pipeline (Table A17/A18) are NOT in the repo; this "
                "derivation grounds what IS tracked."
            ),
        },
        "bytebeat": {
            "n_constraints": len(bb["constraints"]),
            "baseline_valid_samples": bb["valid_samples"],
            "pitch_A4_marginal": bb_pitch_marginal,
            "pitch_A4_pair_max_rho": bb_pitch_max_rho_pair,
            "constraints": bb["constraints"],
        },
        "if_dsl": {
            "n_constraints": len(iff["constraints"]),
            "baseline_valid_samples": iff["valid_samples"],
            "baseline_n_samples_param": iff["baseline_params"]["samples"],
            "high_rho_pair": ["one_bottleneck", "pathlen_eq_5"],
            "high_rho_value": if_high_pair_rho,
        },
    }

    OUT.write_text(json.dumps(derived, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
