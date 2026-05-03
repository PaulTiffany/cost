#!/usr/bin/env python3
"""
plot_image_transfer.py - Build the demonstrative figure for the image
medium bound transfer experiment.

Reads image_transfer_results.json, picks the first successful trial of
each staged cell (S0..S5), and composes a 2x3 grid of side-by-side
panels with k labels. The resulting figure goes in paper App C; the
reader sees the cliff by inspection.

Output:
  paper/figures/image_transfer.pdf  (used by main.tex)
  paper/figures/image_transfer.png  (sanity preview)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.image as mpimg

import argparse

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
DEFAULT_RESULTS_JSON = SCRIPT_DIR / "image_transfer_results_runB.json"
OUT_PDF = REPO_ROOT / "paper" / "figures" / "image_transfer.pdf"
OUT_PNG = REPO_ROOT / "paper" / "figures" / "image_transfer.png"

# Cells in left-to-right, top-to-bottom order. S0 first (zero baseline),
# then S1..S5 with increasing k.
PANEL_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]
PANEL_LABELS = {
    "S0": r"$k{=}0$ (empty prompt)",
    "S1": r"$k{=}1$ (LaTeX block)",
    "S2": r"$k{=}2$ (+ accessibility)",
    "S3": r"$k{=}3$ (+ anonymity)",
    "S4": r"$k{=}4$ (+ no-offensive)",
    "S5": r"$k{=}5$ (+ ethics-scope)",
}


def first_success_image(payload: dict, cell_id: str) -> Path | None:
    for tr in payload.get("trials", []):
        if tr.get("cell_id") != cell_id:
            continue
        path = tr.get("final_image_path")
        if path:
            return REPO_ROOT / path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_JSON,
                        help="Path to image_transfer_results_*.json")
    args = parser.parse_args()

    if not args.results.exists():
        print(f"ERROR: {args.results} not found. Run run_image_transfer.py first.",
              file=sys.stderr)
        return 2

    payload = json.loads(args.results.read_text(encoding="utf-8"))

    fig, axes = plt.subplots(2, 3, figsize=(9.5, 6.0))
    axes = axes.flatten()
    for ax, cell_id in zip(axes, PANEL_ORDER):
        ax.set_axis_off()
        ax.set_title(PANEL_LABELS[cell_id], fontsize=10)
        img_path = first_success_image(payload, cell_id)
        if img_path and img_path.exists():
            try:
                ax.imshow(mpimg.imread(str(img_path)))
            except Exception as e:
                ax.text(0.5, 0.5, f"(image load error: {e})",
                         ha="center", va="center", fontsize=8, transform=ax.transAxes)
        else:
            ax.text(0.5, 0.5, "(no image returned)",
                     ha="center", va="center", fontsize=10,
                     style="italic", color="0.4", transform=ax.transAxes)

    fig.suptitle(
        r"Image-medium bound transfer: same target, increasing $k$",
        fontsize=12, y=0.995,
    )
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.97])

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PDF, bbox_inches="tight")
    fig.savefig(OUT_PNG, bbox_inches="tight", dpi=120)
    print(f"wrote {OUT_PDF.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_PNG.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
