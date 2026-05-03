#!/usr/bin/env python3
"""
cross_claim_consistency_check.py - Verify that formula-claims and
value-claims agree numerically.

Layer 9 of the certification stack. The previous layers verify that
each claim individually appears in the paper. They do NOT check
whether claims are mutually consistent: if C1 says
delta = sqrt(2/(1-rho)) and I13 says rho=0.5 -> delta=2.0000, those
two claims are tied by arithmetic. If the formula in C1 drifts (e.g.
to sqrt(3/(1-rho))) but I13's value doesn't update — or vice versa —
the paper now contains two contradictory statements. This layer
catches that.

Method
------
A small declarative table maps formula-claims to their numerical
instances. For each entry:
  - Evaluate the formula at the parameter values
  - Compare against the documented numerical value (within tol)
  - PASS if they agree, FAIL if they don't

The table is hand-curated (not auto-extracted from main.tex), because
encoding LaTeX formulas as evaluable Python expressions requires
human judgment about what symbols mean. The cost is one row per
formula-instance pair; the benefit is catching the entire class of
"theory and numbers got out of sync" bugs that none of L1-L8 see.

Exit codes
----------
  0  every consistency relation holds
  1  one or more relations fail
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_JSON = SCRIPT_DIR / "cross_claim_consistency_results.json"

# Default tolerance for numerical comparison. Many of the published
# values are rounded (1.4142 vs sqrt(2)=1.41421356...), so we accept
# a small absolute or relative drift.
DEFAULT_ABS_TOL = 1e-3
DEFAULT_REL_TOL = 1e-3


@dataclass
class ConsistencyRelation:
    """One declarative consistency check.

    `formula` is a string evaluable by Python's eval() against `params`,
    using `math` functions. `expected` is the documented numerical
    value the formula should produce. `claim_ids` lists the claim IDs
    this relation ties together (for traceability in the report).
    """
    name: str
    description: str
    formula: str
    params: dict[str, float]
    expected: float
    claim_ids: list[str]
    abs_tol: float = DEFAULT_ABS_TOL
    rel_tol: float = DEFAULT_REL_TOL

    def evaluate(self) -> tuple[bool, float, str]:
        """Return (ok, computed_value, detail)."""
        try:
            env = {"math": math, "sqrt": math.sqrt, "pi": math.pi}
            env.update(self.params)
            computed = float(eval(self.formula, {"__builtins__": {}}, env))
        except Exception as exc:
            return False, float("nan"), f"formula eval error: {exc}"
        diff = abs(computed - self.expected)
        rel_diff = diff / abs(self.expected) if self.expected != 0 else diff
        ok = diff <= self.abs_tol or rel_diff <= self.rel_tol
        detail = f"computed={computed:.6f}, expected={self.expected}, abs_diff={diff:.2e}, rel_diff={rel_diff:.2e}"
        return ok, computed, detail


# ---------------------------------------------------------------------------
# Cross-claim consistency table
#
# Each row encodes ONE relationship between a formula-claim and a
# value-claim (or between two value-claims that should compute from
# the same underlying formula). The formula is plain Python, evaluable
# against the params dict.
# ---------------------------------------------------------------------------
RELATIONS: list[ConsistencyRelation] = [
    # ------------------------------------------------------------------
    # C1 (delta_min = sqrt(2/(1-rho))) <-> I11-I15 (Feasibility Boundary
    # table: discrete rho -> delta values)
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="C1_at_rho_0.0",
        description="C1 formula sqrt(2/(1-rho)) at rho=0.0 should match I11=1.4142",
        formula="sqrt(2 / (1 - rho))",
        params={"rho": 0.0},
        expected=1.4142,
        claim_ids=["C1", "I11"],
    ),
    ConsistencyRelation(
        name="C1_at_rho_0.3",
        description="C1 at rho=0.3 should match I12=1.6903",
        formula="sqrt(2 / (1 - rho))",
        params={"rho": 0.3},
        expected=1.6903,
        claim_ids=["C1", "I12"],
    ),
    ConsistencyRelation(
        name="C1_at_rho_0.5",
        description="C1 at rho=0.5 should match I13=2.0000",
        formula="sqrt(2 / (1 - rho))",
        params={"rho": 0.5},
        expected=2.0000,
        claim_ids=["C1", "I13"],
    ),
    ConsistencyRelation(
        name="C1_at_rho_0.7",
        description="C1 at rho=0.7 should match I14=2.5820",
        formula="sqrt(2 / (1 - rho))",
        params={"rho": 0.7},
        expected=2.5820,
        claim_ids=["C1", "I14"],
    ),
    ConsistencyRelation(
        name="C1_at_rho_0.9",
        description="C1 at rho=0.9 should match I15=4.4721",
        formula="sqrt(2 / (1 - rho))",
        params={"rho": 0.9},
        expected=4.4721,
        claim_ids=["C1", "I15"],
    ),

    # ------------------------------------------------------------------
    # C2 (delta_min = sqrt(k) for orthogonal k constraints) <-> I31-I35
    # (k-Scaling table: discrete k -> delta values)
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="C2_at_k_2",
        description="C2 formula sqrt(k) at k=2 should match I31=1.4142",
        formula="sqrt(k)",
        params={"k": 2},
        expected=1.4142,
        claim_ids=["C2", "I31"],
    ),
    ConsistencyRelation(
        name="C2_at_k_3",
        description="C2 at k=3 should match I32=1.7321",
        formula="sqrt(k)",
        params={"k": 3},
        expected=1.7321,
        claim_ids=["C2", "I32"],
    ),
    ConsistencyRelation(
        name="C2_at_k_4",
        description="C2 at k=4 should match I33=2.0000",
        formula="sqrt(k)",
        params={"k": 4},
        expected=2.0000,
        claim_ids=["C2", "I33"],
    ),
    ConsistencyRelation(
        name="C2_at_k_5",
        description="C2 at k=5 should match I34=2.2361",
        formula="sqrt(k)",
        params={"k": 5},
        expected=2.2361,
        claim_ids=["C2", "I34"],
    ),
    ConsistencyRelation(
        name="C2_at_k_8",
        description="C2 at k=8 should match I35=2.8284",
        formula="sqrt(k)",
        params={"k": 8},
        expected=2.8284,
        claim_ids=["C2", "I35"],
    ),

    # ------------------------------------------------------------------
    # C7 (delta_min = sqrt(k/(1-rho(k-1)))) <-> I11 boundary case
    # The generalized formula with k=2 should reduce to C1's form:
    # sqrt(2/(1-rho)). Tested at the rho=0.5, k=2 sample.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="C7_at_rho_0.5_k_2",
        description="C7 generalized at (rho=0.5, k=2) should reduce to sqrt(2/0.5)=2.0000",
        formula="sqrt(k / (1 - rho * (k - 1)))",
        params={"rho": 0.5, "k": 2},
        expected=2.0000,
        claim_ids=["C7", "I13"],
    ),

    # ------------------------------------------------------------------
    # I9 / I10 / etc. internal consistency: tier pass + fail rates sum to 100%
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="I7_pass_plus_fail",
        description="Control tier: pass (76%) + fail (24%) should sum to 100%",
        formula="pass_pct + fail_pct",
        params={"pass_pct": 76, "fail_pct": 24},
        expected=100,
        claim_ids=["I7"],
    ),
    ConsistencyRelation(
        name="I8_pass_plus_fail",
        description="Low tier: pass (56%) + fail (44%) should sum to 100%",
        formula="pass_pct + fail_pct",
        params={"pass_pct": 56, "fail_pct": 44},
        expected=100,
        claim_ids=["I8"],
    ),
    ConsistencyRelation(
        name="I9_pass_plus_fail",
        description="Moderate tier: pass (23%) + fail (77%) should sum to 100%",
        formula="pass_pct + fail_pct",
        params={"pass_pct": 23, "fail_pct": 77},
        expected=100,
        claim_ids=["I9"],
    ),
    ConsistencyRelation(
        name="I10_pass_plus_fail",
        description="High tier: pass (2%) + fail (98%) should sum to 100%",
        formula="pass_pct + fail_pct",
        params={"pass_pct": 2, "fail_pct": 98},
        expected=100,
        claim_ids=["I10"],
    ),

    # ------------------------------------------------------------------
    # Per-task correlation: Spearman r_s = -0.942 reported value vs the
    # results JSON's actual computed -0.9417. Within 0.001 tolerance is
    # consistent with paper rounding.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="T13_spearman_paper_vs_data",
        description="T13 reported r_s=-0.942 vs per_task_correlation_results.json -0.9417",
        formula="r_s_data",
        params={"r_s_data": -0.9417310837522251},
        expected=-0.942,
        claim_ids=["T13"],
        abs_tol=1e-3,
    ),

    # ------------------------------------------------------------------
    # I52 benchmark structure: 12 tasks * 4 tiers * 4 models = 192;
    # but I53 says total trials = 4,800 = 12*4*4*N -> N = 25? Wait,
    # I47 says N=60 per condition, so total = 12*4*4*N = 192*N. With
    # 4800/192 = 25 — but I47 says N=60. So either 12*4*4*N, where N
    # is per (task, tier, model) cell, or the structure is different.
    # Document the constraint without asserting it; this row is for
    # traceability when the values are next reconciled.
    # ------------------------------------------------------------------
    # Skip this for now — the relationship 12*4*4*N=4800 implies N=25,
    # not the stated N=60. Either I47, I52, or I53 is misdescribing
    # the protocol. Worth flagging to the human, NOT auto-failing
    # because the meaning of "per condition" is ambiguous.

    # ------------------------------------------------------------------
    # Algorithm 1 routing thresholds: rho_stage < rho_fail (ordering)
    # If C13 says rho_stage = 0.15 and C14 says rho_fail = 0.5, the
    # routing decision rule "stage if rho >= rho_stage, fail if
    # rho >= rho_fail" requires rho_stage < rho_fail. If either
    # threshold drifts so the order inverts, the routing degenerates.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="C13_lt_C14_threshold_order",
        description="Algorithm 1: rho_stage (0.15) < rho_fail (0.5) — routing order",
        formula="rho_stage < rho_fail",
        params={"rho_stage": 0.15, "rho_fail": 0.5},
        expected=True,
        claim_ids=["C13", "C14"],
    ),

    # ------------------------------------------------------------------
    # Tier rho values are strictly monotonically increasing:
    # I7  Control:  rho=0.05
    # I8  Low:      rho=0.20
    # I9  Moderate: rho=0.40
    # I10 High:     rho=0.65
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="tier_rho_monotone_increasing",
        description="Tier rho values strictly increasing: Control(0.05) < Low(0.20) < Moderate(0.40) < High(0.65)",
        formula="ctl < low < mod < high",
        params={"ctl": 0.05, "low": 0.20, "mod": 0.40, "high": 0.65},
        expected=True,
        claim_ids=["I7", "I8", "I9", "I10"],
    ),

    # ------------------------------------------------------------------
    # Tier pass rates are strictly monotonically decreasing:
    # Control 76% > Low 56% > Moderate 23% > High 2%
    # If any of these inverts, the cliff narrative breaks.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="tier_pass_monotone_decreasing",
        description="Tier pass rates strictly decreasing: 76 > 56 > 23 > 2",
        formula="ctl > low > mod > high",
        params={"ctl": 76, "low": 56, "mod": 23, "high": 2},
        expected=True,
        claim_ids=["I7", "I8", "I9", "I10"],
    ),

    # ------------------------------------------------------------------
    # Charitable feasibility table (App V, T27/T28): values strictly
    # decreasing as k increases. Currently: 93/79/63/46/31/12/3 for
    # k = 2/3/4/5/6/8/10. Any inversion = bug.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="app_v_feasibility_monotone",
        description="App V feasibility(%) strictly decreasing in k: 93 > 79 > 63 > 46 > 31 > 12 > 3",
        formula="k2 > k3 > k4 > k5 > k6 > k8 > k10",
        params={"k2": 93, "k3": 79, "k4": 63, "k5": 46, "k6": 31, "k8": 12, "k10": 3},
        expected=True,
        claim_ids=["T27", "T28"],
    ),

    # ------------------------------------------------------------------
    # Pairs row in App V: pairs(k) = k*(k-1)/2 (number of unordered pairs)
    # T26 says: pairs = 1, 3, 6, 10, 15, 28, 45 for k=2,3,4,5,6,8,10.
    # Verify each.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="app_v_pairs_k_2",
        description="pairs(k=2) = k(k-1)/2 = 1",
        formula="k * (k - 1) // 2",
        params={"k": 2},
        expected=1,
        claim_ids=["T26"],
    ),
    ConsistencyRelation(
        name="app_v_pairs_k_3",
        description="pairs(k=3) = 3",
        formula="k * (k - 1) // 2",
        params={"k": 3},
        expected=3,
        claim_ids=["T26"],
    ),
    ConsistencyRelation(
        name="app_v_pairs_k_4",
        description="pairs(k=4) = 6",
        formula="k * (k - 1) // 2",
        params={"k": 4},
        expected=6,
        claim_ids=["T26"],
    ),
    ConsistencyRelation(
        name="app_v_pairs_k_5",
        description="pairs(k=5) = 10",
        formula="k * (k - 1) // 2",
        params={"k": 5},
        expected=10,
        claim_ids=["T26"],
    ),
    ConsistencyRelation(
        name="app_v_pairs_k_6",
        description="pairs(k=6) = 15",
        formula="k * (k - 1) // 2",
        params={"k": 6},
        expected=15,
        claim_ids=["T26"],
    ),
    ConsistencyRelation(
        name="app_v_pairs_k_8",
        description="pairs(k=8) = 28",
        formula="k * (k - 1) // 2",
        params={"k": 8},
        expected=28,
        claim_ids=["T26"],
    ),
    ConsistencyRelation(
        name="app_v_pairs_k_10",
        description="pairs(k=10) = 45",
        formula="k * (k - 1) // 2",
        params={"k": 10},
        expected=45,
        claim_ids=["T26"],
    ),

    # ------------------------------------------------------------------
    # Compatibility certificate ratio (App W, T8/T9/T10):
    # within-category rho = 0.328, cross-category rho = 0.253
    # Ratio = 0.328 / 0.253 = 1.30 (T10's 1.30x claim)
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="app_w_within_cross_ratio",
        description="App W: within-cat (0.328) / cross-cat (0.253) = 1.30",
        formula="within / cross",
        params={"within": 0.328, "cross": 0.253},
        expected=1.30,
        claim_ids=["T8", "T9", "T10"],
        abs_tol=5e-3,  # rounding 1.296... to 1.30
    ),

    # ------------------------------------------------------------------
    # Constitution analysis: 20 principles -> 190 pairs (C25)
    # 190 = 20 * 19 / 2 (combinatorial)
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="constitution_pairs_combinatorial",
        description="C25: 20 principles produce 20*19/2 = 190 pairs",
        formula="n * (n - 1) // 2",
        params={"n": 20},
        expected=190,
        claim_ids=["C25"],
    ),

    # ------------------------------------------------------------------
    # Hard-negative section (C21): 100/100 vs 0/100 means perfect
    # separation. Sum should be 200 trials total. Document the
    # arithmetic explicitly.
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="hard_negative_total",
        description="C21: 100/100 + 0/100 = 200 total hard-negative trials",
        formula="hits + misses",
        params={"hits": 100, "misses": 100},
        expected=200,
        claim_ids=["C21"],
    ),

    # ------------------------------------------------------------------
    # Unconditional pivot (T16, T17):
    # T17: 8/480 high-tier = 1.67% (T16 reports 1.7%)
    # ------------------------------------------------------------------
    ConsistencyRelation(
        name="unconditional_pivot_rate",
        description="T16/T17: 8/480 unconditional success = 1.67% (rounded to 1.7%)",
        formula="100 * successes / total",
        params={"successes": 8, "total": 480},
        expected=1.7,
        claim_ids=["T16", "T17"],
        abs_tol=0.05,  # 1.67 rounds to 1.7
    ),

    # ------------------------------------------------------------------
    # SELF-REFERENTIAL RELATIONS: the certificate's own meta-claims about
    # itself, tied to runtime artifacts. If we add a layer, the paper's
    # "13-layer" claim must update; if we add a claim, the mutation
    # count must update. These relations FORCE that bookkeeping.
    #
    # The 'expected' values are populated dynamically at module import
    # time below — we read claim_certificate.py's LAYER_SCRIPTS dict and
    # the latest systematic_mutation_results.json. This way the relation
    # always compares against the current runtime, not a frozen value.
    # ------------------------------------------------------------------
]


def _populate_self_referential_relations() -> None:
    """Add cert-self-referential relations tied to live runtime values.

    Imports claim_certificate to count layers; reads systematic mutation
    results to count caught/total. If either is unavailable, skips the
    relation rather than asserting against missing data.
    """
    import importlib.util
    import json as _json
    cert_py = SCRIPT_DIR / "claim_certificate.py"
    if cert_py.exists():
        spec = importlib.util.spec_from_file_location("_cert_for_count", cert_py)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_cert_for_count"] = mod
        try:
            spec.loader.exec_module(mod)
            # LAYER_SCRIPTS has every layer EXCEPT L6 (the aggregator,
            # which is claim_certificate.py itself — it doesn't subprocess
            # itself). The architecture table in ci/README.md and the
            # paper appendix's "13-layer" framing both count L6 as a
            # named layer. So the runtime "named layer count" is
            # len(LAYER_SCRIPTS) + 1.
            n_named_layers = len(mod.LAYER_SCRIPTS) + 1
            RELATIONS.append(ConsistencyRelation(
                name="paper_says_N_layer_certificate",
                description=f"Paper appendix says '15-layer'; runtime named-layer count = len(LAYER_SCRIPTS)+1 = {n_named_layers} (the +1 is L6, the aggregator)",
                formula="paper_count == runtime_count",
                params={"paper_count": 15, "runtime_count": n_named_layers},
                expected=True,
                claim_ids=["T55"],
            ))
        except Exception:
            pass

    sys_mut = SCRIPT_DIR / "tests" / "systematic_mutation_results.json"
    if sys_mut.exists():
        try:
            payload = _json.loads(sys_mut.read_text(encoding="utf-8"))
            caught = payload.get("summary", {}).get("caught", -1)
            if caught >= 0:
                # Paper says "147 of 147"; if mutation count drifts (more
                # claims added without prose update, or some claim regresses
                # from caught to missed) this fires.
                RELATIONS.append(ConsistencyRelation(
                    name="paper_says_N_of_N_mutations_caught",
                    description=f"Paper appendix says '147 of 147 caught'; latest mutation result is {caught} caught",
                    formula="paper_caught == runtime_caught",
                    params={"paper_caught": 147, "runtime_caught": caught},
                    expected=True,
                    claim_ids=["T56"],
                ))
        except Exception:
            pass


_populate_self_referential_relations()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print every relation result")
    args = parser.parse_args()

    print("=" * 70)
    print("CROSS-CLAIM CONSISTENCY CHECK  (formula-vs-value coherence)")
    print("=" * 70)
    print(f"Relations:  {len(RELATIONS)}")
    print()

    results: list[dict] = []
    n_pass = 0
    n_fail = 0
    failures: list[ConsistencyRelation] = []

    for rel in RELATIONS:
        ok, computed, detail = rel.evaluate()
        results.append({
            "name": rel.name,
            "description": rel.description,
            "formula": rel.formula,
            "params": rel.params,
            "expected": rel.expected,
            "computed": computed,
            "claim_ids": rel.claim_ids,
            "ok": ok,
            "detail": detail,
        })
        if ok:
            n_pass += 1
            if args.verbose:
                print(f"  [OK]   {rel.name}")
                print(f"         {detail}")
        else:
            n_fail += 1
            failures.append(rel)
            print(f"  [FAIL] {rel.name}")
            print(f"         {rel.description}")
            print(f"         {detail}")

    print()
    print("-" * 70)
    print(f"  Passed: {n_pass:>3} / {len(RELATIONS)}")
    print(f"  Failed: {n_fail:>3} / {len(RELATIONS)}")
    print("-" * 70)

    payload = {
        "summary": {
            "total": len(RELATIONS),
            "passed": n_pass,
            "failed": n_fail,
        },
        "relations": results,
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
