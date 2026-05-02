#!/usr/bin/env python3
"""
Proxy Ablation: Random rho-hat vs Real rho-hat vs Oracle rho routing comparison.

Uses REAL data from:
  - supplementary/experiments/outputs/constitution/constitution_rho_matrix.npy (20x20)
  - supplementary/experiments/outputs/constitution/constitution_analysis.json
  - supplementary/experiments/outputs/conflict_detection/probes.json (190 pairs)
  - supplementary/experiments/code_constraint_results.json (4 models, 1920 trials)

Scale: ~2,110 real routing decisions (190 constitution + 1,920 code constraint).

Demonstrates that routing quality degrades gracefully with proxy noise,
confirming ordinal sufficiency (Lemma lem:proxy_sufficiency).

Conditions:
1. Oracle: route using ground-truth outcomes
2. Real rho-hat: route using embedding cosine / tier-level rho
3. Shuffled rho-hat: permute rho-hat values
4. Random rho-hat: uniform random proxy values
"""

import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

import numpy as np

try:
    from scipy.stats import spearmanr, kendalltau
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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Load real data
# ---------------------------------------------------------------------------

def load_constitution_data() -> Tuple[np.ndarray, dict, list]:
    """Load constitution rho matrix + conflict detection probes."""
    rho_path = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "constitution" / "constitution_rho_matrix.npy"
    analysis_path = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "constitution" / "constitution_analysis.json"
    probes_path = REPO_ROOT / "supplementary" / "experiments" / "outputs" / "conflict_detection" / "probes.json"

    rho_matrix = np.load(rho_path)
    with open(analysis_path, "r", encoding="utf-8") as f:
        analysis = json.load(f)
    with open(probes_path, "r", encoding="utf-8") as f:
        probes_data = json.load(f)

    return rho_matrix, analysis, probes_data["probes"]


def load_code_constraint_results() -> list:
    """Load all 1,920 code constraint trial results."""
    path = REPO_ROOT / "supplementary" / "experiments" / "code_constraint_results.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_trials = []
    for model_key, model_data in data.items():
        if model_key == "_meta":
            continue
        for r in model_data["results"]:
            all_trials.append({
                "id": f"{model_key}|{r['task_id']}|{r['tier']}|{r['protocol']}|t{r['trial']}",
                "model": model_key,
                "task_id": r["task_id"],
                "tier": r["tier"],
                "protocol": r["protocol"],
                "pass_both": r["pass_both"],
            })

    return all_trials


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------

@dataclass
class RoutingResult:
    condition: str
    accuracy: float
    regret: float
    spearman: float
    n_items: int
    description: str


def compute_metrics(
    decisions: List[str],       # "oneshot" or "staged"
    oracle_decisions: List[str],
    outcomes: List[bool],       # True = oneshot would have succeeded
    staging_cost: float = 0.05,
) -> Tuple[float, float]:
    """Accuracy and regret vs oracle."""
    n = len(decisions)
    correct = sum(1 for d, o in zip(decisions, oracle_decisions) if d == o)
    regret = 0.0
    for d, o, success in zip(decisions, oracle_decisions, outcomes):
        if d == o:
            pass  # correct decision
        elif d == "staged" and o == "oneshot":
            regret += staging_cost  # over-staging
        else:
            regret += 1.0  # under-staging: paid the failure
    return correct / n, regret / n


# ---------------------------------------------------------------------------
# Ablation on constitution conflict detection (190 real pairs)
# ---------------------------------------------------------------------------

