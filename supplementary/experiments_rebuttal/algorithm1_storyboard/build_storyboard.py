#!/usr/bin/env python3
"""
Render a 4-panel Algorithm 1 storyboard for rebuttal Figure R5.

Panels:  Prompt  ->  Embed  ->  Budget  ->  Route

Design principle: the cosine *matrix* from v1 is replaced with two
vectors and a visible angle arc, showing the geometric connection
between constraint text and conflict measurement.  Color (blue/orange)
is the only visual thread; everything else is neutral gray.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    # Force Type-1 fonts for ICML compliance (no Type-3)
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
except ImportError as exc:
    raise SystemExit(
        "matplotlib is required to render the storyboard.  "
        "Install it or run in an environment that already includes it."
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = Path(__file__).with_name("storyboard_spec.json")
REBUTTAL_FIG_DIR = REPO_ROOT / "rebuttal" / "figures"
CAMERA_READY_FIG_DIR = REPO_ROOT / "submission_repo" / "figures"

# Palette: blue + orange + neutral grays only
C_TEXT = "#314453"
C_MUTED = "#6A7B8D"
C_LIGHT = "#D2DBE5"
C_BG = "#FFFFFF"
C_EDGE = "#C4D0DC"
C_TITLE = "#14222D"


def load_spec(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# -- Frame helpers -----------------------------------------------------------

def add_panel(ax, x, y, w, h, title, subtitle=None):
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.006,rounding_size=0.02",
        linewidth=1.2, edgecolor=C_EDGE, facecolor=C_BG,
        transform=ax.transAxes,
    )
    ax.add_patch(box)
    ax.text(
        x + w / 2, y + h - 0.04, title,
        transform=ax.transAxes, fontsize=12, fontweight="bold",
        color=C_TITLE, va="top", ha="center",
    )
    if subtitle:
        ax.text(
            x + w / 2, y + h - 0.10, subtitle,
            transform=ax.transAxes, fontsize=8.5, color=C_MUTED,
            va="top", ha="center", fontstyle="italic",
        )


def add_flow_arrow(ax, x1, y1, x2, y2):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle="-|>", mutation_scale=18, linewidth=1.8,
        color=C_MUTED, transform=ax.transAxes,
    ))


# -- Panel 1: Prompt --------------------------------------------------------

def draw_prompt(ax, x, y, w, h, spec):
    c = spec["constraints"]
    px = x + 0.02
    top = y + h - 0.18
    gap = 0.088
    lines = [
        ('"Write an assistant',  C_TEXT,        "normal", None),
        ("response that is",     C_TEXT,        "normal", None),
        (c[0]["text"],           c[0]["color"], "bold",   c[0]),
        ("and",                  C_TEXT,        "normal", None),
        (c[1]["text"] + '."',   c[1]["color"], "bold",   c[1]),
    ]
    badge_x = x + w - 0.02
    for i, (text, color, weight, ci) in enumerate(lines):
        ax.text(
            px, top - i * gap, text,
            transform=ax.transAxes, fontsize=10.5, color=color,
            fontweight=weight, ha="left", va="top",
        )
        # C1/C2 badge next to constraint words (makes parse step visible)
        if ci:
            ax.text(
                badge_x, top - i * gap, ci["label"],
                transform=ax.transAxes, fontsize=9, color=ci["color"],
                fontweight="bold", ha="right", va="top",
                bbox=dict(
                    boxstyle="round,pad=0.12", facecolor=C_BG,
                    edgecolor=ci["color"], linewidth=0.8,
                ),
            )


# -- Panel 2: Embed (hero) --------------------------------------------------

def draw_embed(ax, x, y, w, h, spec):
    """Two vectors from a shared origin with an angle arc."""
    c = spec["constraints"]
    rho = spec["rho_hat"]
    rc = spec["route_color"]

    fig_w, fig_h = ax.figure.get_size_inches()
    aspect = fig_w / fig_h

    theta = math.acos(rho)
    half = theta / 2.0

    # Origin at bottom-center of content area
    ox = x + w * 0.50
    oy = y + h * 0.25
    vlen = h * 0.38

    # Equation at top
    ax.text(
        ox, y + h - 0.14,
        r"$\hat{\rho}_{ij}"
        r" = \cos(\Phi(C_i),\,\Phi(C_j))$",
        transform=ax.transAxes, fontsize=10.5, color=C_TEXT,
        ha="center", va="top",
    )

    # Vector angles (symmetric about vertical)
    blue_ang = math.pi / 2 + half
    orange_ang = math.pi / 2 - half

    # Draw vectors
    for ang, ci in [(blue_ang, c[0]), (orange_ang, c[1])]:
        dx = math.cos(ang) * vlen / aspect
        dy = math.sin(ang) * vlen
        ax.add_patch(FancyArrowPatch(
            (ox, oy), (ox + dx, oy + dy),
            arrowstyle="-|>", mutation_scale=16, linewidth=2.8,
            color=ci["color"], transform=ax.transAxes,
        ))
        # Label at tip
        ha = "right" if dx < 0 else "left"
        nudge_x = -0.008 if dx < 0 else 0.008
        ax.text(
            ox + dx + nudge_x, oy + dy + 0.015,
            r"$\Phi($" + ci["label"] + r"$)$",
            transform=ax.transAxes, fontsize=10, color=ci["color"],
            fontweight="bold", ha=ha, va="bottom",
        )

    # Angle arc (polyline for aspect-ratio safety)
    arc_r = vlen * 0.30
    angs = np.linspace(orange_ang, blue_ang, 50)
    ax.plot(
        ox + np.cos(angs) * arc_r / aspect,
        oy + np.sin(angs) * arc_r,
        transform=ax.transAxes, color=C_MUTED, linewidth=1.5,
    )

    # Theta label inside arc
    ax.text(
        ox, oy + arc_r * 0.50, r"$\theta$",
        transform=ax.transAxes, fontsize=13, color=C_MUTED,
        ha="center", va="center", fontstyle="italic",
    )

    # Origin dot
    ax.scatter(
        [ox], [oy], transform=ax.transAxes,
        s=30, color=C_TEXT, zorder=5,
    )

    # Result line
    ax.text(
        ox, oy - 0.06,
        r"$\hat{\rho} = \cos\theta = " + f"{rho:.2f}$",
        transform=ax.transAxes, fontsize=11.5, color=rc,
        ha="center", va="top", fontweight="bold",
    )
    ax.text(
        ox, oy - 0.12,
        "conflict proxy",
        transform=ax.transAxes, fontsize=8.5, color=C_MUTED,
        ha="center", va="top", fontstyle="italic",
    )


# -- Panel 3: Budget --------------------------------------------------------

def draw_budget(ax, x, y, w, h, spec):
    """Vertical gauge: delta / delta_min with colored routing zones."""
    ratio = spec["budget_ratio"]
    rc = spec["route_color"]

    # Ratio annotation (clear of gauge, below subtitle)
    ax.text(
        x + w * 0.50, y + h - 0.18,
        r"ratio $= \delta\,/\,\delta_{\min}$",
        transform=ax.transAxes, fontsize=9, color=C_MUTED,
        ha="center", va="top", fontstyle="italic",
    )

    bar_x = x + w * 0.38
    bar_w = w * 0.24
    bar_bot = y + h * 0.18
    bar_top = y + h * 0.68
    bar_h = bar_top - bar_bot
    vmax = 2.6

    def yp(v):
        return bar_bot + bar_h * (v / vmax)

    # Zone fills: familiar border (red/yellow/green) + accessible fill (gray/amber/blue)
    for v0, v1, fill, border in [
        (0.0, 1.2, "#E5E7EB", "#E57373"),   # gray fill, red border
        (1.2, 2.0, "#FEF3C7", "#FFB74D"),   # amber fill, orange border
        (2.0, vmax, "#DBEAFE", "#81C784"),   # blue fill, green border
    ]:
        ax.add_patch(Rectangle(
            (bar_x, yp(v0)), bar_w, yp(v1) - yp(v0),
            transform=ax.transAxes, facecolor=fill,
            edgecolor=border, linewidth=1.8,
        ))

    # Outer outline
    ax.add_patch(Rectangle(
        (bar_x, bar_bot), bar_w, bar_h,
        transform=ax.transAxes, facecolor="none",
        edgecolor=C_LIGHT, linewidth=0.8,
    ))

    # Threshold dashes
    for val in (1.2, 2.0):
        yy = yp(val)
        ax.plot(
            [bar_x - 0.005, bar_x + bar_w + 0.005], [yy, yy],
            transform=ax.transAxes, color=C_MUTED,
            linewidth=0.8, linestyle="--",
        )
        ax.text(
            bar_x + bar_w + 0.015, yy, f"{val:g}",
            transform=ax.transAxes, fontsize=8, color=C_LIGHT,
            ha="left", va="center",
        )

    # Marker dot
    my = yp(ratio)
    ax.scatter(
        [bar_x + bar_w / 2], [my], transform=ax.transAxes,
        s=80, color=rc, edgecolors="white", linewidths=1.5, zorder=5,
    )

    # Ratio label
    ax.text(
        bar_x - 0.015, my,
        r"$\frac{\delta}{\delta_{\min}}\!=\!" + f"{ratio:.1f}$",
        transform=ax.transAxes, fontsize=10, color=rc,
        ha="right", va="center", fontweight="bold",
    )

    # Zone labels (uniform — selection happens in Route panel)
    for zy, text in [
        (yp(2.3),  "One-Shot"),
        (yp(1.6),  "Staged"),
        (yp(0.6),  "Decompose"),
    ]:
        ax.text(
            bar_x + bar_w + 0.015, zy, text,
            transform=ax.transAxes, fontsize=7.5, color=C_MUTED,
            ha="left", va="center",
        )


# -- Panel 4: Route ---------------------------------------------------------

def draw_route(ax, x, y, w, h, spec):
    """Three routing options; the active one highlighted."""
    rc = spec["route_color"]
    active = spec["route"]

    # Checkbox size
    cb = 0.018  # checkbox side length (in axes coords)
    bx = x + 0.012
    text_x = bx + cb + 0.008

    # Colors match Budget gauge zones: fill + border
    options = [
        ("One-Shot",  y + h * 0.60, r"$\delta/\delta_{\min}\!>\!2$",
         "#DBEAFE", "#81C784"),   # blue fill, green border
        ("Staged",    y + h * 0.43, r"$\hat{\rho}\!<\!0.5$",
         "#FEF3C7", "#FFB74D"),   # amber fill, orange border
        ("Decompose", y + h * 0.26, "else",
         "#E5E7EB", "#E57373"),   # gray fill, red border
    ]
    for name, by, cond, fill, border in options:
        hit = name == active

        # Checkbox
        ax.add_patch(Rectangle(
            (bx, by - cb / 2), cb, cb,
            transform=ax.transAxes,
            facecolor=fill if hit else "white",
            edgecolor=border, linewidth=1.8,
        ))
        # Checkmark for selected
        if hit:
            ax.text(
                bx + cb / 2, by, "\u2713",
                transform=ax.transAxes, fontsize=11,
                color=C_TEXT, ha="center", va="center",
                fontweight="bold",
            )

        # Label
        ax.text(
            text_x, by, name,
            transform=ax.transAxes, fontsize=8.5,
            color=C_TEXT if hit else C_MUTED,
            ha="left", va="center",
            fontweight="bold" if hit else "normal",
        )
        # Routing condition (Algorithm 1, lines 6/8/10)
        ax.text(
            text_x + w * 0.37, by, cond,
            transform=ax.transAxes, fontsize=7,
            fontweight="bold" if hit else "normal",
            color=C_MUTED,
            ha="left", va="center",
        )

    ax.text(
        x + w / 2, y + 0.06,
        "before generation\n< 10 ms",
        transform=ax.transAxes, fontsize=7.5, color=C_MUTED,
        ha="center", va="top", fontstyle="italic", linespacing=1.4,
    )


# -- Orchestrator ------------------------------------------------------------

def render(spec, output_png, output_pdf, camera_ready_pdf=None):
    fig = plt.figure(figsize=(16, 4.8), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    ax.text(
        0.50, 0.95, spec["title"],
        transform=ax.transAxes, fontsize=18, fontweight="bold",
        color=C_TITLE, ha="center", va="top",
    )

    y, h = 0.10, 0.72
    panels = [
        (0.04, 0.18, "Parse the Constraints",    "Alg. 1, L1"),
        (0.26, 0.26, "Embed the Geometry",        "Alg. 1, L2"),
        (0.56, 0.16, "Budget for Feasibility",    "Alg. 1, L3-4"),
        (0.76, 0.18, "Selectively Route",         "Alg. 1, L6-12"),
    ]

    for px, pw, title, sub in panels:
        add_panel(ax, px, y, pw, h, title, sub)

    for i in range(len(panels) - 1):
        x1 = panels[i][0] + panels[i][1]
        x2 = panels[i + 1][0]
        add_flow_arrow(ax, x1 + 0.003, y + h / 2, x2 - 0.003, y + h / 2)

    px = [p[0] for p in panels]
    pw = [p[1] for p in panels]
    draw_prompt(ax, px[0], y, pw[0], h, spec)
    draw_embed(ax, px[1], y, pw[1], h, spec)
    draw_budget(ax, px[2], y, pw[2], h, spec)
    draw_route(ax, px[3], y, pw[3], h, spec)

    for path in (output_png, output_pdf, camera_ready_pdf):
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)

    fig.savefig(output_png, dpi=220, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    if camera_ready_pdf is not None:
        fig.savefig(camera_ready_pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {output_png}")
    print(f"Wrote {output_pdf}")
    if camera_ready_pdf:
        print(f"Wrote {camera_ready_pdf}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument(
        "--png-output", type=Path,
        default=REBUTTAL_FIG_DIR / "algorithm1_storyboard.png",
    )
    parser.add_argument(
        "--pdf-output", type=Path,
        default=REBUTTAL_FIG_DIR / "algorithm1_storyboard.pdf",
    )
    parser.add_argument(
        "--camera-ready-pdf", type=Path,
        default=CAMERA_READY_FIG_DIR / "algorithm1_storyboard.pdf",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    spec = load_spec(args.spec)
    render(spec, args.png_output, args.pdf_output, args.camera_ready_pdf)


if __name__ == "__main__":
    main()
