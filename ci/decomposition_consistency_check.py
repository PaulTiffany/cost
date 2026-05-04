#!/usr/bin/env python3
"""
decomposition_consistency_check.py — semantic relations between data tied
claims that L15 misses by checking each claim independently.

L15 catches "computed value matches expected value." It does not catch
"these N claims must arithmetically reconcile." Concretely: L15 lets both
'smooth_total = 4272' and 'total_infeasible = 4800' pass, but does not
verify that smooth_total + pivot_total == total_infeasible. When the
paper text drifts (e.g., calls 4800 'smooth-regime'), nothing fails.

This layer reads source JSONs directly and asserts inter-claim relations.
Companion to L9 (formula identity) but for data-derived decompositions.

Exit codes
----------
  0  every relation holds
  1  one or more relations fail
  2  invocation error (manifest missing)
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = SCRIPT_DIR / "decomposition_consistency_results.json"


@dataclass
class RelationResult:
    name: str
    passed: bool
    detail: str


def _load_json(rel: str):
    return json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _paper_text() -> str:
    try:
        from text_normalization import normalize_latex
    except ImportError:
        sys.path.insert(0, str(SCRIPT_DIR))
        from text_normalization import normalize_latex
    return normalize_latex(PAPER_TEX.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Relations: each is (name, lambda) -> RelationResult
# Add new relations here as decomposition invariants are discovered.
# ---------------------------------------------------------------------------

def _smooth_pivot_decomposition() -> RelationResult:
    """smooth_total + pivot_total == total_infeasible (4272 + 528 == 4800)."""
    d = _load_json("rebuttal/figures/unconditional_pivot_results.json")
    fp = d["full_paper_claim"]
    s, p, t = fp["smooth_total"], fp["pivot_total"], fp["total_infeasible"]
    ok = (s + p == t)
    return RelationResult(
        name="smooth_pivot_decomposition",
        passed=ok,
        detail=f"smooth_total={s} + pivot_total={p} {'==' if ok else '!='} total_infeasible={t}",
    )


def _smooth_zero_refutations() -> RelationResult:
    """smooth_successes must be zero (the headline falsifiability claim)."""
    d = _load_json("rebuttal/figures/unconditional_pivot_results.json")
    s = d["full_paper_claim"]["smooth_successes"]
    return RelationResult(
        name="smooth_zero_refutations",
        passed=(s == 0),
        detail=f"smooth_successes={s} (must be 0; this is the falsifiability claim)",
    )


def _pivot_estimated_42() -> RelationResult:
    """pivot_successes_estimated must be 42 (the 1.7% unconditional rate basis)."""
    d = _load_json("rebuttal/figures/unconditional_pivot_results.json")
    p = d["full_paper_claim"]["pivot_successes_estimated"]
    return RelationResult(
        name="pivot_estimated_42",
        passed=(p == 42),
        detail=f"pivot_successes_estimated={p} (must be 42)",
    )


def _smooth_regime_pct_89() -> RelationResult:
    """smooth_total / total_infeasible rounds to 89%."""
    d = _load_json("rebuttal/figures/unconditional_pivot_results.json")
    fp = d["full_paper_claim"]
    pct = round(100 * fp["smooth_total"] / fp["total_infeasible"])
    return RelationResult(
        name="smooth_regime_pct_89",
        passed=(pct == 89),
        detail=f"smooth_total/total_infeasible = {pct}% (paper says 89%)",
    )


def _pivot_regime_pct_11() -> RelationResult:
    """pivot_total / total_infeasible rounds to 11%."""
    d = _load_json("rebuttal/figures/unconditional_pivot_results.json")
    fp = d["full_paper_claim"]
    pct = round(100 * fp["pivot_total"] / fp["total_infeasible"])
    return RelationResult(
        name="pivot_regime_pct_11",
        passed=(pct == 11),
        detail=f"pivot_total/total_infeasible = {pct}% (paper says 11%)",
    )


def _no_4800_smooth_regime_in_paper() -> RelationResult:
    """If smooth_total != total_infeasible, paper must not call 4,800 'smooth-regime'.

    This is the exact failure mode that motivated this check: the L15 ties
    for both 4,272 and 4,800 passed while the paper still said
    '0/4,800 smooth-regime'. We catch that text-level drift here.
    """
    d = _load_json("rebuttal/figures/unconditional_pivot_results.json")
    fp = d["full_paper_claim"]
    if fp["smooth_total"] == fp["total_infeasible"]:
        return RelationResult(
            name="no_4800_smooth_regime_in_paper",
            passed=True,
            detail="smooth_total == total_infeasible; check skipped (no decomposition mismatch)",
        )
    text = _paper_text()
    # Look for "4,800" within ~40 chars of "smooth" / "smooth-regime"
    pat = re.compile(r"4[,\\{}\\!\s]*800[^.]{0,60}smooth[\s-]*regime|smooth[\s-]*regime[^.]{0,60}4[,\\{}\\!\s]*800", re.IGNORECASE)
    m = pat.search(text)
    if m:
        ctx = text[max(0, m.start()-30):min(len(text), m.end()+30)]
        return RelationResult(
            name="no_4800_smooth_regime_in_paper",
            passed=False,
            detail=f"paper still says '4,800 smooth-regime' but smooth_total={fp['smooth_total']} (full total_infeasible={fp['total_infeasible']}); ctx: ...{ctx!r}...",
        )
    return RelationResult(
        name="no_4800_smooth_regime_in_paper",
        passed=True,
        detail="paper does not conflate 4,800 with smooth-regime",
    )


def _claim_audit_C3_consistent() -> RelationResult:
    """Sanity-check that claim_audit.py C3 description still encodes the smooth count, not the total."""
    text = (REPO_ROOT / "ci" / "claim_audit.py").read_text(encoding="utf-8")
    # C3 description should mention 4,272 and not solely 4,800
    m = re.search(r'"id":\s*"C3".*?"description":\s*r?"([^"]+)"', text, re.DOTALL)
    if not m:
        return RelationResult(name="claim_audit_C3_consistent", passed=False, detail="could not locate C3 description in claim_audit.py")
    desc = m.group(1)
    has_4272 = "4,272" in desc or "4272" in desc
    return RelationResult(
        name="claim_audit_C3_consistent",
        passed=has_4272,
        detail=f"C3 description: {desc!r}; mentions 4,272: {has_4272}",
    )


# ---------------------------------------------------------------------------

RELATIONS: list[Callable[[], RelationResult]] = [
    _smooth_pivot_decomposition,
    _smooth_zero_refutations,
    _pivot_estimated_42,
    _smooth_regime_pct_89,
    _pivot_regime_pct_11,
    _no_4800_smooth_regime_in_paper,
    _claim_audit_C3_consistent,
]


def main() -> int:
    print("=" * 70)
    print("DECOMPOSITION CONSISTENCY CHECK (L17)")
    print("=" * 70)

    results: list[RelationResult] = []
    for rel_fn in RELATIONS:
        try:
            results.append(rel_fn())
        except Exception as e:
            results.append(RelationResult(
                name=rel_fn.__name__.lstrip("_"),
                passed=False,
                detail=f"check raised {type(e).__name__}: {e}",
            ))

    n_pass = sum(1 for r in results if r.passed)
    for r in results:
        badge = "[OK]  " if r.passed else "[FAIL]"
        print(f"  {badge} {r.name}: {r.detail}")
    print()
    print("-" * 70)
    print(f"  Passed: {n_pass} / {len(results)}")
    print("-" * 70)

    payload = {
        "summary": {"total": len(results), "passed": n_pass, "failed": len(results) - n_pass},
        "results": [asdict(r) for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON.relative_to(REPO_ROOT)}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
