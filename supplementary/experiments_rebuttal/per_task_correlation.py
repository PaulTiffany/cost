#!/usr/bin/env python3
"""
Per-task rank correlation: finer-grained ρ̂ vs pass rate analysis.

Addresses reviewer concern: "r_s=1.0 is over only 4 bins (4 data points)."

Computes:
1. Per-task pass rate at each tier (12 tasks × 4 tiers = 48 data points)
2. Spearman rank correlation between tier-level ρ̂ and per-task pass rate
3. Per-task monotonicity check (does pass rate decrease with ρ̂?)
4. Bootstrap CIs on per-tier pass rates

Output: table + figure for rebuttal.
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np

try:
    from scipy.stats import spearmanr, bootstrap
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = REPO_ROOT / "supplementary" / "experiments" / "code_constraint_results.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "figures"

# Tier → approximate ρ̂ from constraint analysis (same as proxy_ablation.py)
TIER_RHO = {
    "control": 0.05,
    "low": 0.20,
    "moderate": 0.40,
    "high": 0.65,
}

TIER_ORDER = ["control", "low", "moderate", "high"]


def load_data():
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    trials = []
    for model_key, model_data in data.items():
        if model_key == "_meta":
            continue
        for r in model_data["results"]:
            trials.append({
                "model": model_key,
                "task_id": r["task_id"],
                "tier": r["tier"],
                "protocol": r["protocol"],
                "pass_both": r["pass_both"],
            })
    return trials


def compute_per_task_rates(trials):
    """Compute pass rate per (task, tier), aggregating across models and protocols."""
    counts = defaultdict(lambda: {"pass": 0, "total": 0})
    for t in trials:
        key = (t["task_id"], t["tier"])
        counts[key]["total"] += 1
        if t["pass_both"]:
            counts[key]["pass"] += 1

    results = {}
    for (task_id, tier), c in counts.items():
        results[(task_id, tier)] = c["pass"] / c["total"] if c["total"] > 0 else 0.0
    return results


def compute_per_tier_rates(trials):
    """Compute aggregate pass rate per tier with bootstrap CI."""
    tier_outcomes = defaultdict(list)
    for t in trials:
        tier_outcomes[t["tier"]].append(1.0 if t["pass_both"] else 0.0)

    results = {}
    for tier in TIER_ORDER:
        outcomes = np.array(tier_outcomes[tier])
        mean = outcomes.mean()
        # Bootstrap 95% CI
        n_boot = 10000
        rng = np.random.RandomState(42)
        boot_means = [rng.choice(outcomes, size=len(outcomes), replace=True).mean()
                      for _ in range(n_boot)]
        ci_lo = np.percentile(boot_means, 2.5)
        ci_hi = np.percentile(boot_means, 97.5)
        results[tier] = {
            "mean": mean,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "n": len(outcomes),
        }
    return results


def main():
    trials = load_data()
    tasks = sorted(set(t["task_id"] for t in trials))
    n_models = len(set(t["model"] for t in trials))

    print("=" * 70)
    print("PER-TASK RANK CORRELATION ANALYSIS")
    print(f"Tasks: {len(tasks)}, Models: {n_models}, Total trials: {len(trials)}")
    print("=" * 70)

    # 1. Per-task pass rates
    per_task = compute_per_task_rates(trials)

    print(f"\n{'Task':<20}", end="")
    for tier in TIER_ORDER:
        print(f"  {tier:>10} (rho={TIER_RHO[tier]:.2f})", end="")
    print("  Monotone?")
    print("-" * 100)

    monotone_count = 0
    all_rhos = []
    all_rates = []

    for task in tasks:
        rates = [per_task.get((task, tier), 0.0) for tier in TIER_ORDER]
        # Check monotonicity: pass rate should decrease with tier
        is_monotone = all(rates[i] >= rates[i + 1] for i in range(len(rates) - 1))
        monotone_count += int(is_monotone)

        print(f"{task:<20}", end="")
        for rate in rates:
            print(f"  {rate:>10.1%}          ", end="")
        print(f"  {'Y' if is_monotone else 'N'}")

        for tier, rate in zip(TIER_ORDER, rates):
            all_rhos.append(TIER_RHO[tier])
            all_rates.append(rate)

    print(f"\nMonotone tasks: {monotone_count}/{len(tasks)}")

    # 2. Spearman correlation across all 48 (task, tier) points
    if HAS_SCIPY:
        rs_all, p_all = spearmanr(all_rhos, all_rates)
        print(f"\nSpearman (48 task×tier points): r_s = {rs_all:.3f}, p = {p_all:.2e}")

        # Also per-tier aggregate
        tier_means = [np.mean([per_task.get((task, tier), 0.0) for task in tasks])
                      for tier in TIER_ORDER]
        tier_rhos = [TIER_RHO[tier] for tier in TIER_ORDER]
        rs_agg, p_agg = spearmanr(tier_rhos, tier_means)
        print(f"Spearman (4 tier aggregates):   r_s = {rs_agg:.3f}, p = {p_agg:.4f}")

    # 3. Per-tier rates with bootstrap CIs
    tier_rates = compute_per_tier_rates(trials)
    print(f"\n{'Tier':<12} {'rho':>6} {'Pass%':>8} {'95% CI':>16} {'N':>6}")
    print("-" * 55)
    for tier in TIER_ORDER:
        r = tier_rates[tier]
        print(f"{tier:<12} {TIER_RHO[tier]:>6.2f} {r['mean']:>8.1%} "
              f"[{r['ci_lo']:.1%}, {r['ci_hi']:.1%}]{r['n']:>6}")

    # Check non-overlapping CIs
    print("\nCI overlap check:")
    for i in range(len(TIER_ORDER) - 1):
        t1, t2 = TIER_ORDER[i], TIER_ORDER[i + 1]
        r1, r2 = tier_rates[t1], tier_rates[t2]
        overlap = r1["ci_lo"] < r2["ci_hi"] and r2["ci_lo"] < r1["ci_hi"]
        print(f"  {t1} vs {t2}: {'OVERLAPPING' if overlap else 'NON-OVERLAPPING'}")

    # 4. Plot
    if HAS_MATPLOTLIB:
        fig, axes = plt.subplots(1, 2, figsize=(13, 5))

        # Left: per-task heatmap
        ax = axes[0]
        rate_matrix = np.array([[per_task.get((task, tier), 0.0)
                                 for tier in TIER_ORDER] for task in tasks])
        im = ax.imshow(rate_matrix, cmap="YlOrBr_r", vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(TIER_ORDER)))
        ax.set_xticklabels([f"{t}\n(ρ̂={TIER_RHO[t]:.2f})" for t in TIER_ORDER],
                           fontsize=8)
        ax.set_yticks(range(len(tasks)))
        ax.set_yticklabels(tasks, fontsize=8)
        ax.set_title(f"Per-Task Pass Rate by Tier\n({monotone_count}/{len(tasks)} monotone)")
        for i in range(len(tasks)):
            for j in range(len(TIER_ORDER)):
                val = rate_matrix[i, j]
                color = "white" if val < 0.5 else "black"
                ax.text(j, i, f"{val:.0%}", ha="center", va="center",
                        fontsize=8, color=color)
                # Dual encoding: intuitive border (green/orange/red) over accessible fill
                border = "#81C784" if val >= 0.6 else "#FFB74D" if val >= 0.3 else "#E57373"
                ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                             fill=False, edgecolor=border, linewidth=1.2))
        plt.colorbar(im, ax=ax, label="Pass Rate", shrink=0.8)

        # Right: aggregate with CIs
        ax = axes[1]
        rhos = [TIER_RHO[t] for t in TIER_ORDER]
        means = [tier_rates[t]["mean"] for t in TIER_ORDER]
        ci_lo = [tier_rates[t]["ci_lo"] for t in TIER_ORDER]
        ci_hi = [tier_rates[t]["ci_hi"] for t in TIER_ORDER]
        yerr_lo = [m - lo for m, lo in zip(means, ci_lo)]
        yerr_hi = [hi - m for m, hi in zip(means, ci_hi)]

        ax.errorbar(rhos, means, yerr=[yerr_lo, yerr_hi],
                    fmt="o-", color="#2c3e50", capsize=5, linewidth=2, markersize=8)
        ax.set_xlabel("Regime Index ρ̂")
        ax.set_ylabel("Pass Rate")
        ax.set_title(f"Aggregate Pass Rate with 95% Bootstrap CI\n"
                     f"r_s = {rs_all:.3f} (48 pts), p = {p_all:.2e}" if HAS_SCIPY else "")
        ax.set_xlim(-0.05, 0.75)
        ax.set_ylim(0, 1.05)
        ax.grid(True, alpha=0.3)

        # Shade non-overlapping CIs
        for i, tier in enumerate(TIER_ORDER):
            ax.fill_between([rhos[i] - 0.02, rhos[i] + 0.02],
                            ci_lo[i], ci_hi[i], alpha=0.2, color="#3498db")

        plt.tight_layout()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for ext in ["pdf", "png"]:
            path = OUTPUT_DIR / f"per_task_correlation.{ext}"
            fig.savefig(path, bbox_inches="tight", dpi=150)
            print(f"\nFigure saved: {path}")
        plt.close(fig)

    # Save JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_out = {
        "n_tasks": len(tasks),
        "n_models": n_models,
        "n_trials": len(trials),
        "monotone_tasks": monotone_count,
        "spearman_48pt": {"r_s": float(rs_all), "p": float(p_all)} if HAS_SCIPY else None,
        "spearman_4tier": {"r_s": float(rs_agg), "p": float(p_agg)} if HAS_SCIPY else None,
        "per_tier": {tier: tier_rates[tier] for tier in TIER_ORDER},
        "per_task": {task: {tier: per_task.get((task, tier), 0.0)
                            for tier in TIER_ORDER} for task in tasks},
    }
    json_path = OUTPUT_DIR / "per_task_correlation_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2)
    print(f"Results saved: {json_path}")


if __name__ == "__main__":
    main()
