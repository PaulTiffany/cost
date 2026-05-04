#!/usr/bin/env python3
"""
sample_size_adequacy_check.py

Suite: statistical_hygiene

Walks ci/claim_data_ties.json and, for every claim whose value_expr
evaluates to a percentage / proportion / pass-rate, attempts to infer the
denominator N from the source JSON. Flags any claim where the inferred N is
below 30, the conventional binomial-proportion adequacy floor.

Why this suite belongs here: the paper's empirical core is a stack of rate
comparisons (0/4272, 88% vs 0%, 26% vs 93%, etc.). When a rate is computed
from a small cell, the visible precision exceeds what the sample size will
support. This check makes that asymmetry visible per claim, separately from
the precision-vs-SE check (sample_size_uncertainty_check.py), which speaks
to over-stated decimals rather than to N adequacy.

Behavior:
  Advisory by default. Returns 0 even when warnings are present. Returns 1
  only if a claim with N < 30 is also classified as headline-tier (see the
  is_headline heuristic below).

  When N cannot be inferred, the claim is recorded as inconclusive and not
  flagged. When the value expression returns an integer count rather than a
  rate, the claim is skipped (counts are exact).

Output: ci/sample_size_adequacy_results.json
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
RESULTS_JSON = SCRIPT_DIR / "sample_size_adequacy_results.json"

ADEQUACY_FLOOR = 30


# Restricted globals for value_expr evaluation; mirrors claim_data_ties_check.
_SAFE_BUILTINS = {
    "sum": sum,
    "len": len,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "next": next,
}
_SAFE_GLOBALS = {"__builtins__": {}, "math": math}


def _load_source(source_file: str) -> tuple[Any, str]:
    path = REPO_ROOT / source_file
    if not path.exists():
        return None, f"source file missing: {source_file}"
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh), ""
    except Exception as exc:
        return None, f"load error: {exc}"


def _eval_value_expr(value_expr: str, data: Any) -> tuple[Any, str]:
    """Evaluate value_expr against data bound as `d`. Returns (value, error)."""
    locals_ = {"d": data, **_SAFE_BUILTINS}
    try:
        return eval(value_expr, _SAFE_GLOBALS, locals_), ""
    except Exception as exc:
        return None, f"eval error: {exc}"


def _looks_like_rate(value_expr: str, expected: Any) -> bool:
    """
    Heuristic: does this claim's value_expr produce a rate / percentage /
    proportion as opposed to an integer count?

    Conservative: we want to skip count claims (where N is exact and the
    adequacy question does not apply).
    """
    if expected is None:
        return False
    expr = value_expr or ""

    # Strong rate indicators in the expression itself.
    rate_markers = (
        "rate", "_pct", "fraction", "ratio", "_rho", "rho_",
        "proportion", "prob", "frequency", "_freq",
        "* 100", "*100", "/ 100", "/100",
        "pass_b", "pass_a", "pass_both", "pass_rate",
        "failure_rate", "improvement_ratio",
    )
    expr_lower = expr.lower()
    if any(m in expr_lower for m in rate_markers):
        return True

    # Otherwise treat as count (skip).
    return False


def _infer_n(data: Any, value_expr: str) -> tuple[int | None, str]:
    """
    Try to infer the denominator N for a rate-like claim.

    Strategy, in priority order:
      1. If the value_expr references a specific tier (e.g. d['by_tier']['high']),
         use that tier's n_trials.
      2. If it uses a comprehension over d['results'] with a tier filter,
         count the matching records.
      3. _meta.n_trials_per_cell, _meta.parameters.n_trials_per_cell.
      4. aggregate.stats[any].n_trials (per-tier N, calibration shape).
      5. summary[any].n_trials (json_nl shape).
      6. parameters.n_trials, top-level n_trials.
      7. _meta.n_trials (grand total; used only as last resort).
      8. len(d['results']) or len(d['data']).
    """
    if data is None:
        return None, "data unavailable"

    expr = value_expr or ""

    # --- 1. by_tier['<tier>'] form ---
    if isinstance(data, dict) and "by_tier" in data and isinstance(data["by_tier"], dict):
        for tier_key in data["by_tier"]:
            needle_a = f"by_tier']['{tier_key}'"
            needle_b = f'by_tier"]["{tier_key}"'
            if needle_a in expr or needle_b in expr:
                cell = data["by_tier"][tier_key]
                if isinstance(cell, dict) and "n_trials" in cell:
                    return int(cell["n_trials"]), f"by_tier['{tier_key}'].n_trials"

    # --- 2. comprehension over d['results'] with a tier filter ---
    if isinstance(data, dict) and "results" in data and isinstance(data["results"], list):
        results = data["results"]
        # try to extract a tier filter pattern: r['tier']=='<x>'
        import re as _re

        # Accept any single-token variable name (r, x, t, etc.) as the loop var.
        tier_match = _re.search(r"\w+\[['\"]tier['\"]\]\s*==\s*['\"]([\w-]+)['\"]", expr)
        model_match = _re.search(r"\w+\[['\"]model['\"]\]\s*==\s*['\"]([\w.\-/+]+)['\"]", expr)
        if tier_match or model_match:
            n = 0
            for r in results:
                if not isinstance(r, dict):
                    continue
                if tier_match and r.get("tier") != tier_match.group(1):
                    continue
                if model_match and r.get("model") != model_match.group(1):
                    continue
                n += 1
            if n > 0:
                tag_parts = []
                if model_match:
                    tag_parts.append(f"model={model_match.group(1)}")
                if tier_match:
                    tag_parts.append(f"tier={tier_match.group(1)}")
                return n, f"count(results | {', '.join(tag_parts)})"

    # --- pooled_per_tier[<tier>].n form ---
    if isinstance(data, dict) and isinstance(data.get("pooled_per_tier"), dict):
        for tier_key, cell in data["pooled_per_tier"].items():
            if not isinstance(cell, dict):
                continue
            needle_a = f"'{tier_key}'"
            needle_b = f'"{tier_key}"'
            if needle_a in expr or needle_b in expr:
                if "n" in cell:
                    return int(cell["n"]), f"pooled_per_tier['{tier_key}'].n"

    # --- 3. _meta.n_trials_per_cell ---
    if isinstance(data, dict) and isinstance(data.get("_meta"), dict):
        meta = data["_meta"]
        if "n_trials_per_cell" in meta:
            return int(meta["n_trials_per_cell"]), "_meta.n_trials_per_cell"
        if isinstance(meta.get("parameters"), dict) and "n_trials_per_cell" in meta["parameters"]:
            return int(meta["parameters"]["n_trials_per_cell"]), "_meta.parameters.n_trials_per_cell"

    # --- 4. aggregate.stats[any].n_trials ---
    if isinstance(data, dict) and isinstance(data.get("aggregate"), dict):
        stats = data["aggregate"].get("stats")
        if isinstance(stats, dict):
            tier_ns = [v.get("n_trials") for v in stats.values() if isinstance(v, dict) and "n_trials" in v]
            if tier_ns:
                return int(tier_ns[0]), "aggregate.stats[tier].n_trials"

    # --- 5. summary[any].n_trials ---
    if isinstance(data, dict) and isinstance(data.get("summary"), dict):
        for v in data["summary"].values():
            if isinstance(v, dict) and "n_trials" in v:
                return int(v["n_trials"]), "summary[tier].n_trials"

    # --- 6. parameters.n_trials, top-level n_trials ---
    if isinstance(data, dict):
        params = data.get("parameters")
        if isinstance(params, dict) and "n_trials" in params:
            return int(params["n_trials"]), "parameters.n_trials"
        if "n_trials" in data:
            return int(data["n_trials"]), "n_trials"

    # --- 7. _meta.n_trials (grand total, last resort) ---
    if isinstance(data, dict) and isinstance(data.get("_meta"), dict):
        if "n_trials" in data["_meta"]:
            return int(data["_meta"]["n_trials"]), "_meta.n_trials (grand total)"

    # --- 8. len(d['results']) or len(d['data']) ---
    for key in ("results", "data"):
        if isinstance(data, dict) and isinstance(data.get(key), list) and data[key]:
            return len(data[key]), f"len(d['{key}'])"

    return None, "no N field or pattern matched"


def _is_headline(claim: dict) -> bool:
    """
    Strict heuristic: only the paper_location_hint counts. A claim is
    headline-tier only when its registered location is the abstract, a
    contribution paragraph, the falsifiable scope section, or the
    conclusion. Appendix tables and per-tier table cells do not qualify
    even when the claim text uses the word "headline" as narrative
    shorthand.
    """
    hint = (claim.get("paper_location_hint") or "").lower()
    headline_markers = (
        "abstract",
        "contribution",
        "scope_falsifiable",
        "conclusion",
    )
    return any(m in hint for m in headline_markers)


def main() -> int:
    if not TIES_JSON.exists():
        result = {
            "status": "ERROR",
            "exit_code": 2,
            "errors": [f"ties file missing: {TIES_JSON}"],
            "summary": {},
        }
        RESULTS_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"ERROR: {TIES_JSON} not found", file=sys.stderr)
        return 2

    with TIES_JSON.open(encoding="utf-8") as fh:
        ties = json.load(fh)

    claims = ties.get("claims", {})

    findings: list[dict] = []
    n_passed = 0
    n_warned = 0
    n_blocked = 0
    n_skipped_count = 0
    n_inconclusive = 0
    n_headline_block = 0

    for name, entry in claims.items():
        value_expr = entry.get("value_expr", "")
        expected = entry.get("expected")
        compare_as = entry.get("compare_as", "")

        # Skip claims that compare as int and whose expression is plainly a count.
        if not _looks_like_rate(value_expr, expected):
            n_skipped_count += 1
            continue

        source_file = entry.get("source_file", "")
        data, load_err = _load_source(source_file)

        if data is None:
            n_inconclusive += 1
            findings.append({
                "name": name,
                "verdict": "INCONCLUSIVE",
                "reason": load_err,
                "n_inferred": None,
                "headline": _is_headline(entry),
            })
            continue

        n, n_note = _infer_n(data, value_expr)
        headline = _is_headline(entry)

        if n is None:
            n_inconclusive += 1
            findings.append({
                "name": name,
                "verdict": "INCONCLUSIVE",
                "reason": n_note,
                "n_inferred": None,
                "headline": headline,
            })
            continue

        if n >= ADEQUACY_FLOOR:
            n_passed += 1
            continue

        # N below the adequacy floor.
        finding = {
            "name": name,
            "verdict": "BLOCK" if headline else "WARN",
            "reason": (
                f"inferred N={n} below adequacy floor {ADEQUACY_FLOOR}; "
                f"N source: {n_note}"
            ),
            "n_inferred": n,
            "headline": headline,
            "claim_text": entry.get("claim_text_excerpt", ""),
            "source_file": source_file,
        }
        if headline:
            n_blocked += 1
            n_headline_block += 1
        else:
            n_warned += 1
        findings.append(finding)

    summary = {
        "passed": n_passed,
        "warned": n_warned,
        "blocked": n_blocked,
        "inconclusive": n_inconclusive,
        "skipped_as_count": n_skipped_count,
        "adequacy_floor": ADEQUACY_FLOOR,
        "headline_blocks": n_headline_block,
        "advisory": True,
    }

    exit_code = 1 if n_headline_block > 0 else 0
    status = "PASS" if exit_code == 0 else "FAIL"

    payload = {
        "status": status,
        "exit_code": exit_code,
        "summary": summary,
        "findings": findings,
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"sample_size_adequacy_check: {status}")
    print(
        f"  passed={n_passed} warned={n_warned} blocked={n_blocked} "
        f"inconclusive={n_inconclusive} skipped_as_count={n_skipped_count}"
    )
    if n_warned or n_blocked:
        print("  Advisory findings (N below floor):")
        for f in findings:
            if f["verdict"] in ("WARN", "BLOCK"):
                tag = "BLOCK" if f["verdict"] == "BLOCK" else "warn "
                print(f"    [{tag}] {f['name']}: N={f['n_inferred']} ({f.get('source_file', '')})")
    print(f"Results written to {RESULTS_JSON.name}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
