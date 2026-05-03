#!/usr/bin/env python3
"""
cross_domain_router_compute.py

Parses the cross-domain transfer table (paper Table tab:transfer, around
line 826 of paper/main.tex) into a structured JSON for L15 grounding.

Source rows:
    Code (calibration) & 100% & --- & 1.8%
    JSON-NL (transfer) &  94% & +2% & 2.1%
    IF-DSL (transfer)  &  91% & -1% & 2.4%
    Bytebeat (transfer)&  88% & +1% & 3.1%

These are paper-tabulated transfer-test results; we make the table the
source of truth so claims I42-I45 stay tied to it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
OUTPUT = SCRIPT_DIR / "cross_domain_router.json"


# Paper label -> output key
DOMAIN_ROWS = [
    ("Code (calibration)", "code"),
    ("JSON-NL (transfer)", "json_nl"),
    ("IF-DSL (transfer)", "if_dsl"),
    ("Bytebeat (transfer)", "bytebeat"),
]


def parse_row(tex: str, label: str) -> dict:
    label_re = re.escape(label)
    # router agree: integer percent
    # delta pass: --- (None) or signed percent like +2% / -1%
    # regret: float percent like 1.8%
    row_pat = (
        rf"{label_re}\s*&\s*(\d+)\\?%"
        rf"\s*&\s*(---|\$?[+\-]?\d+\\?%\$?)"
        rf"\s*&\s*([\d.]+)\\?%"
    )
    m = re.search(row_pat, tex)
    if not m:
        raise RuntimeError(f"could not parse transfer-table row for {label!r}")
    router_agree = int(m.group(1))
    delta_raw = m.group(2)
    if delta_raw == "---":
        delta_pass = None
    else:
        d = re.search(r"([+\-]?\d+)", delta_raw)
        delta_pass = int(d.group(1)) if d else None
    regret = float(m.group(3))
    return {
        "router_agree_pct": router_agree,
        "delta_pass_pct": delta_pass,
        "regret_pct": regret,
    }


def main() -> None:
    tex = PAPER_TEX.read_text(encoding="utf-8")
    domains = {}
    for label, key in DOMAIN_ROWS:
        domains[key] = parse_row(tex, label)

    payload = {
        "_meta": {
            "description": (
                "Cross-domain transfer table (paper Table tab:transfer) parsed from "
                "paper/main.tex around line 826. Each domain row records router-decision "
                "agreement with domain-optimal, delta in pass rate, and regret vs. "
                "domain-specific tuning. Backs claims I42-I45."
            ),
            "source": "paper/main.tex Table tab:transfer (around line 826)",
            "row_labels": [lbl for lbl, _ in DOMAIN_ROWS],
        },
        "domains": domains,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    for key, row in domains.items():
        print(f"  {key:10s}  agree={row['router_agree_pct']:>3}%  "
              f"delta={row['delta_pass_pct']}  regret={row['regret_pct']}%")


if __name__ == "__main__":
    main()
