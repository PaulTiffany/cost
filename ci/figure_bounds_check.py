"""DEPRECATED: figure_bounds_check.py — superseded by paper_surface_check.py.

This script is preserved as a thin shim that runs the equivalent single-
category check (`card_overflow`) via the broader paper-surface cert. New
development should call `paper_surface_check.py` directly so all impacting
categories (text-image, image-image, drawing-drawing, margin overflow) are
exercised together.

The shim preserves the original CLI surface so any tooling that still
invokes `python ci/figure_bounds_check.py` continues to work, but emits a
deprecation notice on stderr.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "ci" / "paper_surface_check.py"


def main() -> int:
    print(
        "figure_bounds_check.py is deprecated; forwarding to "
        "paper_surface_check.py --checks card_overflow",
        file=sys.stderr,
    )
    forwarded = ["python", str(TARGET), "--checks", "card_overflow", "--scope", "body"]
    extra = sys.argv[1:]
    if "--json-out" in extra:
        i = extra.index("--json-out")
        forwarded.extend(["--json-out", extra[i + 1]])
    if "--pdf" in extra:
        i = extra.index("--pdf")
        forwarded.extend(["--pdf", extra[i + 1]])
    return subprocess.call(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
