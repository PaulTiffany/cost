#!/usr/bin/env python3
"""
numerical_bound_check.py - L29 of the certification stack.

Mechanical numerical verification of the lower-bound formulas stated in
the paper for ``min ||d|| subject to <g_i, d> <= -tau_i for each i''.
For each registered bound, we (a) sample many random parameter
configurations from the bound's stated domain, (b) numerically solve
the constrained QP via scipy.optimize.minimize (SLSQP), (c) check the
paper's claim ``||d_min|| >= f(params)'' against the numerical solution
within a relative tolerance, and (d) record any counter-examples.

Why this layer exists
---------------------
The paper survived an LLM-agent BIS audit, but Google PAT earlier
flagged a real math bug in `thm:gram`: the substitution
``m = min_i m_i'' is NOT a valid lower bound on min-norm under
heterogeneous magnitudes. Both my upstream agent A and the BIS pass
initially missed the bug; a later agent M2 caught it by hand. This
layer makes that class of error mechanically detectable: counter-example
search is the unambiguous arbiter of ``is f(params) a true lower bound
on this constrained QP?''. We seed the registry with the post-fix forms
of every load-bearing bound, AND a deliberately-broken H1 entry that
re-creates PAT's original critique (so the cert keeps a permanent
demonstration of the bug class we now defend against).

Convention used throughout
--------------------------
Constraints have the form ``<g_i, d> <= -tau_i'' with g_i = m_i * u_i
(m_i > 0, ||u_i|| = 1). The ``conflict'' parameter rho is the magnitude
of the off-diagonal in the unit-vector Gram with sign convention
<u_i, u_j> = -rho_ij (gradients point apart -> positive ``conflict'').
With that convention the paper's k=2 formula reads
``alpha_i^2 + alpha_j^2 + 2 rho alpha_i alpha_j'' (the +2 sign matches
the inverse of [[1,-rho],[-rho,1]]; verified algebraically).

Exit codes
----------
  0  every bound's PASS gate held
  1  one or more bounds violated the gate
  2  invocation error (scipy missing, etc.)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import numpy as np
    from scipy.optimize import minimize
except ImportError as e:  # pragma: no cover - scipy is project-wide
    print(f"ERROR: numpy/scipy required ({e}); add to environment.", file=sys.stderr)
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_JSON = SCRIPT_DIR / "numerical_bound_results.json"


# ---------------------------------------------------------------------------
# Min-norm QP solver
# ---------------------------------------------------------------------------
def solve_min_norm(constraints: list[tuple[np.ndarray, float]], dim: int,
                   x0: np.ndarray | None = None) -> tuple[np.ndarray | None, bool, float]:
    """Solve   min ||d||^2   s.t.   <g_i, d> <= -tau_i for each i.

    Returns (d_optimal, converged, ||d||). d_optimal is None if the solver
    did not converge. We seed x0 from a least-squares closed-form attempt
    so SLSQP starts inside or near the feasible set, which dramatically
    improves convergence on near-singular Gram matrices.
    """
    G = np.array([g for g, _ in constraints], dtype=float)  # (k, dim)
    t = np.array([tau for _, tau in constraints], dtype=float)  # (k,)

    # Closed-form active-set seed: assume all constraints active, solve
    # G d = -t in the least-norm sense. d* = -G^+ t = -G^T (G G^T)^-1 t
    # if G G^T invertible; else use lstsq.
    try:
        gram = G @ G.T
        d_seed = -G.T @ np.linalg.solve(gram, t)
    except np.linalg.LinAlgError:
        d_seed, *_ = np.linalg.lstsq(G, -t, rcond=None)
    if x0 is None:
        x0 = d_seed

    cons = [
        {"type": "ineq", "fun": (lambda d, g=g, tau=tau: -tau - g @ d), "jac": (lambda d, g=g: -g)}
        for g, tau in constraints
    ]
    res = minimize(
        fun=lambda d: float(d @ d),
        jac=lambda d: 2.0 * d,
        x0=x0,
        method="SLSQP",
        constraints=cons,
        options={"ftol": 1e-12, "maxiter": 300, "disp": False},
    )
    if not res.success:
        # Retry from the unconstrained zero seed (rare; sometimes helps when
        # least-squares seed lands on the wrong active set).
        res2 = minimize(
            fun=lambda d: float(d @ d),
            jac=lambda d: 2.0 * d,
            x0=np.zeros(dim),
            method="SLSQP",
            constraints=cons,
            options={"ftol": 1e-12, "maxiter": 500, "disp": False},
        )
        if res2.success and (not res.success or res2.fun < res.fun):
            res = res2
    if not res.success:
        return None, False, float("nan")
    # Verify feasibility (SLSQP can return ``success'' on slight infeasibility).
    slack = -t - G @ res.x
    if np.min(slack) < -1e-6:
        return None, False, float("nan")
    return res.x, True, float(np.linalg.norm(res.x))


# ---------------------------------------------------------------------------
# Helpers for constructing unit vectors with a given Gram
# ---------------------------------------------------------------------------
def cholesky_unit_vectors(gram: np.ndarray) -> np.ndarray:
    """Return rows = unit vectors realizing the requested Gram matrix.

    Uses Cholesky of (gram + tiny jitter). Result has dim = k.
    Raises np.linalg.LinAlgError if Gram is not PD (caller filters).
    """
    k = gram.shape[0]
    jitter = 1e-12
    L = np.linalg.cholesky(gram + jitter * np.eye(k))
    return L  # rows are the unit vectors


def uniform_conflict_gram(k: int, rho: float) -> np.ndarray:
    """Gram matrix of unit vectors with pairwise <u_i,u_j> = -rho."""
    G = -rho * np.ones((k, k)) + (1.0 + rho) * np.eye(k)
    return G


# ---------------------------------------------------------------------------
# Registered bounds
# ---------------------------------------------------------------------------
@dataclass
class Bound:
    name: str
    paper_location: str
    description: str
    sample_params: Callable[[np.random.Generator], dict]
    build_constraints: Callable[[dict], tuple[list[tuple[np.ndarray, float]], int]]
    bound_value: Callable[[dict], float]
    n_samples: int = 100
    expected_pass: bool = True
    tolerance: float = 1e-4


def make_registry() -> list[Bound]:
    bounds: list[Bound] = []

    # -----------------------------------------------------------------
    # (a) thm:main_hetero (k=2, heterogeneous m, post-fix)
    # -----------------------------------------------------------------
    def sample_hetero2(rng: np.random.Generator) -> dict:
        return {
            "m1": float(rng.uniform(0.1, 10.0)),
            "m2": float(rng.uniform(0.1, 10.0)),
            "rho": float(rng.uniform(0.0, 0.95)),
            "tau1": float(rng.uniform(0.01, 1.0)),
            "tau2": float(rng.uniform(0.01, 1.0)),
        }

    def build_hetero2(p: dict) -> tuple[list[tuple[np.ndarray, float]], int]:
        # Unit-gradient Gram with <u1,u2> = -rho (paper convention).
        gram = np.array([[1.0, -p["rho"]], [-p["rho"], 1.0]])
        U = cholesky_unit_vectors(gram)
        g1 = p["m1"] * U[0]; g2 = p["m2"] * U[1]
        return [(g1, p["tau1"]), (g2, p["tau2"])], 2

    def value_hetero2(p: dict) -> float:
        a1 = p["tau1"] / p["m1"]; a2 = p["tau2"] / p["m2"]
        rho = p["rho"]
        return math.sqrt((a1 * a1 + a2 * a2 + 2.0 * rho * a1 * a2) / (1.0 - rho * rho))

    bounds.append(Bound(
        name="thm:main_hetero_k2",
        paper_location="paper/main.tex:307-312",
        description=(
            "k=2 heterogeneous diagonal cost: ||d|| >= sqrt((a1^2+a2^2+2*rho*a1*a2)/(1-rho^2)), "
            "a_i = tau_i/m_i."
        ),
        sample_params=sample_hetero2,
        build_constraints=build_hetero2,
        bound_value=value_hetero2,
        n_samples=150,
        expected_pass=True,
    ))

    # -----------------------------------------------------------------
    # (b) cor:k_uniform (uniform conflict, uniform m)
    # -----------------------------------------------------------------
    def sample_kuniform(rng: np.random.Generator) -> dict:
        k = int(rng.choice([2, 3, 4, 5, 8]))
        # rho_max = 1/(k-1); stay strictly inside.
        rho_cap = 1.0 / (k - 1)
        rho = float(rng.uniform(0.001, 0.95 * rho_cap))
        return {
            "k": k,
            "rho": rho,
            "m": float(rng.uniform(0.1, 10.0)),
            "tau": float(rng.uniform(0.01, 1.0)),
        }

    def build_kuniform(p: dict) -> tuple[list[tuple[np.ndarray, float]], int]:
        gram = uniform_conflict_gram(p["k"], p["rho"])
        U = cholesky_unit_vectors(gram)
        cons = [(p["m"] * U[i], p["tau"]) for i in range(p["k"])]
        return cons, p["k"]

    def value_kuniform(p: dict) -> float:
        return (p["tau"] / p["m"]) * math.sqrt(p["k"] / (1.0 - p["rho"] * (p["k"] - 1)))

    bounds.append(Bound(
        name="cor:k_uniform",
        paper_location="paper/main.tex:327-329",
        description=(
            "Uniform-conflict k-bound: ||d|| >= (tau/m)*sqrt(k/(1-rho(k-1))) "
            "for k in {2,3,4,5,8}, rho < 1/(k-1)."
        ),
        sample_params=sample_kuniform,
        build_constraints=build_kuniform,
        bound_value=value_kuniform,
        n_samples=80,
        expected_pass=True,
    ))

    # -----------------------------------------------------------------
    # (c) thm:gram with hetero m, POST-FIX form ||d|| >= sqrt(alpha^T Gamma^-1 alpha)
    # -----------------------------------------------------------------
    def sample_gram_hetero(rng: np.random.Generator) -> dict:
        k = int(rng.choice([2, 3, 4, 5]))
        rho_cap = 1.0 / (k - 1)
        return {
            "k": k,
            "rho": float(rng.uniform(0.001, 0.9 * rho_cap)),
            "m": [float(rng.uniform(0.1, 10.0)) for _ in range(k)],
            "tau": float(rng.uniform(0.01, 1.0)),
        }

    def build_gram_hetero(p: dict) -> tuple[list[tuple[np.ndarray, float]], int]:
        gram = uniform_conflict_gram(p["k"], p["rho"])
        U = cholesky_unit_vectors(gram)
        cons = [(p["m"][i] * U[i], p["tau"]) for i in range(p["k"])]
        return cons, p["k"]

    def value_gram_hetero_postfix(p: dict) -> float:
        gram = uniform_conflict_gram(p["k"], p["rho"])
        alpha = np.array([p["tau"] / m_i for m_i in p["m"]])
        return math.sqrt(float(alpha @ np.linalg.solve(gram, alpha)))

    bounds.append(Bound(
        name="thm:gram_hetero_postfix",
        paper_location="paper/main.tex:319-325 (post-fix interpretation)",
        description=(
            "Heterogeneous-m k-Gram bound (post-fix): ||d|| >= sqrt(alpha^T Gamma^-1 alpha), "
            "alpha_i = tau_i/m_i. This is the form that survives PAT's critique."
        ),
        sample_params=sample_gram_hetero,
        build_constraints=build_gram_hetero,
        bound_value=value_gram_hetero_postfix,
        n_samples=100,
        expected_pass=True,
    ))

    # -----------------------------------------------------------------
    # (d) thm:gram ORIGINAL form (m = min_i m_i): permanent demonstration
    #     that the formula PAT flagged is a NOT a valid lower bound under
    #     heterogeneous m. ``expected_pass=False'' inverts the gate: this
    #     entry passes only if counter-examples are found (i.e., we can
    #     mechanically reproduce PAT's flag).
    # -----------------------------------------------------------------
    def value_gram_original_buggy(p: dict) -> float:
        # Original (broken) substitution: m -> min_i m_i. With heterogeneous
        # m_i this overstates the bound when some m_i >> m_min, because the
        # true alpha vector mixes large and small components but this
        # formula treats every constraint as having alpha = tau / m_min.
        m_min = min(p["m"])
        gram = uniform_conflict_gram(p["k"], p["rho"])
        ones = np.ones(p["k"])
        return (p["tau"] / m_min) * math.sqrt(float(ones @ np.linalg.solve(gram, ones)))

    bounds.append(Bound(
        name="thm:gram_ORIGINAL_buggy_demo",
        paper_location="paper/main.tex:319-325 (PAT's critique of the m=min substitution)",
        description=(
            "PAT's original critique: substituting m=min_i m_i into the uniform-m formula "
            "is NOT a valid lower bound under heterogeneous magnitudes. This entry passes "
            "ONLY if counter-examples are found (gate inverted). Permanent demonstration "
            "in the cert that we know why the post-fix form is necessary."
        ),
        sample_params=sample_gram_hetero,
        build_constraints=build_gram_hetero,
        bound_value=value_gram_original_buggy,
        n_samples=100,
        expected_pass=False,  # we WANT counter-examples here
    ))

    # -----------------------------------------------------------------
    # (e) app:preservation general-k: ||Delta_preserve|| = (tau/m)/sqrt(1 - h^T G_P^-1 h)
    #     with p prior unit vectors (the active preservation halfspaces) and
    #     a target unit vector u_j; cosines h_i = <u_i, u_j>. We sample a random
    #     PD Gram for the (p+1) unit vectors, build the constraints
    #     (-h_i)<=  <u_i, d> <= 0  (preservation: progress nonneg, simplified to
    #     equality at activation; we use <u_i, d> >= 0) and  <u_j, d> <= -tau/m
    #     (progress on active constraint), and compare numerical min-norm to the
    #     formula.
    # -----------------------------------------------------------------
    def sample_preservation(rng: np.random.Generator) -> dict:
        p = int(rng.choice([1, 2, 3, 4]))
        # Random PD Gram of size (p+1): start from random matrix, form A A^T,
        # then normalize diag to 1.
        k = p + 1
        # Bias toward modest off-diagonals so 1 - h^T G_P^-1 h stays comfortably
        # away from 0 (otherwise SLSQP struggles and the formula blows up).
        A = rng.normal(size=(k, k + 2)) * 0.4
        gram = A @ A.T
        d = np.sqrt(np.diag(gram))
        gram = gram / np.outer(d, d)
        return {
            "p": p,
            "gram": gram,  # (p+1, p+1); index 0..p-1 = priors, index p = active j
            "tau_over_m": float(rng.uniform(0.05, 1.0)),
        }

    def build_preservation(p: dict) -> tuple[list[tuple[np.ndarray, float]], int]:
        gram = p["gram"]; pp = p["p"]; tom = p["tau_over_m"]
        U = cholesky_unit_vectors(gram)
        # Active constraint at index pp: <u_j, d> <= -tau/m. Encode as g = u_j, tau_eff = tau/m.
        # Preservation halfspaces at active worst case: <u_i, d> = 0 for i in priors.
        # Encode equality as <u_i, d> <= 0 AND <-u_i, d> <= 0.
        cons: list[tuple[np.ndarray, float]] = []
        for i in range(pp):
            cons.append((U[i].copy(), 0.0))
            cons.append((-U[i].copy(), 0.0))
        cons.append((U[pp].copy(), tom))
        return cons, pp + 1

    def value_preservation(p: dict) -> float:
        gram = p["gram"]; pp = p["p"]; tom = p["tau_over_m"]
        if pp == 0:
            return tom
        G_P = gram[:pp, :pp]
        h = gram[:pp, pp]
        denom = 1.0 - float(h @ np.linalg.solve(G_P, h))
        if denom <= 1e-9:
            return float("nan")  # caller filters
        return tom / math.sqrt(denom)

    bounds.append(Bound(
        name="app:preservation_general_k",
        paper_location="paper/main.tex:1267-1272 (eq:pres_general)",
        description=(
            "Preservation-constrained min-norm: ||Delta_preserve|| = (tau/m)/sqrt(1 - h^T G_P^-1 h). "
            "Random PD Gram, p prior unit vectors with active preservation halfspaces (<u_i,d>=0) "
            "plus active constraint <u_j,d> <= -tau/m."
        ),
        sample_params=sample_preservation,
        build_constraints=build_preservation,
        bound_value=value_preservation,
        n_samples=80,
        expected_pass=True,
        # Equality-as-two-inequalities + near-singular Gram make SLSQP slightly
        # less crisp; loosen tolerance modestly.
        tolerance=1e-3,
    ))

    # -----------------------------------------------------------------
    # (f) prop:staging_reduces: stage-p step magnitude under uniform conflict.
    #     delta^(p) = (tau/m) * sqrt((1-rho(p-1))/((1+rho)(1-rho*p)))
    # -----------------------------------------------------------------
    def sample_staging(rng: np.random.Generator) -> dict:
        # k constraints, currently at stage p with p priors active.
        # Need rho * p < 1 (formula denominator) and rho < 1 (positive (1+rho)).
        p = int(rng.choice([1, 2, 3, 4]))
        rho_cap = 1.0 / (p + 0.001)  # strict: rho * p < 1
        rho = float(rng.uniform(0.001, 0.9 * rho_cap))
        return {
            "p": p,
            "rho": rho,
            "m": float(rng.uniform(0.1, 10.0)),
            "tau": float(rng.uniform(0.01, 1.0)),
        }

    def build_staging(p: dict) -> tuple[list[tuple[np.ndarray, float]], int]:
        # Uniform-conflict Gram on (p+1) unit vectors: priors u_1..u_p, active u_j.
        pp = p["p"]; rho = p["rho"]; m = p["m"]; tau = p["tau"]
        gram = uniform_conflict_gram(pp + 1, rho)
        U = cholesky_unit_vectors(gram)
        cons: list[tuple[np.ndarray, float]] = []
        # Preservation halfspaces (active, equality): <u_i, d> = 0 for i = 0..p-1
        for i in range(pp):
            cons.append((U[i].copy(), 0.0))
            cons.append((-U[i].copy(), 0.0))
        # Active progress: <m * u_j, d> <= -tau, i.e. <u_j, d> <= -tau/m.
        cons.append((m * U[pp].copy(), tau))
        return cons, pp + 1

    def value_staging(p: dict) -> float:
        pp = p["p"]; rho = p["rho"]; m = p["m"]; tau = p["tau"]
        num = 1.0 - rho * (pp - 1)
        den = (1.0 + rho) * (1.0 - rho * pp)
        if num <= 0 or den <= 0:
            return float("nan")
        return (tau / m) * math.sqrt(num / den)

    bounds.append(Bound(
        name="prop:staging_reduces",
        paper_location="paper/main.tex:1536-1540",
        description=(
            "Stage-p step magnitude under uniform conflict: "
            "(tau/m)*sqrt((1-rho(p-1))/((1+rho)(1-rho*p))) for p in {1,..,4}, rho*p<1."
        ),
        sample_params=sample_staging,
        build_constraints=build_staging,
        bound_value=value_staging,
        n_samples=100,
        expected_pass=True,
        tolerance=1e-3,
    ))

    return bounds


# ---------------------------------------------------------------------------
# Sampling driver
# ---------------------------------------------------------------------------
@dataclass
class BoundResult:
    name: str
    paper_location: str
    description: str
    n_samples: int
    n_converged: int
    n_skipped_invalid_formula: int = 0
    n_violations: int = 0
    max_relative_violation: float = 0.0
    expected_pass: bool = True
    passed: bool = False
    tolerance: float = 1e-4
    counter_examples: list[dict] = field(default_factory=list)


def evaluate_bound(b: Bound, rng: np.random.Generator, max_examples: int = 3) -> BoundResult:
    res = BoundResult(
        name=b.name, paper_location=b.paper_location, description=b.description,
        n_samples=b.n_samples, n_converged=0,
        expected_pass=b.expected_pass, tolerance=b.tolerance,
    )
    for _ in range(b.n_samples):
        params = b.sample_params(rng)
        try:
            cons, dim = b.build_constraints(params)
        except np.linalg.LinAlgError:
            continue  # singular Gram in sampling; skip
        d_opt, ok, norm = solve_min_norm(cons, dim)
        if not ok:
            continue
        res.n_converged += 1
        claimed = b.bound_value(params)
        if not math.isfinite(claimed):
            res.n_skipped_invalid_formula += 1
            continue
        # Bound claim: norm >= claimed. Violation: norm < claimed (by more
        # than tolerance, relative to claimed).
        rel_violation = (claimed - norm) / max(claimed, 1e-12)
        if rel_violation > b.tolerance:
            res.n_violations += 1
            if rel_violation > res.max_relative_violation:
                res.max_relative_violation = rel_violation
            if len(res.counter_examples) < max_examples:
                # Make params JSON-friendly.
                params_json = {}
                for k, v in params.items():
                    if isinstance(v, np.ndarray):
                        params_json[k] = v.tolist()
                    elif isinstance(v, list):
                        params_json[k] = v
                    else:
                        params_json[k] = v
                res.counter_examples.append({
                    "params": params_json,
                    "numerical_d_min_norm": norm,
                    "claimed_lower_bound": claimed,
                    "relative_violation": rel_violation,
                    "d_optimal_first8": d_opt[:8].tolist() if d_opt is not None else None,
                })

    # Gate logic.
    if b.expected_pass:
        # PASS = no violations among converged samples (modulo tolerance).
        res.passed = res.n_violations == 0 and res.n_converged > 0
    else:
        # Inverted gate: PASS = at least one counter-example was found
        # (i.e., we successfully demonstrate the formula is broken).
        res.passed = res.n_violations > 0

    return res


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=20260504,
                        help="RNG seed (deterministic; pinned for reproducibility)")
    parser.add_argument("--json-out", default=str(RESULTS_JSON),
                        help="path for JSON output")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print every counter-example to stdout")
    args = parser.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    registry = make_registry()

    print("=" * 70)
    print("L29 NUMERICAL BOUND CHECK")
    print("=" * 70)
    print(f"  Seed: {args.seed}")
    print(f"  Bounds in registry: {len(registry)}")
    print()

    t0 = time.time()
    results: list[BoundResult] = []
    any_blocker = False
    for b in registry:
        ts = time.time()
        r = evaluate_bound(b, rng)
        dt = time.time() - ts
        results.append(r)
        gate = "PASS" if r.passed else "FAIL"
        if not r.passed:
            any_blocker = True
        mode = "expect_violations" if not b.expected_pass else "expect_clean"
        print(f"  [{gate}] {r.name:<40}  "
              f"n={r.n_samples:<3} conv={r.n_converged:<3} "
              f"viol={r.n_violations:<3} maxRV={r.max_relative_violation:.2e}  "
              f"({mode}, {dt:.1f}s)")
        if args.verbose and r.counter_examples:
            for ce in r.counter_examples:
                print(f"     counter-example: numerical={ce['numerical_d_min_norm']:.6g}, "
                      f"claimed={ce['claimed_lower_bound']:.6g}, "
                      f"rel_viol={ce['relative_violation']:.4g}")
                print(f"        params: {ce['params']}")

    elapsed = time.time() - t0
    print()
    print("-" * 70)
    print(f"  Total time: {elapsed:.2f}s")
    print(f"  Verdict:    {'PASS' if not any_blocker else 'FAIL'}")
    print("-" * 70)

    payload = {
        "summary": {
            "total_bounds": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "elapsed_seconds": round(elapsed, 3),
            "seed": args.seed,
        },
        "bounds": [
            {
                "name": r.name,
                "paper_location": r.paper_location,
                "description": r.description,
                "n_samples": r.n_samples,
                "n_converged": r.n_converged,
                "n_skipped_invalid_formula": r.n_skipped_invalid_formula,
                "n_violations": r.n_violations,
                "max_relative_violation": r.max_relative_violation,
                "expected_pass": r.expected_pass,
                "passed": r.passed,
                "tolerance": r.tolerance,
                "counter_examples": r.counter_examples,
            }
            for r in results
        ],
    }
    Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  Results -> {args.json_out}")
    return 0 if not any_blocker else 1


if __name__ == "__main__":
    sys.exit(main())
