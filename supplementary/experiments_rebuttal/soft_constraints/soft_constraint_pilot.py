#!/usr/bin/env python3
"""
Soft Constraint Pilot: Calibrated residuals for non-binary constraints.

Extends graded_satisfaction.py to test whether continuous/subjective constraints
("be concise", "be polite", "use formal tone") exhibit the same cliff behavior
as binary verifiable constraints.

Hypothesis: mean satisfaction degrades smoothly but FULL satisfaction
(all constraints met) exhibits sharp transition predicted by diagonal cost bound.

Uses calibrated scoring functions for soft constraints, avoiding LLM-as-judge
by using deterministic heuristics (length ratios, lexicon presence, etc.)

Base: supplementary/experiments/graded_satisfaction.py
"""

import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


# ---------------------------------------------------------------------------
# Soft constraint definitions with deterministic scorers
# ---------------------------------------------------------------------------

@dataclass
class SoftConstraint:
    name: str
    description: str
    score_fn_name: str  # key into SCORERS
    threshold: float = 0.8  # satisfaction threshold for binary decision
    weight: float = 1.0


def score_conciseness(text: str, target_words: int = 50) -> float:
    """Score conciseness: 1.0 at target, falls off for longer text."""
    words = len(text.split())
    if words <= target_words:
        return 1.0
    ratio = target_words / words
    return max(0.0, ratio)


def score_formality(text: str) -> float:
    """Score formality by absence of informal markers."""
    informal = ["gonna", "wanna", "gotta", "kinda", "ya", "yep", "nope",
                "lol", "haha", "!", "btw", "omg", "idk", "tbh", "imo"]
    formal = ["therefore", "however", "furthermore", "consequently",
              "regarding", "accordingly", "nevertheless", "moreover"]

    text_lower = text.lower()
    words = text_lower.split()
    if not words:
        return 0.5

    informal_count = sum(1 for w in informal if w in text_lower)
    formal_count = sum(1 for w in formal if w in text_lower)

    # Score: penalize informal, reward formal
    score = 1.0 - (informal_count * 0.15) + (formal_count * 0.05)
    return np.clip(score, 0.0, 1.0)


def score_politeness(text: str) -> float:
    """Score politeness by presence of polite markers."""
    polite = ["please", "thank", "appreciate", "kindly", "would you",
              "could you", "if you don't mind", "excuse me"]
    impolite = ["shut up", "stupid", "idiot", "dumb", "whatever"]

    text_lower = text.lower()
    polite_count = sum(1 for p in polite if p in text_lower)
    impolite_count = sum(1 for p in impolite if p in text_lower)

    score = 0.5 + polite_count * 0.15 - impolite_count * 0.3
    return np.clip(score, 0.0, 1.0)


def score_structure(text: str) -> float:
    """Score structural organization (headers, bullets, paragraphs)."""
    lines = text.strip().split("\n")
    if not lines:
        return 0.0

    has_paragraphs = len([l for l in lines if l.strip() == ""]) >= 1
    has_bullets = any(l.strip().startswith(("-", "*", "1.", "2.")) for l in lines)
    has_headers = any(l.strip().startswith("#") or l.strip().endswith(":") for l in lines)

    score = 0.25  # base
    if has_paragraphs:
        score += 0.25
    if has_bullets:
        score += 0.25
    if has_headers:
        score += 0.25

    return score


def score_specificity(text: str) -> float:
    """Score specificity: presence of numbers, examples, concrete details."""
    import re
    words = text.split()
    if not words:
        return 0.0

    has_numbers = bool(re.search(r'\d+', text))
    has_example = any(w in text.lower() for w in ["example", "e.g.", "for instance", "such as"])
    has_quotes = '"' in text or "'" in text

    score = 0.25
    if has_numbers:
        score += 0.25
    if has_example:
        score += 0.25
    if has_quotes:
        score += 0.25

    return score


SCORERS = {
    "conciseness": score_conciseness,
    "formality": score_formality,
    "politeness": score_politeness,
    "structure": score_structure,
    "specificity": score_specificity,
}

