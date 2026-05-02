#!/usr/bin/env python3
"""
Generate constrained decoding comparison figure for rebuttal Section 5.

Reads constrained_decoding_results.json, produces bar chart comparing
standard vs constrained decoding by tier.
"""

import json
from pathlib import Path

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_PATH = SCRIPT_DIR / "constrained_decoding_results.json"
OUTPUT_DIR = SCRIPT_DIR.parent.parent / "figures"


def main():
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = data["summary"]
    tiers = sorted(summary.keys())

    if not HAS_MATPLOTLIB:
        print("matplotlib not available")
        return

    fig, axes = plt.subplots(1, 3, figsize=(10, 2.7),
                             gridspec_kw={"width_ratios": [5, 5, 1.6]})

    x = np.arange(len(tiers))
    width = 0.35

    # pass_format comparison
    ax = axes[0]
    std_fmt = [summary[t]["standard"]["pass_format"] for t in tiers]
    con_fmt = [summary[t]["constrained"]["pass_format"] for t in tiers]
    bars1 = ax.bar(x - width / 2, std_fmt, width, label="Standard", color="#7f8c8d")
    bars2 = ax.bar(x + width / 2, con_fmt, width, label="Constrained", color="#2980b9")
    ax.set_ylabel("Pass Rate (%)")
    ax.set_title("Format Compliance (pass_format)")
    ax.set_xticks(x)
    ax.set_xticklabels([t.capitalize() for t in tiers])
    ax.set_ylim(0, 115)
    for bars in [bars1, bars2]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h + 1.5,
                    f"{h:.0f}%", ha="center", va="bottom", fontsize=9)

    # pass_both comparison
    ax = axes[1]
    std_both = [summary[t]["standard"]["pass_both"] for t in tiers]
    con_both = [summary[t]["constrained"]["pass_both"] for t in tiers]
    bars1 = ax.bar(x - width / 2, std_both, width, label="Standard", color="#7f8c8d")
    bars2 = ax.bar(x + width / 2, con_both, width, label="Constrained", color="#2980b9")
    ax.set_ylabel("Pass Rate (%)")
    ax.set_title("Joint Satisfaction (pass_both)")
    ax.set_xticks(x)
    ax.set_xticklabels([t.capitalize() for t in tiers])
    ax.set_ylim(0, 115)
    for bars in [bars1, bars2]:
        for bar in bars:
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, h + 1.5,
                    f"{h:.0f}%", ha="center", va="bottom", fontsize=9)

    n = summary[tiers[0]]["standard"]["n"]
    fig.suptitle(f"Constrained Decoding vs Standard Generation  "
                 f"(Qwen2.5-Coder-1.5B, N={int(n)} per cell)",
                 fontsize=12, fontweight="bold")

    # Shared key (no panel label; caption notes it applies to both)
    legend_ax = axes[2]
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)
    legend_ax.axis("off")
    legend_ax.add_patch(plt.Rectangle((0.05, 0.60), 0.32, 0.22,
                                       facecolor="#7f8c8d", edgecolor="black", linewidth=0.8))
    legend_ax.text(0.42, 0.71, "Standard", fontsize=15, va="center", fontweight="bold")
    legend_ax.add_patch(plt.Rectangle((0.05, 0.25), 0.32, 0.22,
                                       facecolor="#2980b9", edgecolor="black", linewidth=0.8))
    legend_ax.text(0.42, 0.36, "Constrained", fontsize=15, va="center", fontweight="bold")

    plt.tight_layout(rect=[0, 0, 1, 0.91])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ["pdf", "png"]:
        path = OUTPUT_DIR / f"constrained_decoding.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=150)
        print(f"Figure saved: {path}")
    plt.close(fig)


if __name__ == "__main__":
    main()
