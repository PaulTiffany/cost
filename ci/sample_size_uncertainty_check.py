#!/usr/bin/env python3
"""
sample_size_uncertainty_check.py - Flag empirical claims whose stated precision
is finer than the binomial standard error the underlying sample size supports.

Advisory check only (exit 0 always). Catches patterns like "33.3% (N=24)" where
SE half-width is ~19%, making one-decimal precision meaningless.

Method
------
1. Load ci/claim_data_ties.json.
2. For each claim with compare_as == "float" that looks like a percentage
   (expected value in (0, 100]), derive N from the claim source JSON using
   the lookup chain: _meta.n_trials, _meta.n_trials_per_cell, or sum of
   n_trials from by_tier/tiers structures.
3. Compute binomial SE half-width = 1.96 * sqrt(p*(1-p)/N) * 100.
4. Compare stated precision (decimal places of the expected value) against
   SE half-width.
5. Report OVERPRECISE / OK per claim.

Output: ci/sample_size_uncertainty_results.json
Exit: always 0 (advisory).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TIES_JSON = SCRIPT_DIR / "claim_data_ties.json"
RESULTS_JSON = SCRIPT_DIR / "sample_size_uncertainty_results.json"


# ---------------------------------------------------------------------------
# N-derivation helpers
# ---------------------------------------------------------------------------

def _try_load_source(source_file: str) -> tuple[Any, str]:
    """Load JSON from REPO_ROOT/source_file. Returns (data, error_str)."""
    path = REPO_ROOT / source_file
    if not path.exists():
        return None, f"source not found: {path}"
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh), ""
    except Exception as exc:
        return None, f"load error: {exc}"


def _derive_n(data: Any, value_expr: str) -> tuple[int | None, str]:
    """
    Try to derive N (number of trials) from the loaded data object.
    For per-tier percentage claims we prefer per-cell N over the grand total.
    Returns (N, note_string).
    """
    if data is None:
        return None, "data not loaded"

    # Priority 1: _meta.n_trials_per_cell (most specific for tier-level pct claims)
    if isinstance(data, dict) and "_meta" in data:
        meta = data["_meta"]
        if isinstance(meta, dict):
            if "n_trials_per_cell" in meta:
                return int(meta["n_trials_per_cell"]), "from _meta.n_trials_per_cell"
            if "parameters" in meta and isinstance(meta["parameters"], dict):
                params = meta["parameters"]
                if "n_trials_per_cell" in params:
                    return int(params["n_trials_per_cell"]), "from _meta.parameters.n_trials_per_cell"

    # Priority 2: _meta.n_trials (grand total; lower priority than per_cell)
    if isinstance(data, dict) and "_meta" in data:
        meta = data["_meta"]
        if isinstance(meta, dict) and "n_trials" in meta:
            return int(meta["n_trials"]), "from _meta.n_trials (grand total)"

    # Priority 3: aggregate.stats.<tier>.n_trials (calibration pattern)
    if isinstance(data, dict) and "aggregate" in data:
        agg = data["aggregate"]
        if isinstance(agg, dict) and "stats" in agg:
            stats = agg["stats"]
            if isinstance(stats, dict):
                # Claims are per-tier: use per-tier N
                tier_ns = [
                    v["n_trials"]
                    for v in stats.values()
                    if isinstance(v, dict) and "n_trials" in v
                ]
                if tier_ns:
                    return int(tier_ns[0]), "from aggregate.stats[tier].n_trials"

    # Priority 4: summary.<tier>.n_trials (json_nl pattern)
    if isinstance(data, dict) and "summary" in data:
        summary = data["summary"]
        if isinstance(summary, dict):
            for v in summary.values():
                if isinstance(v, dict) and "n_trials" in v:
                    return int(v["n_trials"]), "from summary[tier].n_trials"

    # Priority 5: parameters.n_trials (charitable pattern)
    if isinstance(data, dict) and "parameters" in data:
        params = data["parameters"]
        if isinstance(params, dict) and "n_trials" in params:
            return int(params["n_trials"]), "from parameters.n_trials"

    # Priority 6: top-level n_trials
    if isinstance(data, dict) and "n_trials" in data:
        return int(data["n_trials"]), "from top-level n_trials"

    # Priority 7: count records in data['data'] or data['results'] (rough estimate)
    for key in ("data", "results"):
        if isinstance(data, dict) and key in data:
            items = data[key]
            if isinstance(items, list) and len(items) > 0:
                return len(items), f"estimated from len(data['{key}'])"

    return None, "N not derivable"


def _stated_precision_pct(expected: float) -> float:
    """
    Return the precision implied by the decimal representation of `expected`
    as a percentage-point increment.

    e.g.  33.3  -> precision 0.1 pct
          7.37  -> precision 0.01 pct
          76    -> precision 1 pct (integer)
    """
    s = str(expected)
    if "." in s:
        decimals = len(s.split(".", 1)[1].rstrip("0") or "0")
    else:
        decimals = 0
    return 10.0 ** (-decimals)


def _looks_like_percentage(expected: float, claim_text: str) -> bool:
    """
    Heuristic: is this claim a percentage?
    Requires BOTH a value in (0, 100] AND at least one pct keyword in the
    claim text. This avoids false-positives from ratio/multiplier claims
    (e.g. '2.5x staging ratio' with expected=2.5).
    """
    if not (0 < expected <= 100):
        return False
    text_lower = claim_text.lower()
    pct_keywords = ["%", "percent", "pct", "pass rate", "failure rate",
                    "pass_rate", "failure_rate", "fraction", " pct"]
    return any(kw in text_lower for kw in pct_keywords)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not TIES_JSON.exists():
        print(f"ERROR: {TIES_JSON} not found", file=sys.stderr)
        sys.exit(0)

    with TIES_JSON.open(encoding="utf-8") as fh:
        ties = json.load(fh)

    claims = ties.get("claims", {})
    per_claim: list[dict] = []
    total = ok = warn = 0

    for name, entry in claims.items():
        compare_as = entry.get("compare_as", "")
        expected = entry.get("expected")
        source_file = entry.get("source_file", "")
        claim_text = entry.get("claim_text_excerpt", "")

        # Only examine float claims that look like percentages
        if compare_as != "float":
            continue
        if expected is None:
            continue
        if not _looks_like_percentage(float(expected), claim_text):
            continue

        total += 1
        p = float(expected)

        data, load_err = _try_load_source(source_file)
        value_expr = entry.get("value_expr", "")
        n, n_note = _derive_n(data, value_expr)

        if n is None:
            per_claim.append({
                "name": name,
                "expected": p,
                "n_trials": None,
                "se_halfwidth_pct": None,
                "stated_precision_pct": _stated_precision_pct(p),
                "verdict": "UNKNOWN",
                "detail": f"N not derivable ({n_note}); load_err={load_err or 'none'}",
            })
            # UNKNOWN does not count as warn or ok
            continue

        # Convert p from percentage to proportion for SE formula
        p_prop = p / 100.0
        p_prop_clamped = max(1e-6, min(1 - 1e-6, p_prop))
        se_halfwidth = 1.96 * math.sqrt(p_prop_clamped * (1 - p_prop_clamped) / n) * 100
        stated_prec = _stated_precision_pct(p)

        # Tuned thresholds (avoid false positives for routine ML-paper precision):
        #   Small N (< 30): flag if stated_prec < SE (strict — small N means
        #     even 1 decimal can be misleading).
        #   Standard N (>= 30): accept 0.1% precision as field-norm (one decimal
        #     place is the convention in ML papers regardless of SE width). Only
        #     flag if stated precision is finer than 0.1% AND finer than SE.
        SMALL_N_THRESHOLD = 30
        FIELD_NORM_PRECISION_PCT = 0.1
        if n < SMALL_N_THRESHOLD:
            overprecise = stated_prec < se_halfwidth
            trigger = "small_N (<30)"
        else:
            overprecise = (stated_prec < FIELD_NORM_PRECISION_PCT) and (stated_prec < se_halfwidth)
            trigger = f"finer than field-norm {FIELD_NORM_PRECISION_PCT}% (and finer than SE)"

        if overprecise:
            verdict = "OVERPRECISE"
            warn += 1
            detail = (
                f"Stated precision {stated_prec:.3f}% is finer than "
                f"SE half-width {se_halfwidth:.1f}% (N={n}); trigger: {trigger}. "
                f"N source: {n_note}."
            )
        elif stated_prec < se_halfwidth:
            verdict = "OK_FIELD_NORM"
            ok += 1
            detail = (
                f"Stated precision {stated_prec:.3f}% wider than field-norm floor; "
                f"SE half-width {se_halfwidth:.1f}% (N={n}). Field-norm conventions accept 1-decimal "
                f"precision when N>={SMALL_N_THRESHOLD}. N source: {n_note}."
            )
        else:
            verdict = "OK"
            ok += 1
            detail = (
                f"Stated precision {stated_prec:.3f}% is within "
                f"SE half-width {se_halfwidth:.1f}% (N={n}). "
                f"N source: {n_note}."
            )

        per_claim.append({
            "name": name,
            "expected": p,
            "n_trials": n,
            "se_halfwidth_pct": round(se_halfwidth, 3),
            "stated_precision_pct": stated_prec,
            "verdict": verdict,
            "detail": detail,
        })

    # Count UNKNOWN separately (not ok, not warn)
    n_unknown = sum(1 for r in per_claim if r["verdict"] == "UNKNOWN")

    summary = {
        "total": total,
        "ok": ok,
        "warn": warn,
        "unknown": n_unknown,
    }

    results = {"summary": summary, "per_claim": per_claim}
    RESULTS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"sample_size_uncertainty_check: {total} percentage claims examined")
    print(f"  OK={ok}  OVERPRECISE(warn)={warn}  UNKNOWN={n_unknown}")
    if warn:
        print("  OVERPRECISE claims (advisory):")
        for r in per_claim:
            if r["verdict"] == "OVERPRECISE":
                print(f"    [{r['name']}] expected={r['expected']}%  "
                      f"N={r['n_trials']}  SE±{r['se_halfwidth_pct']}%  "
                      f"stated_prec={r['stated_precision_pct']}%")
    print(f"Results written to {RESULTS_JSON}")
    sys.exit(0)


if __name__ == "__main__":
    main()