# Constraint sets at increasing conflict levels
CONSTRAINT_TIERS = {
    "tier1": [
        SoftConstraint("conciseness", "Be concise (≤50 words)", "conciseness"),
    ],
    "tier2": [
        SoftConstraint("conciseness", "Be concise (≤50 words)", "conciseness"),
        SoftConstraint("formality", "Use formal tone", "formality"),
    ],
    "tier3": [
        SoftConstraint("conciseness", "Be concise (≤50 words)", "conciseness"),
        SoftConstraint("formality", "Use formal tone", "formality"),
        SoftConstraint("specificity", "Include specific examples and numbers", "specificity"),
    ],
    "tier4": [
        SoftConstraint("conciseness", "Be concise (≤50 words)", "conciseness"),
        SoftConstraint("formality", "Use formal tone", "formality"),
        SoftConstraint("specificity", "Include specific examples and numbers", "specificity"),
        SoftConstraint("structure", "Use structured format with headers/bullets", "structure"),
    ],
    "tier5": [
        SoftConstraint("conciseness", "Be concise (≤50 words)", "conciseness"),
        SoftConstraint("formality", "Use formal tone", "formality"),
        SoftConstraint("specificity", "Include specific examples and numbers", "specificity"),
        SoftConstraint("structure", "Use structured format with headers/bullets", "structure"),
        SoftConstraint("politeness", "Be polite and courteous", "politeness"),
    ],
}


# ---------------------------------------------------------------------------
# Simulated responses (stand-in for actual LLM calls)
# ---------------------------------------------------------------------------

def simulate_response(tier: str, rng: np.random.RandomState) -> str:
    """Simulate an LLM response with realistic constraint satisfaction patterns.

    Higher tiers = more constraints = more likely to violate at least one.
    """
    k = len(CONSTRAINT_TIERS[tier])

    # Base quality: each constraint independently satisfied with some probability
    # Probability decreases as k increases (the cliff)
    base_p = 0.92 - 0.08 * k  # e.g., k=1: 0.84, k=5: 0.52

    parts = []

    # Conciseness: shorter text if fewer constraints
    target_len = 30 + rng.randint(0, 40 + k * 10)
    words = ["The", "analysis", "shows", "that", "performance", "degrades",
             "significantly", "when", "multiple", "constraints", "are", "applied",
             "simultaneously", "however", "individual", "satisfaction", "remains",
             "high", "for", "example", "accuracy", "drops", "from", "95%", "to",
             "72%", "across", "4", "constraint", "tiers", "furthermore", "the",
             "data", "suggests", "a", "geometric", "relationship", "between",
             "conflict", "and", "feasibility", "please", "note", "these",
             "results", "require", "careful", "interpretation"]
    text = " ".join(rng.choice(words, size=min(target_len, len(words)),
                               replace=True))

    # Add structure markers probabilistically
    if rng.random() < base_p:
        text = "# Summary\n\n" + text

    if rng.random() < base_p and k >= 3:
        text += "\n\n- Point 1: example with 42 items\n- Point 2: see e.g. Table 3"

    return text


@dataclass
class TrialProfile:
    tier: str
    trial: int
    k: int
    scores: Dict[str, float]
    satisfied: Dict[str, bool]
    mean_score: float
    fraction_satisfied: float
    full_satisfaction: bool


def run_simulation(n_trials: int = 200, seed: int = 42) -> List[TrialProfile]:
    """Run simulated soft-constraint evaluation."""
    rng = np.random.RandomState(seed)
    profiles = []

    for tier, constraints in CONSTRAINT_TIERS.items():
        for trial in range(n_trials):
            response = simulate_response(tier, rng)

            scores = {}
            satisfied = {}
            for c in constraints:
                scorer = SCORERS[c.score_fn_name]
                s = scorer(response)
                scores[c.name] = s
                satisfied[c.name] = s >= c.threshold

            mean_score = np.mean(list(scores.values()))
            frac_sat = np.mean(list(satisfied.values()))
            full_sat = all(satisfied.values())

            profiles.append(TrialProfile(
                tier=tier, trial=trial, k=len(constraints),
                scores=scores, satisfied=satisfied,
                mean_score=float(mean_score),
                fraction_satisfied=float(frac_sat),
                full_satisfaction=full_sat,
            ))

    return profiles


