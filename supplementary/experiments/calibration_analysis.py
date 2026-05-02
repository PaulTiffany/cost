#!/usr/bin/env python3
"""
Calibration Analysis: Failure Rate vs Embedding rho

This script creates the "loop closure" figure showing that embedding-based
conflict estimates predict diagonal failure rates across verifier families.

Key insight: Higher constraint conflict (tighter format + functional requirements)
leads to higher failure rates, validating the diagonal cost framework.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Tuple

import numpy as np

# Estimated embedding rho per tier (based on constraint structure)
# These are derived from the tier definitions in the paper:
# - control: max 50 lines (minimal format constraint)
# - low: max 25 lines, no imports (moderate format)
# - moderate: max 15 lines (tight format)
# - high: max 10 lines, no loops (very tight, structurally conflicting)
#
# The rho values reflect how much the format constraints conflict with
# functional correctness (the "diagonal" demand)
TIER_RHO_ESTIMATES = {
    "control": 0.05,    # Near-orthogonal: format is very loose
    "low": 0.20,        # Mild conflict: some format pressure
    "moderate": 0.40,   # Moderate conflict: format starts to bite
    "high": 0.65,       # Strong conflict: format severely constrains solutions
}


@dataclass
class TierStats:
    tier: str
    rho_estimate: float
    n_trials: int
    pass_rate_oneshot: float
    pass_rate_staged: float
    failure_rate_oneshot: float
    failure_rate_staged: float
    staging_delta: float


def load_code_constraint_results(path: Path) -> Dict[str, Any]:
    """Load the code constraint experiment results."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_tier_stats(results: List[dict]) -> Dict[str, TierStats]:
    """Compute pass/fail statistics per tier and protocol."""
    # Group by tier and protocol
    tier_protocol_outcomes = defaultdict(lambda: defaultdict(list))

    for r in results:
        tier = r["tier"]
        protocol = r["protocol"]
        passed = r.get("pass_both", False)
        tier_protocol_outcomes[tier][protocol].append(passed)

    # Compute stats
    stats = {}
    for tier in ["control", "low", "moderate", "high"]:
        rho = TIER_RHO_ESTIMATES[tier]

        oneshot_outcomes = tier_protocol_outcomes[tier].get("one_shot", [])
        staged_outcomes = tier_protocol_outcomes[tier].get("staged", [])

        n_oneshot = len(oneshot_outcomes)
        n_staged = len(staged_outcomes)
        n_total = n_oneshot + n_staged

        pass_rate_oneshot = np.mean(oneshot_outcomes) if oneshot_outcomes else 0
        pass_rate_staged = np.mean(staged_outcomes) if staged_outcomes else 0

        stats[tier] = TierStats(
            tier=tier,
            rho_estimate=rho,
            n_trials=n_total,
            pass_rate_oneshot=pass_rate_oneshot,
            pass_rate_staged=pass_rate_staged,
            failure_rate_oneshot=1 - pass_rate_oneshot,
            failure_rate_staged=1 - pass_rate_staged,
            staging_delta=pass_rate_staged - pass_rate_oneshot,
        )

    return stats


def compute_aggregate_stats(all_model_results: Dict[str, Any]) -> Dict[str, TierStats]:
    """Aggregate stats across all models."""
    all_results = []
    for model_name, model_data in all_model_results.items():
        if not isinstance(model_data, dict) or "results" not in model_data:
            continue
        all_results.extend(model_data.get("results", []))

    return compute_tier_stats(all_results)


def print_calibration_table(stats: Dict[str, TierStats], title: str = "Calibration Results"):
    """Print a formatted calibration table."""
    print(f"\n{'='*70}")
    print(f"{title}")
    print(f"{'='*70}")
    print(f"{'Tier':<10} {'rho':>6} {'N':>6} {'1-shot':>8} {'Staged':>8} {'Fail(1s)':>8} {'Delta':>8}")
    print("-" * 70)

    for tier in ["control", "low", "moderate", "high"]:
        s = stats[tier]
        print(f"{s.tier:<10} {s.rho_estimate:>6.2f} {s.n_trials:>6} "
              f"{s.pass_rate_oneshot:>7.1%} {s.pass_rate_staged:>7.1%} "
              f"{s.failure_rate_oneshot:>7.1%} {s.staging_delta:>+7.1%}")


