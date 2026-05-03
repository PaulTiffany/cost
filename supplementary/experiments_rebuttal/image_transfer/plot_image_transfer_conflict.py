#!/usr/bin/env python3
"""
plot_image_transfer_conflict.py - Build the Run C demonstrative figure.

Two rows:
  Row 1: oneshot cliff sweep (C-oneshot k=1,3,5,7,9,11), 6 panels.
  Row 2: image-staged k=11 recovery (stage 1 -> stage 2), 2 panels
         spanning the bottom width.

Output:
  paper/figures/image_transfer_conflict.pdf
  paper/figures/image_transfer_conflict.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.gridspec as gridspec

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
DEFAULT_RESULTS_JSON = SCRIPT_DIR / "image_transfer_results_runC.json"
OUT_PDF = REPO_ROOT / "paper" / "figures" / "image_transfer_conflict.pdf"
OUT_PNG = REPO_ROOT / "paper" / "figures" / "image_transfer_conflict.png"

ONESHOT_KS = [1, 3, 5, 7, 9, 11]


def first_success_oneshot(payload: dict, k: int) -> Path | None:
    cell_id = f"C-oneshot-{k}"
    for tr in payload.get("trials", []):
        if tr.get("cell_id") != cell_id:
            continue
        path = tr.get("final_image_path")
        if path:
            return REPO_ROOT / path
    return None


def first_staged_stage(payload: dict, stage: int) -> Path | None:
    """Find the first successful C-staged-11 trial's stage1 (step 0) or stage2 (step 1) image."""
    for tr in payload.get("trials", []):
        if tr.get("cell_id") != "C-staged-11":
            continue
        steps = tr.get("steps", [])
        if stage < len(steps) and steps[stage].get("success") and steps[stage].get("image_path"):
            return REPO_ROOT / steps[stage]["image_path"]
    return None


def imshow_or_placeholder(ax, img_path: Path | None, missing_label: str = "(no image)"):
    ax.set_axis_off()
    if img_path and img_path.exists():
        try:
            ax.imshow(mpimg.imread(str(img_path)))
        except Exception as e:
            ax.text(0.5, 0.5, f"(load error: {e})", ha="center", va="center",
                     fontsize=8, transform=ax.transAxes)
    else:
        ax.text(0.5, 0.5, missing_label, ha="center", va="center",
                 fontsize=10, style="italic", color="0.4", transform=ax.transAxes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_JSON)
    args = parser.parse_args()

    if not args.results.exists():
        print(f"ERROR: {args.results} not found.", file=sys.stderr)
        return 2

    payload = json.loads(args.results.read_text(encoding="utf-8"))

    fig = plt.figure(figsize=(11.0, 6.5))
    gs = gridspec.GridSpec(2, 6, figure=fig, height_ratios=[1.0, 1.0],
                            hspace=0.30, wspace=0.10)

    # Row 1: oneshot cliff sweep (6 panels)
    for col, k in enumerate(ONESHOT_KS):
        ax = fig.add_subplot(gs[0, col])
        ax.set_title(f"$k{{=}}{k}$", fontsize=10)
        imshow_or_placeholder(ax, first_success_oneshot(payload, k))

    # Row 2: image-staged k=11 (2 panels spanning, 3 cols each)
    ax_s1 = fig.add_subplot(gs[1, 0:3])
    ax_s1.set_title(r"Stage 1: base image (LaTeX only)", fontsize=10)
    imshow_or_placeholder(ax_s1, first_staged_stage(payload, 0))

    ax_s2 = fig.add_subplot(gs[1, 3:6])
    ax_s2.set_title(r"Stage 2: stage-1 image $+$ 11 ethics", fontsize=10)
    imshow_or_placeholder(ax_s2, first_staged_stage(payload, 1))

    # Row labels via figtext
    fig.text(0.005, 0.78, "One-shot cliff sweep", rotation=90,
              ha="center", va="center", fontsize=9, color="0.3")
    fig.text(0.005, 0.30, "Image-staged recovery", rotation=90,
              ha="center", va="center", fontsize=9, color="0.3")

    fig.suptitle(
        r"Conflict-stack image-medium experiment: oneshot cliff (top) vs image-staged recovery (bottom)",
        fontsize=11, y=0.98,
    )
    fig.tight_layout(rect=[0.02, 0.0, 1.0, 0.96])

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PDF, bbox_inches="tight")
    fig.savefig(OUT_PNG, bbox_inches="tight", dpi=120)
    print(f"wrote {OUT_PDF.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_PNG.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