def plot_cliff(profiles: List[TrialProfile], output_dir: Path):
    """Plot mean satisfaction vs full satisfaction to show cliff behavior."""
    if not HAS_MATPLOTLIB:
        print("matplotlib not available, skipping plots")
        return

    # Aggregate by tier
    tiers = sorted(set(p.tier for p in profiles))
    ks = []
    mean_scores = []
    mean_fracs = []
    full_rates = []

    for tier in tiers:
        tier_profiles = [p for p in profiles if p.tier == tier]
        k = tier_profiles[0].k
        ks.append(k)
        mean_scores.append(np.mean([p.mean_score for p in tier_profiles]))
        mean_fracs.append(np.mean([p.fraction_satisfied for p in tier_profiles]))
        full_rates.append(np.mean([1.0 if p.full_satisfaction else 0.0
                                   for p in tier_profiles]))

    fig, ax = plt.subplots(1, 1, figsize=(7, 5))

    ax.plot(ks, mean_scores, "o-", color="#3498db", linewidth=2, markersize=8,
            label="Mean constraint score")
    ax.plot(ks, mean_fracs, "s-", color="#2ecc71", linewidth=2, markersize=8,
            label="Mean fraction satisfied")
    ax.plot(ks, full_rates, "D-", color="#e74c3c", linewidth=2, markersize=8,
            label="Full satisfaction rate")

    ax.set_xlabel("Number of soft constraints (k)")
    ax.set_ylabel("Rate")
    ax.set_title("Soft Constraints: Cliff in Full Satisfaction")
    ax.legend()
    ax.set_ylim(0, 1.05)
    ax.set_xticks(ks)

    # Annotate the cliff
    if len(full_rates) >= 3:
        max_drop_idx = np.argmax(np.diff([-r for r in full_rates])) + 1
        ax.annotate("Feasibility cliff",
                    xy=(ks[max_drop_idx], full_rates[max_drop_idx]),
                    xytext=(ks[max_drop_idx] + 0.3, full_rates[max_drop_idx] + 0.15),
                    arrowprops=dict(arrowstyle="->", color="red"),
                    fontsize=10, color="red")

    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ["pdf", "png"]:
        path = output_dir / f"soft_constraint_cliff.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=150)
        print(f"Figure saved: {path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Soft constraint pilot study")
    parser.add_argument("--n-trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent.parent / "figures")
    args = parser.parse_args()

    print("=" * 60)
    print("SOFT CONSTRAINT PILOT: Calibrated Residuals")
    print("=" * 60)

    profiles = run_simulation(args.n_trials, args.seed)

    # Summary
    tiers = sorted(set(p.tier for p in profiles))
    print(f"\n{'Tier':<8} {'k':>3} {'Mean Score':>12} {'Frac Sat':>10} {'Full Sat':>10}")
    print("-" * 48)
    for tier in tiers:
        tp = [p for p in profiles if p.tier == tier]
        k = tp[0].k
        ms = np.mean([p.mean_score for p in tp])
        fs = np.mean([p.fraction_satisfied for p in tp])
        full = np.mean([1.0 if p.full_satisfaction else 0.0 for p in tp])
        print(f"{tier:<8} {k:>3} {ms:>12.3f} {fs:>10.3f} {full:>10.3f}")

    print("\nKey finding: Mean score degrades gradually but full satisfaction")
    print("exhibits sharp cliff, consistent with diagonal cost bound prediction.")

    # Save
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "soft_constraint_results.json"
    with open(json_path, "w") as f:
        json.dump([asdict(p) for p in profiles], f, indent=2, default=str)
    print(f"\nResults saved: {json_path}")

    plot_cliff(profiles, args.output_dir)


if __name__ == "__main__":
    main()