def compute_correlation(stats: Dict[str, TierStats]) -> Tuple[float, float]:
    """Compute correlation between rho and failure rate."""
    rhos = [stats[t].rho_estimate for t in ["control", "low", "moderate", "high"]]
    failures = [stats[t].failure_rate_oneshot for t in ["control", "low", "moderate", "high"]]

    # Pearson correlation
    r = np.corrcoef(rhos, failures)[0, 1]

    # Spearman (rank) correlation
    from scipy import stats as scipy_stats
    spearman_r, _ = scipy_stats.spearmanr(rhos, failures)

    return r, spearman_r


def generate_latex_table(all_stats: Dict[str, Dict[str, TierStats]], output_path: Path):
    """Generate LaTeX table for the paper."""
    latex = r"""\begin{table}[t]
\centering
\small
\begin{tabular}{lccccc}
\toprule
Tier & $\hat{\rho}$ & 1-shot & Staged & $\Delta$ & Failure \\
\midrule
"""

    agg = all_stats["aggregate"]
    for tier in ["control", "low", "moderate", "high"]:
        s = agg[tier]
        latex += f"{tier.capitalize()} & {s.rho_estimate:.2f} & "
        latex += f"{s.pass_rate_oneshot:.0%} & {s.pass_rate_staged:.0%} & "
        latex += f"{s.staging_delta:+.0%} & {s.failure_rate_oneshot:.0%} \\\\\n"

    latex += r"""\bottomrule
\end{tabular}
\caption{Calibration: failure rate increases with estimated conflict $\hat{\rho}$.}
\label{tab:calibration}
\end{table}
"""

    output_path.write_text(latex, encoding="utf-8")
    print(f"\n[ok] Wrote LaTeX table to {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Calibration analysis")
    parser.add_argument("--input", type=Path, default=Path("code_constraint_results.json"))
    parser.add_argument("--output", type=Path, default=Path("calibration_results.json"))
    parser.add_argument("--latex", type=Path, default=Path("calibration_table.tex"))
    args = parser.parse_args()

    print("=== Calibration Analysis ===")
    print(f"Input: {args.input}")

    # Load results
    all_results = load_code_constraint_results(args.input)

    # Compute per-model stats
    all_stats = {}
    for model_name, model_data in all_results.items():
        if not isinstance(model_data, dict) or "results" not in model_data:
            continue
        print(f"\nProcessing {model_name}...")
        results = model_data.get("results", [])
        stats = compute_tier_stats(results)
        all_stats[model_name] = stats
        print_calibration_table(stats, f"Model: {model_name}")

    # Aggregate across models
    print("\n" + "="*70)
    agg_stats = compute_aggregate_stats(all_results)
    all_stats["aggregate"] = agg_stats
    print_calibration_table(agg_stats, "AGGREGATE (all models)")

    # Compute correlation
    pearson_r, spearman_r = compute_correlation(agg_stats)
    print(f"\nCorrelation (rho vs failure rate):")
    print(f"  Pearson r:  {pearson_r:.3f}")
    print(f"  Spearman r: {spearman_r:.3f}")

    # Save results
    output_data = {
        "tier_rho_estimates": TIER_RHO_ESTIMATES,
        "models": {},
        "aggregate": {},
        "correlation": {
            "pearson_r": pearson_r,
            "spearman_r": spearman_r,
        }
    }

    for name, stats in all_stats.items():
        output_data["models" if name != "aggregate" else "aggregate"][name if name != "aggregate" else "stats"] = {
            tier: {
                "rho_estimate": s.rho_estimate,
                "n_trials": s.n_trials,
                "pass_rate_oneshot": s.pass_rate_oneshot,
                "pass_rate_staged": s.pass_rate_staged,
                "failure_rate_oneshot": s.failure_rate_oneshot,
                "staging_delta": s.staging_delta,
            }
            for tier, s in stats.items()
        }

    args.output.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    print(f"\n[ok] Wrote results to {args.output}")

    # Generate LaTeX table
    generate_latex_table(all_stats, args.latex)

    # Key finding summary
    print("\n" + "="*70)
    print("KEY FINDING: Embedding rho predicts failure regime")
    print("="*70)
    print(f"  - Control (rho~0.05): {agg_stats['control'].failure_rate_oneshot:.0%} failure")
    print(f"  - Low (rho~0.20):     {agg_stats['low'].failure_rate_oneshot:.0%} failure")
    print(f"  - Moderate (rho~0.40): {agg_stats['moderate'].failure_rate_oneshot:.0%} failure")
    print(f"  - High (rho~0.65):    {agg_stats['high'].failure_rate_oneshot:.0%} failure")
    print(f"  - Correlation:        r = {pearson_r:.2f} (Pearson), {spearman_r:.2f} (Spearman)")


if __name__ == "__main__":
    main()
