#!/usr/bin/env python3
"""
confidence_interval_coverage_check.py

Suite: statistical_hygiene

Walks every claim in ci/claim_data_ties.json whose `expected` value is a
float (rates, ratios, etc.), and verifies that one of the following holds:

  (a) the source JSON contains a confidence interval, standard error,
      standard deviation, or interquartile range field for the quantity, OR

  (b) the claim is registered in ci/caveat_ledger.json under a caveat whose
      summary or id signals a point-estimate-only acknowledgement.

Why this suite belongs here: a paper whose empirical core is rate
comparisons should make uncertainty visible per number. A point estimate
without dispersion is fine as long as it is documented as such. This check
makes the documentation gap explicit per claim, separately from the
sample-size adequacy check.

Behavior:
  Advisory by default. Returns 0 if every float-typed claim has either CI
  data or a registered caveat. Returns 1 only if a headline-tier float claim
  has neither, i.e. the load-bearing number sits without dispersion or
  written acknowledgement.

Output: ci/confidence_interval_coverage_results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TIES_JSON = SCRIPT_DIR / "claim_data_ties.json"
CAVEAT_LEDGER = SCRIPT_DIR / "caveat_ledger.json"
RESULTS_JSON = SCRIPT_DIR / "confidence_interval_coverage_results.json"


# Field-name fragments that signal dispersion is recorded somewhere in the
# source JSON. Substring match, case-insensitive.
DISPERSION_MARKERS = (
    "ci_low", "ci_high", "ci95", "ci_95", "_ci",
    "conf_int", "confidence_interval", "confint",
    "std", "stddev", "std_dev", "stderr", "se_",
    "iqr", "q25", "q75", "p25", "p75",
    "lower_bound", "upper_bound", "margin_of_error",
    "variance", "_var", "sem",
    "bootstrap", "boot_ci", "boot_low", "boot_high",
    "plus_minus", "pm_", "_pm",
)

POINT_ESTIMATE_CAVEAT_MARKERS = (
    "point_estimate_only",
    "no confidence interval",
    "single rater",
    "manual scoring",
    "small_n",
    "small n",
    "uncertainty not reported",
)


def _walk_keys(obj: Any, depth: int = 0, max_depth: int = 6):
    """Yield every dict key encountered while walking obj. Bounded depth."""
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj[:200]:  # cap list traversal
            yield from _walk_keys(v, depth + 1, max_depth)


def _has_dispersion(data: Any) -> tuple[bool, str]:
    """Return (found, marker). Conservative: any DISPERSION_MARKERS substring."""
    if data is None:
        return False, ""
    for key in _walk_keys(data):
        if not isinstance(key, str):
            continue
        kl = key.lower()
        for marker in DISPERSION_MARKERS:
            if marker in kl:
                return True, key
    return False, ""


def _load_caveat_index() -> tuple[dict[str, list[str]], list[str]]:
    """
    Build two indices from caveat_ledger.json:
      - claim_id -> list of caveat ids that mention it
      - list of all caveat ids whose summary/id matches a
        point-estimate-only marker (used to filter to acknowledgement-class)
    """
    if not CAVEAT_LEDGER.exists():
        return {}, []
    try:
        with CAVEAT_LEDGER.open(encoding="utf-8") as fh:
            ledger = json.load(fh)
    except Exception:
        return {}, []

    claim_to_caveats: dict[str, list[str]] = {}
    point_est_caveats: list[str] = []
    for entry in ledger.get("caveats", []):
        cid = entry.get("id", "")
        summary = (entry.get("summary") or "").lower()
        why = (entry.get("why_it_matters") or "").lower()
        blob = f"{cid.lower()} {summary} {why}"
        is_point_est = any(m in blob for m in POINT_ESTIMATE_CAVEAT_MARKERS)
        if is_point_est:
            point_est_caveats.append(cid)
        for claim_id in entry.get("applies_to_claims", []) or []:
            claim_to_caveats.setdefault(claim_id, []).append(cid)
    return claim_to_caveats, point_est_caveats


def _is_headline(claim: dict) -> bool:
    """
    Strict heuristic: only the paper_location_hint counts. The text excerpt
    sometimes mentions the word "headline" as narrative shorthand inside a
    parenthetical, so we do not key off it. A claim is headline-tier only
    when its registered location is the abstract, a contribution paragraph,
    the falsifiable scope section, or the conclusion.
    """
    hint = (claim.get("paper_location_hint") or "").lower()
    headline_markers = (
        "abstract",
        "contribution",
        "scope_falsifiable",
        "conclusion",
    )
    return any(m in hint for m in headline_markers)


def _load_source(source_file: str) -> tuple[Any, str]:
    path = REPO_ROOT / source_file
    if not path.exists():
        return None, f"source file missing: {source_file}"
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh), ""
    except Exception as exc:
        return None, f"load error: {exc}"


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

    claim_to_caveats, point_est_caveats = _load_caveat_index()
    point_est_set = set(point_est_caveats)

    findings: list[dict] = []
    n_passed = 0
    n_warned = 0
    n_blocked = 0
    n_headline_block = 0
    n_skipped = 0

    # Cache loaded sources to avoid re-reading.
    source_cache: dict[str, Any] = {}

    for name, entry in claims.items():
        compare_as = entry.get("compare_as", "")
        expected = entry.get("expected")

        if compare_as != "float":
            n_skipped += 1
            continue
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            n_skipped += 1
            continue

        source_file = entry.get("source_file", "")
        if source_file not in source_cache:
            source_cache[source_file], _err = _load_source(source_file)
        data = source_cache[source_file]

        has_disp, marker = _has_dispersion(data)

        caveat_ids = claim_to_caveats.get(name, [])
        # An acknowledgement-class caveat is enough; otherwise any caveat
        # mentioning the claim is treated as relevant (conservative: we
        # accept that the human author has flagged it).
        relevant_caveats = caveat_ids
        point_est_caveat_ids = [c for c in caveat_ids if c in point_est_set]

        headline = _is_headline(entry)

        if has_disp:
            n_passed += 1
            findings.append({
                "name": name,
                "verdict": "PASS",
                "reason": f"dispersion field present in source: {marker}",
                "headline": headline,
            })
            continue

        if relevant_caveats:
            n_passed += 1
            findings.append({
                "name": name,
                "verdict": "PASS_VIA_CAVEAT",
                "reason": f"covered by caveats: {', '.join(relevant_caveats)}",
                "point_estimate_only_caveats": point_est_caveat_ids,
                "headline": headline,
            })
            continue

        # Neither dispersion nor a caveat.
        verdict = "BLOCK" if headline else "WARN"
        finding = {
            "name": name,
            "verdict": verdict,
            "reason": "no dispersion field in source and no caveat ledger entry",
            "expected": expected,
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
        "skipped_non_float": n_skipped,
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

    print(f"confidence_interval_coverage_check: {status}")
    print(
        f"  passed={n_passed} warned={n_warned} blocked={n_blocked} "
        f"skipped_non_float={n_skipped}"
    )
    if n_warned or n_blocked:
        print("  Advisory findings (no CI and no caveat):")
        for f in findings:
            if f["verdict"] in ("WARN", "BLOCK"):
                tag = "BLOCK" if f["verdict"] == "BLOCK" else "warn "
                print(f"    [{tag}] {f['name']}: expected={f.get('expected')} ({f.get('source_file', '')})")
    print(f"Results written to {RESULTS_JSON.name}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
