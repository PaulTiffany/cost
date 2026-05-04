#!/usr/bin/env python3
"""
multi_seed_drift_check.py

L25 cert layer. Reads the cached drift data at ci/multi_seed_drift_data.json
(populated by ci/multi_seed_drift_runner.py) and verifies that per-model
sampling variance is within an acceptable threshold.

This check is API-free; it reads the cached results only. The runner does
the API calls and is invoked by hand when the user wants to refresh the
cache. If the cache is missing the check returns 0 with a note that the
data has not been collected yet (advisory; never blocks the cert).

Suite: statistical_hygiene.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = Path(__file__).resolve().parent / "multi_seed_drift_data.json"
RESULTS_PATH = Path(__file__).resolve().parent / "multi_seed_drift_results.json"

# Threshold: per-cell actual_stdev (chars). For the digit_sum_42 task
# at temperature=0.7 the response length varies meaningfully across
# models, but a single model with healthy sampling should stay under
# 400 chars stdev across 5 reruns. Anything higher suggests the model
# is mode-collapsing or has a runaway distribution that warrants
# investigation. Threshold is conservative; raise after a stable run.
ACTUAL_STDEV_THRESHOLD = 400.0


def main() -> int:
    if not DATA_PATH.exists():
        payload = {
            "status": "no_cache",
            "passed": True,
            "note": ("ci/multi_seed_drift_data.json not present. Run "
                     "ci/multi_seed_drift_runner.py to populate the cache. "
                     "Advisory; cert is not blocked."),
        }
        RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("multi_seed_drift_check: no cache present (advisory)")
        return 0

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    per_model = data.get("per_model", {})
    findings = []
    over_threshold = 0
    for model_name, m in per_model.items():
        std = m.get("actual_stdev", 0.0)
        n = m.get("n_runs", 0)
        within = std <= ACTUAL_STDEV_THRESHOLD
        if not within:
            over_threshold += 1
        findings.append({
            "model": model_name,
            "n_runs": n,
            "actual_stdev": std,
            "delta_stdev": m.get("delta_stdev"),
            "fixed_point_hit_rate": m.get("fixed_point_hit_rate"),
            "threshold": ACTUAL_STDEV_THRESHOLD,
            "within_threshold": within,
        })

    payload = {
        "status": "cached",
        "passed": over_threshold == 0,
        "n_models_checked": len(per_model),
        "n_over_threshold": over_threshold,
        "threshold_chars": ACTUAL_STDEV_THRESHOLD,
        "data_meta": data.get("_meta", {}),
        "findings": findings,
        "note": ("Advisory layer. Per-model actual_stdev (response length variance "
                 "across reruns) compared against threshold. A model that exceeds "
                 "the threshold is flagged for investigation but does not block the cert."),
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"multi_seed_drift_check: {len(per_model)} models checked, "
          f"{over_threshold} over threshold ({ACTUAL_STDEV_THRESHOLD:.0f} chars)")
    for f in findings:
        flag = " " if f["within_threshold"] else "*"
        print(f" {flag} {f['model']:<12} n={f['n_runs']} actual_stdev={f['actual_stdev']:>7.1f} "
              f"delta_stdev={f['delta_stdev']:>7.1f}")

    # Advisory: never block the cert on drift alone. Return 0 either way;
    # human triage on the results JSON is the actionable signal.
    return 0


if __name__ == "__main__":
    sys.exit(main())
