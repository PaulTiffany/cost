"""
new_findings_showcase.py

Three plots showcasing the May 2026 Claude family extension:
  1. Policy-density cliff curve (mean across 9 working models, one-shot vs staged
     pass rate at each of 8 tiers; shows the staging rescue at low k and the
     staging inversion at k > ~22).
  2. Per-model cliff matrix heatmap (one_shot pass rate per model at each tier;
     highlights opus-4.1 sustained dominance).
  3. Implicit-k tagger calibration scatter (mean extracted k vs pipeline pass
     rate per model; lower extraction correlates with higher pass).

No API calls. Reads the result JSONs already in the repo. Writes PNGs into
supplementary/demos/figures/.

Usage:
    python new_findings_showcase.py
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
POLICY_PATH = REPO_ROOT / "supplementary/experiments/outputs/policy_density/policy_density_results.json"
IMPLICIT_PATH = REPO_ROOT / "supplementary/experiments/outputs/implicit_k/implicit_k_results.json"
OUT_DIR = REPO_ROOT / "supplementary/demos/figures"


def cliff_curve(policy: dict) -> Path:
    """Plot 1: mean cliff curve, one-shot vs staged across tiers."""
    cells = defaultdict(dict)
    for s in policy["summary"]:
        cells[(s["model"], s["tier"])][s["protocol"]] = s["all_pass_rate"]
        cells[(s["model"], s["tier"])]["k"] = s["k"]
    models = sorted({s["model"] for s in policy["summary"]})
    tiers = sorted({s["tier"] for s in policy["summary"]})
    ks = [cells[(models[0], t)]["k"] for t in tiers]

    one_shot_means = []
    staged_means = []
    for t in tiers:
        os_v = [cells[(m, t)].get("one_shot") for m in models if cells[(m, t)].get("one_shot") is not None]
        st_v = [cells[(m, t)].get("staged") for m in models if cells[(m, t)].get("staged") is not None]
        one_shot_means.append(100 * sum(os_v) / len(os_v))
        staged_means.append(100 * sum(st_v) / len(st_v))

    # CVD-safe palette: blue + orange (Wong / Okabe-Ito), distinct hue and
    # luminance. Marker shapes (circle vs square) and dash style (solid vs
    # dashed) give two extra non-color channels of distinction.
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.plot(ks, one_shot_means, "o-",  color="#0072B2", linewidth=2.2,
            markersize=8, label="one-shot")
    ax.plot(ks, staged_means,   "s--", color="#D55E00", linewidth=2.2,
            markersize=8, label="staged")
    ax.set_xlabel("Policy density k (cumulative atomic rules)", fontsize=11)
    ax.set_ylabel("Mean all-pass rate across 9 models (%)", fontsize=11)
    ax.set_title("Policy-density cliff curve (T1 to T8)", fontsize=12)
    ax.set_xticks(ks)
    ax.set_yticks(range(0, 101, 20))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", framealpha=0.95)

    # Annotate key transitions
    ax.annotate("staging rescue", xy=(5, 93), xytext=(7.5, 96),
                fontsize=9, color="#cc3333",
                arrowprops=dict(arrowstyle="->", color="#cc3333", lw=1))
    ax.annotate("staging inversion", xy=(22, 15), xytext=(24, 35),
                fontsize=9, color="#cc3333",
                arrowprops=dict(arrowstyle="->", color="#cc3333", lw=1))
    fig.tight_layout()
    out = OUT_DIR / "new_finding_1_policy_density_cliff_curve.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def per_model_matrix(policy: dict) -> Path:
    """Plot 2: per-model one-shot pass-rate heatmap across tiers.

    Color choices: 'cividis' is monotonic-luminance and CVD-safe, so the
    plot reads correctly in greyscale and for red/green color-blind
    viewers. Cell text is the canonical signal; color is redundant
    encoding. A unicode marker (filled vs hollow vs cross) is added on top
    of the number so readers who cannot see color at all can still
    distinguish pass / partial / fail at a glance."""
    cells = defaultdict(dict)
    for s in policy["summary"]:
        cells[(s["model"], s["tier"])][s["protocol"]] = s["all_pass_rate"]
        cells[(s["model"], s["tier"])]["k"] = s["k"]
    models = sorted({s["model"] for s in policy["summary"]})
    tiers = sorted({s["tier"] for s in policy["summary"]})
    ks = [cells[(models[0], t)]["k"] for t in tiers]

    matrix = np.zeros((len(models), len(tiers)))
    for i, m in enumerate(models):
        for j, t in enumerate(tiers):
            matrix[i, j] = (cells[(m, t)].get("one_shot") or 0) * 100

    fig, ax = plt.subplots(figsize=(9.0, 5.0))
    im = ax.imshow(matrix, cmap="cividis", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(tiers)))
    ax.set_xticklabels([f"T{t}\nk={k}" for t, k in zip(tiers, ks)], fontsize=9)
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels(models, fontsize=10)
    ax.set_title("Per-model one-shot pass rate (Policy density)", fontsize=12)

    def shape_marker(v: float) -> str:
        if v >= 90: return "●"   # filled circle
        if v >= 60: return "◑"   # half-filled
        if v >= 30: return "○"   # hollow circle
        return "×"               # cross

    for i in range(len(models)):
        for j in range(len(tiers)):
            v = matrix[i, j]
            text_color = "white" if v < 50 else "black"
            ax.text(j, i - 0.18, f"{v:.0f}", ha="center", va="center",
                    fontsize=9, color=text_color, fontweight="bold")
            ax.text(j, i + 0.22, shape_marker(v), ha="center", va="center",
                    fontsize=12, color=text_color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.025)
    cbar.set_label("pass rate (%)", fontsize=9)
    fig.text(0.5, 0.01,
             "Marker key:  ● >=90   ◑ 60-89   ○ 30-59   × <30   "
             "(redundant encoding; color is cividis, monotonic luminance, CVD-safe)",
             ha="center", fontsize=8.5, color="#444")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out = OUT_DIR / "new_finding_2_per_model_cliff_matrix.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def tagger_calibration(implicit: dict) -> Path:
    """Plot 3: implicit-k tagger calibration. Mean extracted k per model
    on the x-axis, pipeline pass rate on the y-axis, one point per model."""
    by_model = defaultdict(lambda: {"pass": 0, "n": 0, "ks": []})
    for r in implicit["results"]:
        by_model[r["model_name"]]["pass"] += int(r["all_pass"])
        by_model[r["model_name"]]["n"] += 1
        by_model[r["model_name"]]["ks"].append(r["extracted_k"])

    pts = []
    for m, v in by_model.items():
        mean_k = sum(v["ks"]) / len(v["ks"])
        pass_rate = 100 * v["pass"] / v["n"]
        pts.append((m, mean_k, pass_rate, v["pass"], v["n"]))
    pts.sort(key=lambda p: p[1])

    # CVD-safe single-hue scatter; high-contrast outline so points stand out
    # in greyscale too.
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    xs = [p[1] for p in pts]
    ys = [p[2] for p in pts]
    ax.scatter(xs, ys, s=130, color="#0072B2", edgecolors="black",
               linewidths=1.2, zorder=3)
    for m, mk, pr, p, n in pts:
        offset = (8, 6) if m != "haiku-4.5" else (-8, -16)
        ha = "left" if offset[0] > 0 else "right"
        ax.annotate(f"{m}\n({p}/{n})", xy=(mk, pr), xytext=offset, textcoords="offset points",
                    fontsize=9, ha=ha, alpha=0.9)

    # Light trend line
    if len(xs) >= 2:
        coef = np.polyfit(xs, ys, 1)
        xrange = np.linspace(min(xs) - 1, max(xs) + 2, 50)
        ax.plot(xrange, np.polyval(coef, xrange), "--", color="#888", alpha=0.6, zorder=2,
                label=f"linear fit (slope={coef[0]:.1f} pp / unit k)")

    ax.set_xlabel("Mean extracted k (tagger aggressiveness)", fontsize=11)
    ax.set_ylabel("Pipeline all-pass rate across 8 implicit prompts (%)", fontsize=11)
    ax.set_title("Implicit-k tagger calibration: lower k extraction tracks higher pass rate", fontsize=12)
    ax.set_ylim(-5, 110)
    ax.set_xlim(min(xs) - 2, max(xs) + 4)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", framealpha=0.95)
    fig.tight_layout()
    out = OUT_DIR / "new_finding_3_tagger_calibration.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    implicit = json.loads(IMPLICIT_PATH.read_text(encoding="utf-8"))

    p1 = cliff_curve(policy)
    p2 = per_model_matrix(policy)
    p3 = tagger_calibration(implicit)

    print(f"Wrote {p1.relative_to(REPO_ROOT)}")
    print(f"Wrote {p2.relative_to(REPO_ROOT)}")
    print(f"Wrote {p3.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