def run_constitution_ablation(
    rho_matrix: np.ndarray,
    probes: list,
    n_random: int = 200,
) -> List[RoutingResult]:
    """190 real principle pairs with behavioral ground truth."""
    rng = np.random.RandomState(42)

    # Extract per-pair data
    pair_rhos = []      # structural rho-hat (embedding cosine)
    pair_oracle = []    # True if conflict detected behaviorally
    for p in probes:
        pair_rhos.append(p["structural_rho"])
        pair_oracle.append(bool(p.get("conflict_detected", False)))

    n = len(probes)
    # Oracle: stage if conflict detected
    oracle_decisions = ["staged" if c else "oneshot" for c in pair_oracle]
    outcomes = [not c for c in pair_oracle]  # oneshot succeeds if no conflict

    oracle_vals = [1.0 if c else 0.0 for c in pair_oracle]
    results = []

    # 1. Oracle
    acc, reg = compute_metrics(oracle_decisions, oracle_decisions, outcomes)
    results.append(RoutingResult("Oracle", acc, reg, 1.0, n,
                                 "Behavioral conflict detection"))

    # 2. Real rho-hat (structural_rho from embeddings)
    threshold = 0.30  # route to staged if rho > threshold
    real_decisions = ["staged" if r > threshold else "oneshot" for r in pair_rhos]
    acc, reg = compute_metrics(real_decisions, oracle_decisions, outcomes)
    rs = spearmanr(oracle_vals, pair_rhos)[0] if HAS_SCIPY else 0.0
    results.append(RoutingResult("Real rho-hat", acc, reg, rs, n,
                                 "Embedding cosine similarity"))

    # 3. Shuffled rho-hat
    shuffled = list(pair_rhos)
    rng.shuffle(shuffled)
    shuf_decisions = ["staged" if r > threshold else "oneshot" for r in shuffled]
    acc, reg = compute_metrics(shuf_decisions, oracle_decisions, outcomes)
    rs_s = spearmanr(oracle_vals, shuffled)[0] if HAS_SCIPY else 0.0
    results.append(RoutingResult("Shuffled rho-hat", acc, reg, rs_s, n,
                                 "Permuted embedding values"))

    # 4. Random rho-hat (averaged over trials)
    rand_accs, rand_regs, rand_rs = [], [], []
    for _ in range(n_random):
        rand_vals = rng.uniform(0, 1, n).tolist()
        rand_dec = ["staged" if r > threshold else "oneshot" for r in rand_vals]
        a, r = compute_metrics(rand_dec, oracle_decisions, outcomes)
        rand_accs.append(a)
        rand_regs.append(r)
        if HAS_SCIPY:
            rand_rs.append(spearmanr(oracle_vals, rand_vals)[0])

    results.append(RoutingResult(
        "Random rho-hat",
        float(np.mean(rand_accs)), float(np.mean(rand_regs)),
        float(np.mean(rand_rs)) if rand_rs else 0.0, n,
        "Uniform random proxy",
    ))

    return results


# ---------------------------------------------------------------------------
# Ablation on code constraint trials (1,920 real trials)
# ---------------------------------------------------------------------------

# Tier -> approximate rho from constraint analysis.
# control = ~0 constraints, low = 1, moderate = 3, high = 5.
TIER_RHO = {
    "control": 0.05,
    "low": 0.15,
    "moderate": 0.35,
    "high": 0.55,
}


