#!/usr/bin/env python3
"""
recover_runB_json.py - Reconstruct image_transfer_results_runB.json from
disk state when the harness crashed before writing it.

Scans outputs/runB/ for PNG files, computes their hashes, infers cell/
trial/step structure from filenames, and emits a results JSON in the
same schema the harness would have written. Failed steps (no PNG on
disk) are recorded as success=False so the manifest stays honest.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OUTPUTS_DIR = SCRIPT_DIR / "outputs" / "runB"
RESULTS_JSON = SCRIPT_DIR / "image_transfer_results_runB.json"

CELL_DEFS = {
    "S0":     {"k": 0,          "protocol": "staged",      "n_steps": 1},
    "S1":     {"k": 1,          "protocol": "staged",      "n_steps": 1},
    "S2":     {"k": 2,          "protocol": "staged",      "n_steps": 1},
    "S3":     {"k": 3,          "protocol": "staged",      "n_steps": 1},
    "S4":     {"k": 4,          "protocol": "staged",      "n_steps": 1},
    "S5":     {"k": 5,          "protocol": "staged",      "n_steps": 1},
    "S-imp":  {"k": "implicit", "protocol": "staged",      "n_steps": 1},
    "R-exp":  {"k": "explicit", "protocol": "self-refine", "n_steps": 5},
}
N_TRIALS = 3


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    trials = []
    cells_summary = {}
    for cell_id, cdef in CELL_DEFS.items():
        n_succ_in_cell = 0
        total_calls = 0
        for trial in range(N_TRIALS):
            steps = []
            n_calls = cdef["n_steps"]
            for step in range(n_calls):
                if cdef["n_steps"] == 1:
                    fname = f"{cell_id}_t{trial}.png"
                else:
                    fname = f"{cell_id}_t{trial}_step{step}.png"
                p = OUTPUTS_DIR / fname
                if p.exists():
                    steps.append({
                        "step": step,
                        "prompt_hash": "<not recorded; harness crashed before JSON write>",
                        "prompt_chars": -1,
                        "image_path": str(p.relative_to(REPO_ROOT)).replace("\\", "/"),
                        "image_hash": sha256_file(p),
                        "image_bytes": p.stat().st_size,
                        "success": True,
                        "error": "",
                    })
                else:
                    steps.append({
                        "step": step,
                        "prompt_hash": "<not recorded>",
                        "prompt_chars": -1,
                        "image_path": None,
                        "image_hash": None,
                        "image_bytes": 0,
                        "success": False,
                        "error": "harness crashed before this call returned (recovered post-hoc)",
                    })
            final = next((s for s in reversed(steps) if s["success"]), None)
            trials.append({
                "cell_id": cell_id,
                "trial": trial,
                "protocol": cdef["protocol"],
                "k": cdef["k"],
                "n_calls": n_calls,
                "steps": steps,
                "final_image_path": final["image_path"] if final else None,
                "final_image_hash": final["image_hash"] if final else None,
            })
            if final:
                n_succ_in_cell += 1
            total_calls += n_calls
        cells_summary[cell_id] = {
            "k": cdef["k"],
            "protocol": cdef["protocol"],
            "description": "(see harness CELLS dict)",
            "n_trials": N_TRIALS,
            "n_successes": n_succ_in_cell,
            "calls_per_trial": cdef["n_steps"],
            "total_calls": total_calls,
        }

    payload = {
        "schema_version": 2,
        "run_id": "runB",
        "recovered_post_hoc": True,
        "recovery_note": "Harness crashed on R-exp t2 step4 (OpenRouter returned non-JSON). 35/36 PNGs preserved on disk; this manifest reconstructed by scanning outputs/runB/.",
        "medium_frame": "Generate an image illustrating the following:\n\n",
        "medium_frame_source": "OpenAI image-gen prompting cookbook (no mandatory prefix; documented common-convention 'declare modality' instruction). Cookbook: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide",
        "model": "openai/gpt-5.4-image-2",
        "n_trials_per_cell": N_TRIALS,
        "cells": cells_summary,
        "trials": trials,
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {RESULTS_JSON.name}")
    for cid, s in cells_summary.items():
        print(f"  {cid:6s} {s['n_successes']}/{s['n_trials']} success, {s['total_calls']} total calls")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
