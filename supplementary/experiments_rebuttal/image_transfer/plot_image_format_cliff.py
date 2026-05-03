#!/usr/bin/env python3
"""
plot_image_format_cliff.py - Build the bound-transfer cliff figure.

Overlays Run D image-medium per-tier Pass B rates with the text-medium
cross-model average. The two curves should track if the bound transfers.

Output:
  paper/figures/image_format_cliff.pdf
  paper/figures/image_format_cliff.png
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
PASSB_JSON = SCRIPT_DIR / "image_transfer_runD_passB.json"
CROSS_MODEL_JSON = REPO_ROOT / "rebuttal" / "figures" / "blinded_external" / "cross_model_results.json"
OUT_PDF = REPO_ROOT / "paper" / "figures" / "image_format_cliff.pdf"
OUT_PNG = REPO_ROOT / "paper" / "figures" / "image_format_cliff.png"

TIERS = ["control", "low", "moderate", "high"]
TIER_K = [1, 4, 7, 11]  # number of compounding demands per tier (from rubric)


def main() -> int:
    if not PASSB_JSON.exists():
        print(f"ERROR: {PASSB_JSON} not found", file=sys.stderr); return 2
    if not CROSS_MODEL_JSON.exists():
        print(f"ERROR: {CROSS_MODEL_JSON} not found", file=sys.stderr); return 2

    image_data = json.loads(PASSB_JSON.read_text(encoding="utf-8"))
    image_rates = [image_data["by_tier"][t]["pass_b_rate"] for t in TIERS]

    text_data = json.loads(CROSS_MODEL_JSON.read_text(encoding="utf-8"))
    by_model_tier = defaultdict(lambda: {"n": 0, "pass": 0})
    for r in text_data["results"]:
        by_model_tier[(r["model"], r["tier"])]["n"] += 1
        if r.get("pass_both"): by_model_tier[(r["model"], r["tier"])]["pass"] += 1
    models = sorted({r["model"] for r in text_data["results"]})

    fig, ax = plt.subplots(1, 1, figsize=(8.5, 5.0))

    # Text-medium per-model curves (light gray)
    text_colors = ["#bdc3c7", "#95a5a6", "#7f8c8d", "#34495e"]
    for i, model in enumerate(models):
        rates = []
        for tier in TIERS:
            a = by_model_tier.get((model, tier), {"n": 0, "pass": 0})
            rates.append(a["pass"] / a["n"] if a["n"] else 0)
        ax.plot(TIER_K, rates, "o--", color=text_colors[i % len(text_colors)],
                 linewidth=1.0, markersize=5, alpha=0.55, label=f"text: {model}")

    # Text-medium average across models (thicker)
    n_total = defaultdict(int); n_pass = defaultdict(int)
    for r in text_data["results"]:
        n_total[r["tier"]] += 1
        if r.get("pass_both"): n_pass[r["tier"]] += 1
    text_avg = [n_pass[t] / n_total[t] if n_total[t] else 0 for t in TIERS]
    ax.plot(TIER_K, text_avg, "s-", color="#34495e", linewidth=2.0,
             markersize=8, label="text-medium (4-model avg, N=1839)")

    # Image-medium curve (highlighted)
    ax.plot(TIER_K, image_rates, "D-", color="#e74c3c", linewidth=2.5,
             markersize=9, label="image-medium (Run D, gpt-5.4-image-2, N=24)")

    ax.set_xlabel("Compounding format demands ($k$)", fontsize=11)
    ax.set_ylabel("Pass B (joint format compliance)", fontsize=11)
    ax.set_title("Diagonal-cost cliff transfers across modalities", fontsize=12)
    ax.set_ylim(0, 1.05)
    ax.set_xticks(TIER_K)
    ax.set_xticklabels([f"$k{{=}}{k}$\n({t})" for k, t in zip(TIER_K, TIERS)], fontsize=10)
    ax.legend(fontsize=8, loc="lower left")
    ax.grid(True, alpha=0.25, linestyle=":")

    plt.tight_layout()
    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PDF, bbox_inches="tight")
    fig.savefig(OUT_PNG, bbox_inches="tight", dpi=140)
    print(f"wrote {OUT_PDF.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_PNG.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
