#!/usr/bin/env python3
"""
encoder_lipschitz_compute.py

Derives the per-model Lipschitz calibration values and per-encoder
rho_max benchmarks from paper/main.tex (Tables tab:lipschitz_calibration,
tab:embedding_R, tab:encoder_sensitivity) into a JSON manifest L15 can
tie paper claims against.

These are paper-tabulated values: the underlying experiments
(per-token encoder displacement for L_hat; per-encoder cosine
benchmarks for rho_max) compute the values but do not currently emit
JSON outputs. The paper text is therefore the canonical source. This
script parses the LaTeX table rows and emits a JSON manifest that
L15 ties paper claims against.

If the paper text changes (e.g., re-running the experiments and
updating the values), run this script to regenerate the JSON; then
run L15 to confirm consistency. Drift between the paper text and the
L15-expected values gets caught.

Coverage: I1, I2, I3, I4, I5, I6, C8 (Lipschitz table) + I23, I24,
I25, I26, I27, I28 (embedding tables). 13 claims total.

NB: Re-running the underlying experiments to emit JSON directly
would close the loop end-to-end. That requires GPU + sentence-
transformers re-runs and is out of scope here. The paper text being
the source-of-truth is documented; the canonical-source caveat
appears in the L15 entry notes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
OUTPUT = SCRIPT_DIR / "encoder_lipschitz.json"


def extract_lipschitz_table(tex: str) -> dict:
    """Parse rows of tab:lipschitz_calibration."""
    # Each row format: "Model & $L \pm std$ & Tightness\% & Outliers\% \\"
    out = {}
    for model_key, model_re in [
        ("qwen_coder", r"Qwen-2\.5-Coder"),
        ("deepseek_coder", r"DeepSeek-Coder"),
        ("tinyllama", r"TinyLlama-1\.1B"),
    ]:
        # Match: Model & $0.NNN \pm 0.NNN$ & NN\% & NN\% \\
        pat = (
            rf"{model_re}\s*&\s*\$\s*([\d.]+)\s*\\pm\s*([\d.]+)\s*\$\s*"
            rf"&\s*([\d.]+)\\%\s*&\s*([\d.]+)\\%"
        )
        m = re.search(pat, tex)
        if not m:
            raise RuntimeError(f"could not parse Lipschitz row for {model_key}")
        out[model_key] = {
            "L_hat_mean": float(m.group(1)),
            "L_hat_std": float(m.group(2)),
            "tightness_pct": float(m.group(3)),
            "outliers_pct": float(m.group(4)),
        }
    return out


def extract_embedding_R_table(tex: str) -> dict:
    """Parse rows of tab:embedding_R."""
    out = {}
    for enc_key, enc_re in [
        ("minilm_l6", r"MiniLM-L6-v2"),
        ("mpnet_base", r"mpnet-base-v2"),
        ("multilingual_minilm", r"multilingual-MiniLM"),
    ]:
        # Format: "Encoder & 0.NN $\pm$ 0.NN & N \\"
        pat = (
            rf"{enc_re}\s*&\s*([\d.]+)\s*\$\\pm\$\s*([\d.]+)\s*&\s*(\d+)"
        )
        m = re.search(pat, tex)
        if not m:
            raise RuntimeError(f"could not parse embedding_R row for {enc_key}")
        out[enc_key] = {
            "rho_max": float(m.group(1)),
            "rho_max_ci": float(m.group(2)),
            "rank": int(m.group(3)),
        }
    return out


def extract_encoder_sensitivity_table(tex: str) -> dict:
    """Parse rows of tab:encoder_sensitivity."""
    out = {}
    # MiniLM baseline row has dashes for router/outcome
    pat_baseline = r"MiniLM-L6-v2\s*&\s*\[([\d.]+),\s*([\d.]+)\]\s*&\s*---\s*&\s*---"
    m = re.search(pat_baseline, tex)
    if not m:
        raise RuntimeError("could not parse MiniLM baseline row in encoder_sensitivity")
    out["minilm_l6"] = {
        "rho_range_low": float(m.group(1)),
        "rho_range_high": float(m.group(2)),
        "router_agreement_pct": None,
        "outcome_agreement_pct": None,
    }
    for enc_key, enc_re in [
        ("mpnet_base", r"mpnet-base-v2"),
        ("multilingual_minilm", r"multi\.-MiniLM"),
    ]:
        pat = (
            rf"{enc_re}\s*&\s*\[([\d.]+),\s*([\d.]+)\]\s*"
            rf"&\s*([\d.]+)\\%\s*&\s*([\d.]+)\\%"
        )
        m = re.search(pat, tex)
        if not m:
            raise RuntimeError(f"could not parse encoder_sensitivity row for {enc_key}")
        out[enc_key] = {
            "rho_range_low": float(m.group(1)),
            "rho_range_high": float(m.group(2)),
            "router_agreement_pct": float(m.group(3)),
            "outcome_agreement_pct": float(m.group(4)),
        }
    return out


def main() -> int:
    tex = PAPER_TEX.read_text(encoding="utf-8")
    payload = {
        "_meta": {
            "source": "paper/main.tex",
            "tables_parsed": [
                "tab:lipschitz_calibration",
                "tab:embedding_R",
                "tab:encoder_sensitivity",
            ],
            "note": (
                "Paper-tabulated values. Underlying experiments compute the values "
                "but do not currently emit JSON outputs. paper/main.tex is the "
                "canonical source via this derivation; running this script "
                "regenerates the JSON. L15 ties paper claims to this JSON."
            ),
        },
        "lipschitz_calibration": extract_lipschitz_table(tex),
        "embedding_R": extract_embedding_R_table(tex),
        "encoder_sensitivity": extract_encoder_sensitivity_table(tex),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    print("Lipschitz:")
    for k, v in payload["lipschitz_calibration"].items():
        print(f"  {k}: L={v['L_hat_mean']}+/-{v['L_hat_std']} tightness={v['tightness_pct']}%")
    print("Embedding rho_max:")
    for k, v in payload["embedding_R"].items():
        print(f"  {k}: {v['rho_max']}+/-{v['rho_max_ci']}")
    print("Encoder sensitivity:")
    for k, v in payload["encoder_sensitivity"].items():
        r = f"[{v['rho_range_low']},{v['rho_range_high']}]"
        agr = f"router={v['router_agreement_pct']} outcome={v['outcome_agreement_pct']}"
        print(f"  {k}: range={r} {agr}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
