#!/usr/bin/env python3
"""
Plot graded satisfaction curves and heatmaps for ICML 2026.

Generates:
1. Dual-curve plot: partial satisfaction + full success rate vs budget
2. Failure heatmap: per-constraint failure rates by budget and protocol
3. Distribution plot: histogram of satisfaction scores

Usage:
  python plot_graded_satisfaction.py --input results.json --output figures/

Author: Anonymous (ICML 2026 Submission)
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

# Try to import matplotlib, graceful fallback
try:
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def load_profiles(path: str) -> list:
    """Load satisfaction profiles from JSON."""
    with open(path) as f:
        data = json.load(f)
    return data.get("profiles", data.get("results", []))


def aggregate_by_budget_protocol(profiles: list) -> dict:
    """Group profiles by (budget, protocol) and compute aggregates."""
    groups = defaultdict(list)

    for p in profiles:
        key = (p["budget"], p["protocol"])
        groups[key].append(p)

    aggregates = {}
    for (budget, protocol), group in groups.items():
        fractions = [p["fraction_satisfied"] for p in group]
        scores = [p["mean_score"] for p in group]
        full_success = [1.0 if p["full_satisfaction"] else 0.0 for p in group]

        # Per-constraint failure rates
        constraint_failures = defaultdict(list)
        for p in group:
            for c in p.get("constraints", []):
                constraint_failures[c["name"]].append(0.0 if c["satisfied"] else 1.0)

        aggregates[(budget, protocol)] = {
            "budget": budget,
            "protocol": protocol,
            "n_trials": len(group),
            "mean_fraction": np.mean(fractions),
            "std_fraction": np.std(fractions),
            "mean_score": np.mean(scores),
            "std_score": np.std(scores),
            "full_success_rate": np.mean(full_success),
            "std_full_success": np.std(full_success),
            "constraint_failure_rates": {k: np.mean(v) for k, v in constraint_failures.items()},
        }

    return aggregates


def plot_satisfaction_curves(aggregates: dict, output_dir: Path):
    """Plot partial satisfaction and full success rate vs budget."""
    if not HAS_MPL:
        print("matplotlib not available, skipping plots")
        return

    budgets = sorted(set(k[0] for k in aggregates.keys()))
    protocols = sorted(set(k[1] for k in aggregates.keys()))

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))

    colors = {"oneshot": "#E74C3C", "staged": "#3498DB"}
    styles = {"oneshot": "--", "staged": "-"}
    labels = {"oneshot": "One-shot", "staged": "Staged"}

    for protocol in protocols:
        color = colors.get(protocol, "gray")
        style = styles.get(protocol, "-")
        label = labels.get(protocol, protocol)

        budget_data = [(b, aggregates[(b, protocol)]) for b in budgets if (b, protocol) in aggregates]
        if not budget_data:
            continue

        bs, data = zip(*budget_data)

        # Left plot: Mean fraction satisfied
        fracs = [d["mean_fraction"] for d in data]
        frac_err = [d["std_fraction"] / np.sqrt(d["n_trials"]) for d in data]

        axes[0].errorbar(bs, fracs, yerr=frac_err, color=color, linestyle=style,
                         marker='o', markersize=4, label=label, capsize=3)

        # Right plot: Full success rate
        fulls = [d["full_success_rate"] for d in data]
        full_err = [d["std_full_success"] / np.sqrt(d["n_trials"]) for d in data]

        axes[1].errorbar(bs, fulls, yerr=full_err, color=color, linestyle=style,
                         marker='o', markersize=4, label=label, capsize=3)

    axes[0].set_xlabel("Token Budget (δ)", fontsize=11)
    axes[0].set_ylabel("Mean Fraction Satisfied", fontsize=11)
    axes[0].set_ylim(0, 1.05)
    axes[0].legend(loc="lower right")
    axes[0].set_title("Partial Satisfaction", fontsize=12)
    axes[0].grid(True, alpha=0.3)

    axes[1].set_xlabel("Token Budget (δ)", fontsize=11)
    axes[1].set_ylabel("Full Success Rate", fontsize=11)
    axes[1].set_ylim(0, 1.05)
    axes[1].legend(loc="lower right")
    axes[1].set_title("Complete Constraint Intersection", fontsize=12)
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "graded_satisfaction.pdf", bbox_inches='tight')
    plt.savefig(output_dir / "graded_satisfaction.png", dpi=150, bbox_inches='tight')
    print(f"Saved: {output_dir}/graded_satisfaction.pdf")


def plot_failure_heatmap(aggregates: dict, output_dir: Path):
    """Plot per-constraint failure rates as heatmap."""
    if not HAS_MPL:
        return

    budgets = sorted(set(k[0] for k in aggregates.keys()))
    protocols = ["oneshot", "staged"]

    # Get all constraint names
    all_constraints = set()
    for agg in aggregates.values():
        all_constraints.update(agg["constraint_failure_rates"].keys())
    constraints = sorted(all_constraints)

    if not constraints:
        print("No constraint data for heatmap")
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, max(3, len(constraints) * 0.4)))

    for idx, protocol in enumerate(protocols):
        ax = axes[idx]

        # Build matrix
        matrix = np.zeros((len(constraints), len(budgets)))
        for i, c in enumerate(constraints):
            for j, b in enumerate(budgets):
                if (b, protocol) in aggregates:
                    rate = aggregates[(b, protocol)]["constraint_failure_rates"].get(c, 0)
                    matrix[i, j] = rate

        im = ax.imshow(matrix, cmap="Reds", vmin=0, vmax=1, aspect="auto")

        ax.set_xticks(range(len(budgets)))
        ax.set_xticklabels(budgets)
        ax.set_yticks(range(len(constraints)))
        ax.set_yticklabels(constraints, fontsize=9)

        ax.set_xlabel("Token Budget (δ)")
        ax.set_title(f"{'One-shot' if protocol == 'oneshot' else 'Staged'} Failure Rates")

        # Add text annotations
        for i in range(len(constraints)):
            for j in range(len(budgets)):
                val = matrix[i, j]
                color = "white" if val > 0.5 else "black"
                ax.text(j, i, f"{val:.0%}", ha="center", va="center", color=color, fontsize=8)

    fig.colorbar(im, ax=axes, shrink=0.6, label="Failure Rate")
    plt.tight_layout()
    plt.savefig(output_dir / "failure_heatmap.pdf", bbox_inches='tight')
    plt.savefig(output_dir / "failure_heatmap.png", dpi=150, bbox_inches='tight')
    print(f"Saved: {output_dir}/failure_heatmap.pdf")


def plot_satisfaction_distribution(profiles: list, output_dir: Path):
    """Plot distribution of satisfaction scores."""
    if not HAS_MPL:
        return

    # Group by protocol
    oneshot = [p["fraction_satisfied"] for p in profiles if p["protocol"] == "oneshot"]
    staged = [p["fraction_satisfied"] for p in profiles if p["protocol"] == "staged"]

    if not oneshot and not staged:
        return

    fig, ax = plt.subplots(figsize=(8, 4))

    bins = np.linspace(0, 1, 21)

    if oneshot:
        ax.hist(oneshot, bins=bins, alpha=0.6, color="#E74C3C", label="One-shot", density=True)
    if staged:
        ax.hist(staged, bins=bins, alpha=0.6, color="#3498DB", label="Staged", density=True)

    ax.set_xlabel("Fraction of Constraints Satisfied")
    ax.set_ylabel("Density")
    ax.set_title("Distribution of Constraint Satisfaction")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_dir / "satisfaction_distribution.pdf", bbox_inches='tight')
    print(f"Saved: {output_dir}/satisfaction_distribution.pdf")


def print_ascii_summary(aggregates: dict):
    """Print ASCII summary table."""
    print()
    print("=" * 70)
    print("  GRADED SATISFACTION SUMMARY")
    print("=" * 70)
    print()
    print(f"  {'Budget':<8} {'Protocol':<10} {'Frac Sat':<12} {'Full Success':<12}")
    print("  " + "-" * 50)

    for (budget, protocol) in sorted(aggregates.keys()):
        agg = aggregates[(budget, protocol)]
        frac = f"{agg['mean_fraction']:.1%}"
        full = f"{agg['full_success_rate']:.1%}"
        print(f"  {budget:<8} {protocol:<10} {frac:<12} {full:<12}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Plot graded satisfaction metrics")
    parser.add_argument("--input", "-i", required=True, help="Input JSON with profiles")
    parser.add_argument("--output", "-o", default="figures", help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    profiles = load_profiles(args.input)
    if not profiles:
        sys.exit("No profiles found in input file")

    aggregates = aggregate_by_budget_protocol(profiles)

    print_ascii_summary(aggregates)

    if HAS_MPL:
        plot_satisfaction_curves(aggregates, output_dir)
        plot_failure_heatmap(aggregates, output_dir)
        plot_satisfaction_distribution(profiles, output_dir)
    else:
        print("Install matplotlib for plots: pip install matplotlib")


if __name__ == "__main__":
    main()