def run_code_constraint_ablation(
    trials: list,
    n_random: int = 200,
) -> List[RoutingResult]:
    """1,920 real trial-level routing decisions."""
    rng = np.random.RandomState(42)
    n = len(trials)

    # Oracle: stage if trial would have failed one-shot
    # We approximate: for staged protocol trials, use their outcome directly;
    # for one-shot trials, use their outcome directly.
    # Route decision: "staged" if one-shot is predicted to fail.
    oracle_decisions = ["oneshot" if t["pass_both"] else "staged" for t in trials]
    outcomes = [t["pass_both"] for t in trials]

    oracle_vals = [1.0 if not t["pass_both"] else 0.0 for t in trials]
    results = []

    # 1. Oracle
    acc, reg = compute_metrics(oracle_decisions, oracle_decisions, outcomes)
    results.append(RoutingResult("Oracle", acc, reg, 1.0, n,
                                 "Ground-truth per-trial outcome"))

    # 2. Real rho-hat (tier-level proxy)
    threshold = 0.30
    real_rhos = [TIER_RHO[t["tier"]] for t in trials]
    real_decisions = ["staged" if r > threshold else "oneshot" for r in real_rhos]
    acc, reg = compute_metrics(real_decisions, oracle_decisions, outcomes)
    rs = spearmanr(oracle_vals, real_rhos)[0] if HAS_SCIPY else 0.0
    results.append(RoutingResult("Real rho-hat", acc, reg, rs, n,
                                 "Tier-level embedding proxy"))

    # 3. Shuffled rho-hat
    shuffled = list(real_rhos)
    rng.shuffle(shuffled)
    shuf_decisions = ["staged" if r > threshold else "oneshot" for r in shuffled]
    acc, reg = compute_metrics(shuf_decisions, oracle_decisions, outcomes)
    rs_s = spearmanr(oracle_vals, shuffled)[0] if HAS_SCIPY else 0.0
    results.append(RoutingResult("Shuffled rho-hat", acc, reg, rs_s, n,
                                 "Permuted tier rho values"))

    # 4. Random rho-hat
    rand_accs, rand_regs, rand_rs = [], [], []
    for _ in range(n_random):
        rand_vals = rng.uniform(0, 1, n).tolist()
        rand_dec = ["staged" if r > threshold else "oneshot" for r in rand_vals]
        a, r = compute_metrics(rand_dec, oracle_decisions, outcomes)
        rand_accs.append(a)
        rand_regs.append(r)
        if HAS_SCIPY:
            rand_rs.append(spearmanr(oracle_vals, rand_vals)[0])

    results.append(RoutingResult(
        "Random rho-hat",
        float(np.mean(rand_accs)), float(np.mean(rand_regs)),
        float(np.mean(rand_rs)) if rand_rs else 0.0, n,
        "Uniform random proxy",
    ))

    return results


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_combined(const_results: List[RoutingResult],
                  cc_results: List[RoutingResult],
                  output_dir: Path):
    if not HAS_MATPLOTLIB:
        print("matplotlib not available, skipping plots")
        return

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    colors = ["#2ecc71", "#3498db", "#e67e22", "#e74c3c"]

    datasets = [
        (const_results, "Constitution Pairs (n=190, behavioral oracle)"),
        (cc_results, "Code Constraint Trials (n=1,920, 4 models)"),
    ]

    for row, (results, title) in enumerate(datasets):
        conditions = [r.condition for r in results]
        accuracies = [r.accuracy for r in results]
        regrets = [r.regret for r in results]

        ax1 = axes[row, 0]
        bars = ax1.bar(conditions, accuracies, color=colors)
        ax1.set_ylabel("Routing Accuracy")
        ax1.set_title(f"{title}\nAccuracy")
        ax1.set_ylim(0, 1.08)
        ax1.axhline(y=0.5, color="gray", linestyle="--", alpha=0.4, label="chance")
        for i, v in enumerate(accuracies):
            ax1.text(i, v + 0.02, f"{v:.1%}", ha="center", fontsize=9, fontweight="bold")

        ax2 = axes[row, 1]
        ax2.bar(conditions, regrets, color=colors)
        ax2.set_ylabel("Mean Routing Regret")
        ax2.set_title(f"{title}\nRegret")
        max_reg = max(regrets) if max(regrets) > 0 else 0.1
        ax2.set_ylim(0, max_reg * 1.5)
        for i, v in enumerate(regrets):
            ax2.text(i, v + max_reg * 0.04, f"{v:.3f}", ha="center", fontsize=9)

    total_n = const_results[0].n_items + cc_results[0].n_items
    fig.suptitle(f"Proxy Ablation: {total_n:,} Real Routing Decisions from Paper Data",
                 fontsize=14, y=1.02, fontweight="bold")
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ["pdf", "png"]:
        path = output_dir / f"proxy_ablation.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=150)
        print(f"Figure saved: {path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_table(results: List[RoutingResult], label: str):
    print(f"\n{label}")
    print(f"{'Condition':<18} {'Accuracy':>10} {'Regret':>10} {'Spearman':>10} {'N':>8}")
    print("-" * 60)
    for r in results:
        print(f"{r.condition:<18} {r.accuracy:>10.1%} {r.regret:>10.4f} "
              f"{r.spearman:>10.3f} {r.n_items:>8,}")


def main():
    parser = argparse.ArgumentParser(description="Proxy ablation with real experiment data")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent.parent / "figures")
    parser.add_argument("--n-random", type=int, default=200)
    args = parser.parse_args()

    print("=" * 65)
    print("PROXY ABLATION: Real Data from Paper Experiments")
    print("=" * 65)

    # Load all real data
    rho_matrix, analysis, probes = load_constitution_data()
    trials = load_code_constraint_results()

    total = len(probes) + len(trials)
    print(f"\nData loaded:")
    print(f"  Constitution conflict probes:  {len(probes):>6,} pairs")
    print(f"  Code constraint trials:        {len(trials):>6,} trials")
    print(f"  Total routing decisions:        {total:>6,}")

    # Run ablations
    const_results = run_constitution_ablation(rho_matrix, probes, args.n_random)
    print_table(const_results, "CONSTITUTION ABLATION (190 pairs, behavioral oracle)")

    cc_results = run_code_constraint_ablation(trials, args.n_random)
    print_table(cc_results, "CODE CONSTRAINT ABLATION (1,920 trials, 4 models)")

    # Combined summary
    print(f"\n{'='*65}")
    print(f"COMBINED: {total:,} real routing decisions")
    print(f"{'='*65}")
    for cond_idx in range(4):
        c_const = const_results[cond_idx]
        c_cc = cc_results[cond_idx]
        # Weighted average
        w1, w2 = c_const.n_items, c_cc.n_items
        combined_acc = (c_const.accuracy * w1 + c_cc.accuracy * w2) / (w1 + w2)
        combined_reg = (c_const.regret * w1 + c_cc.regret * w2) / (w1 + w2)
        print(f"  {c_const.condition:<18} acc={combined_acc:.1%}  regret={combined_reg:.4f}")

    # Save
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "proxy_ablation_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_routing_decisions": total,
            "constitution": [asdict(r) for r in const_results],
            "code_constraints": [asdict(r) for r in cc_results],
        }, f, indent=2)
    print(f"\nResults saved: {json_path}")

    plot_combined(const_results, cc_results, args.output_dir)


if __name__ == "__main__":
    main()
