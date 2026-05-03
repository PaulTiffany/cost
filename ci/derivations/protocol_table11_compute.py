#!/usr/bin/env python3
"""
protocol_table11_compute.py

Parses the protocol-comparison table (the "Table 11" referenced by C15/I36-I41)
out of paper/main.tex into a structured JSON. The table compares 6 routing
protocols by Pass%, token cost, and speedup.

Source rows (from paper/main.tex around line 807):
    One-shot (always)        5%   256   1.0x
    Staged (always)         18%   512   0.5x
    Best-of-4 sampling      12%  1024   0.25x
    Self-refine (<=3 iter)  15%   640   0.4x
    Geometric router        18%   384   0.67x
    Oracle (per-instance)   21%   320   0.8x

These values are paper-tabulated experimental results. They originate from the
code_constraint experiment but are presented in this consolidated table form;
we make the table itself the source of truth so paper claims stay tied to it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
OUTPUT = SCRIPT_DIR / "protocol_table11.json"


# Map: (label_in_paper, key_in_output)
PROTOCOL_LABELS = [
    ("One-shot (always)", "one_shot"),
    ("Staged (always)", "staged"),
    ("Best-of-4 sampling", "best_of_4"),
    ("Self-refine", "self_refine"),
    ("Geometric router", "geometric_router"),
    ("Oracle (per-instance)", "oracle"),
]


def parse_row(tex: str, label: str) -> dict:
    r"""Parse a row of form  '<label> & <pct>\% & <tokens> & $<mult>\times$ \\'."""
    # Allow both bare and \textbf-wrapped values; allow the special $\mathbf{...}$ form.
    label_re = re.escape(label)
    # Pass%: integer percent (possibly inside \textbf{})
    # Tokens: integer (possibly inside \textbf{})
    # Speedup: float inside $...\times$ (possibly $\mathbf{...\times}$)
    row_pat = (
        rf"{label_re}[^&]*&\s*(?:\\textbf\{{)?(\d+)\\?%(?:\}})?\s*"
        rf"&\s*(?:\\textbf\{{)?(\d+)(?:\}})?\s*"
        rf"&\s*\$?(?:\\mathbf\{{)?([\d.]+)\\?\\?times"
    )
    m = re.search(row_pat, tex)
    if not m:
        raise RuntimeError(f"could not parse table row for label={label!r}")
    return {
        "pass_pct": int(m.group(1)),
        "tokens": int(m.group(2)),
        "speedup_vs_oneshot": float(m.group(3)),
    }


def main() -> None:
    tex = PAPER_TEX.read_text(encoding="utf-8")

    protocols = {}
    for label, key in PROTOCOL_LABELS:
        protocols[key] = parse_row(tex, label)

    payload = {
        "_meta": {
            "description": (
                "Protocol comparison table parsed from paper/main.tex (the table "
                "captioned around line 807, referenced as C15 / I36-I41 / I47 / "
                "Table 11 in claim audit). Each row: pass_pct (integer percent), "
                "tokens (integer), speedup_vs_oneshot (float, 1.0x = one-shot baseline)."
            ),
            "source": "paper/main.tex protocol comparison table (around line 807)",
            "row_labels": [lbl for lbl, _ in PROTOCOL_LABELS],
        },
        "protocols": protocols,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    for key, row in protocols.items():
        print(f"  {key:20s}  pass={row['pass_pct']:>3}%  tokens={row['tokens']:>4}  speedup={row['speedup_vs_oneshot']}x")


if __name__ == "__main__":
    main()
