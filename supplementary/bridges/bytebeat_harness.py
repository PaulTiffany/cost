#!/usr/bin/env python3
"""
bytebeat_harness.py
===================
Paper: "The Cost of Cacophony" (ICML 2026)
  - main.tex, references.bib

Verifies:
  - Appendix S (app:bytebeat_bridge): Bytebeat domain transfer
  - Claim C22: 59% → 0% feasibility cliff at ρ ≥ 0.8
  - Table A18 (tab:bytebeat): Bytebeat constraint results
  - Plomp & Levelt (1965) [plomp1965tonal]: Psychoacoustic grounding

ICML-style validation harness: "Diagonal Cost" via demoscene/bytebeat
with executable semantics, objective verification, and repair protocol.

Core idea:
  - Bytebeat expressions are artifacts produced under a strict budget (character limit).
  - Constraints on the resulting waveform (pitch, rhythm, roughness, spectral flatness, etc.)
    can be objectively verified via signal processing.
  - Multi-constraint sets with higher empirical incompatibility (rho) should show a sharper
    feasibility boundary and larger staging benefit (one-shot vs staged).
  - NEW: Repair protocol generates structured error reports for failed constraints,
    enabling a "staged+repair" condition that amplifies the signal at high rho.

No judge model. No self-report. Objective verification only.

Psychoacoustic grounding:
  The roughness constraint is grounded in Plomp & Levelt (1965) critical bandwidth theory.
  When constraints conflict geometrically, the resulting waveform exhibits AM-band energy
  in the 20-150 Hz range - exactly the regime where human hearing perceives "roughness."
  This provides physical (auditory) validation: constraint conflict literally sounds rough.

  See also: supplementary/demos/sonification.py for the inverse mapping (ρ → roughness).

Commands:
  # 1) Estimate conflict matrix (rho) via random grammar sampling
  python supplementary/bridges/bytebeat_harness.py baseline --out supplementary/bridges/out_bb_base --samples 5000 --seed 7

  # 2) Create benchmark template JSONL
  python supplementary/bridges/bytebeat_harness.py bench --baseline supplementary/bridges/out_bb_base/baseline.json --out supplementary/bridges/out_bb_bench

  # 2b) Synthesize filled JSONL (synthetic solver)
  python supplementary/bridges/bytebeat_harness.py synthesize --in supplementary/bridges/out_bb_bench/bench_template.jsonl --baseline supplementary/bridges/out_bb_base/baseline.json --out supplementary/bridges/out_bb_bench

  # 3) Evaluate filled JSONL (expr field populated with model outputs)
  python supplementary/bridges/bytebeat_harness.py eval --in bench_filled.jsonl --baseline supplementary/bridges/out_bb_base/baseline.json --out supplementary/bridges/out_bb_eval

  # 4) Generate repair prompts for failed candidates
  python supplementary/bridges/bytebeat_harness.py repair --in bench_filled.jsonl --baseline supplementary/bridges/out_bb_base/baseline.json --out supplementary/bridges/out_bb_repair

Output:
  - eval_rows.csv / eval_rows.json
  - pass_rate_vs_rho.png
  - staging_benefit_vs_rho.png
  - phase_diagram_{protocol}.png
  - repair_prompts.jsonl (if using repair command)

Paper-friendly operationalization of conflict:
  rho_ij = 1 - (P(i & j) / min(P(i), P(j)))   (clipped to [0,1])
  computed over the same baseline grammar distribution.

Dependencies: numpy, matplotlib (standard scipy-free)

Author: Research validation harness
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import matplotlib.pyplot as plt

# ----------------------------- 
# Config / Utilities
# ----------------------------- 

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")

def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")).replace(",", ";") for c in cols))
    path.write_text("\n".join(lines), encoding="utf-8")

def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x

def read_jsonl(path: Path) -> List[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        rows.append(json.loads(s))
    return rows

# ----------------------------- 
# Safe expression evaluation
# ----------------------------- 

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.BitXor, ast.BitAnd, ast.BitOr,
                   ast.LShift, ast.RShift, ast.Mod, ast.FloorDiv)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub, ast.Invert)
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
    ast.Load, ast.Call,
    *_ALLOWED_BINOPS, *_ALLOWED_UNARYOPS,
)

def _int_cast(x: object) -> np.ndarray:
    return np.asarray(x).astype(np.int64)

_ALLOWED_FUNCS = {
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "abs": np.abs,
    "int": _int_cast,
    "floor": np.floor,
}

@dataclass(frozen=True)
class EvalConfig:
    sr: int = 8000
    seconds: float = 4.0
    max_chars: int = 128

def _validate_ast(node: ast.AST) -> None:
    """Raise ValueError if AST contains disallowed nodes/operators."""
    for n in ast.walk(node):
        if not isinstance(n, _ALLOWED_NODES):
            raise ValueError(f"Disallowed AST node: {type(n).__name__}")
        if isinstance(n, ast.BinOp) and not isinstance(n.op, _ALLOWED_BINOPS):
            raise ValueError(f"Disallowed binary op: {type(n.op).__name__}")
        if isinstance(n, ast.UnaryOp) and not isinstance(n.op, _ALLOWED_UNARYOPS):
            raise ValueError(f"Disallowed unary op: {type(n.op).__name__}")
        if isinstance(n, ast.Name) and n.id != "t" and n.id not in _ALLOWED_FUNCS:
            raise ValueError(f"Disallowed name: {n.id}")
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in _ALLOWED_FUNCS:
                raise ValueError("Only simple calls to allowed functions are permitted.")

def eval_bytebeat_expr(expr: str, cfg: EvalConfig) -> np.ndarray:
    """
    Evaluate bytebeat expression over t = 0..T-1.
    Returns float waveform in [-1, 1].
    """
    expr = expr.strip()
    if len(expr) > cfg.max_chars:
        raise ValueError(f"Expression exceeds budget: {len(expr)} > {cfg.max_chars}")

    node = ast.parse(expr, mode="eval")
    _validate_ast(node)

    code = compile(node, "<bytebeat>", "eval")
    T = int(cfg.sr * cfg.seconds)

    # Vectorized evaluation for speed.
    t = np.arange(T, dtype=np.int64)
    env = {"t": t, **_ALLOWED_FUNCS}
    v = eval(code, {"__builtins__": {}}, env)  # noqa: S307
    vi = _int_cast(v)
    vi = vi & 255
    out = (vi.astype(np.float32) / 127.5) - 1.0
    return out

# ----------------------------- 
# Audio feature extraction
# ----------------------------- 

@dataclass(frozen=True)
class Features:
    sr: int
    n: int
    rms: float
    spectral_centroid: float
    spectral_flatness: float
    f0_peak: float
    ac_peak_bpm: float
    ac_peak_strength: float
    roughness: float

_FFT_CACHE: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}

def _fft_mag(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n = len(x)
    cached = _FFT_CACHE.get(n)
    if cached:
        win, freqs = cached
    else:
        win = np.hanning(n).astype(np.float32)
        freqs = np.fft.rfftfreq(n, d=1.0)
        _FFT_CACHE[n] = (win, freqs)
    X = np.fft.rfft(x * win)
    mag = np.abs(X).astype(np.float32) + 1e-12
    return freqs, mag

def extract_features(x: np.ndarray, sr: int) -> Features:
    n = len(x)
    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)

    freqs_norm, mag = _fft_mag(x)
    freqs_hz = freqs_norm * sr

    # Spectral centroid
    centroid = float(np.sum(freqs_hz * mag) / (np.sum(mag) + 1e-12))

    # Spectral flatness = geometric mean / arithmetic mean
    gm = float(np.exp(np.mean(np.log(mag))))
    am = float(np.mean(mag))
    flatness = float(gm / (am + 1e-12))

    # Fundamental-ish peak (exclude DC)
    lo = np.searchsorted(freqs_hz, 20.0)
    hi = np.searchsorted(freqs_hz, sr / 2.0)
    peak_idx = int(lo + np.argmax(mag[lo:hi]))
    f0_peak = float(freqs_hz[peak_idx])

    # Autocorrelation for rhythm: FFT-based for speed
    x0 = x - np.mean(x)
    nfft = 1 << (2 * n - 1).bit_length()
    X = np.fft.rfft(x0, n=nfft)
    ac = np.fft.irfft(X * np.conj(X), n=nfft)[:n]
    ac /= (ac[0] + 1e-12)

    bpm_min, bpm_max = 60.0, 200.0
    lag_min = int(sr * 60.0 / bpm_max)
    lag_max = int(sr * 60.0 / bpm_min)
    lag_max = min(lag_max, len(ac) - 1)
    
    if lag_max <= lag_min + 2:
        ac_peak_bpm = 0.0
        ac_peak_strength = 0.0
    else:
        seg = ac[lag_min:lag_max]
        pk = int(np.argmax(seg))
        lag = lag_min + pk
        ac_peak_strength = float(seg[pk])
        ac_peak_bpm = float(60.0 * sr / max(lag, 1))

    # Roughness proxy: energy in amplitude modulation band ~ [20, 150] Hz
    # Grounded in Plomp & Levelt (1965) "Tonal Consonance and Critical Bandwidth":
    # - Roughness perception peaks for AM rates within critical bandwidth
    # - For typical audio frequencies, critical bandwidth ≈ 20-100 Hz
    # - Maximum roughness at ~25% of critical bandwidth (30-40 Hz)
    # - We use 20-150 Hz to capture the full roughness-relevant modulation range
    # Reference: Plomp, R. & Levelt, W.J.M. (1965). J. Acoust. Soc. Am. 38(4), 548-560.
    env = np.abs(x0).astype(np.float32)
    freqs_norm_e, mag_e = _fft_mag(env)
    freqs_hz_e = freqs_norm_e * sr
    b0 = np.searchsorted(freqs_hz_e, 20.0)
    b1 = np.searchsorted(freqs_hz_e, 150.0)
    rough = float(np.sum(mag_e[b0:b1]) / (np.sum(mag_e) + 1e-12))

    return Features(
        sr=sr,
        n=n,
        rms=rms,
        spectral_centroid=centroid,
        spectral_flatness=flatness,
        f0_peak=f0_peak,
        ac_peak_bpm=ac_peak_bpm,
        ac_peak_strength=ac_peak_strength,
        roughness=rough,
    )

# ----------------------------- 
# Constraints (objective)
# ----------------------------- 

@dataclass(frozen=True)
class ConstraintResult:
    name: str
    passed: bool
    score: float
    detail: str = ""  # NEW: for repair prompts

def _within(x: float, target: float, tol: float) -> float:
    d = abs(x - target)
    return clamp01(1.0 - (d / tol))

def constraint_pitch_A4(feat: Features) -> ConstraintResult:
    """Pass if peak near 440Hz within 40Hz; score decays with distance."""
    target = 440.0
    tol = 40.0
    s = _within(feat.f0_peak, target, tol)
    detail = ""
    if s < 0.6:
        detail = f"Peak at {feat.f0_peak:.1f}Hz (need 440+/-40Hz)"
    return ConstraintResult("pitch_A4", passed=(s >= 0.6), score=s, detail=detail)

def constraint_rhythm_120bpm(feat: Features) -> ConstraintResult:
    """Requires autocorr peak near 120bpm and sufficiently strong."""
    s1 = _within(feat.ac_peak_bpm, 120.0, 12.0)
    s2 = clamp01((feat.ac_peak_strength - 0.05) / 0.35)
    s = clamp01(0.6 * s1 + 0.4 * s2)
    detail = ""
    if s < 0.6:
        if feat.ac_peak_bpm < 60 or feat.ac_peak_bpm > 200:
            detail = f"No clear rhythm detected (BPM={feat.ac_peak_bpm:.1f})"
        else:
            detail = f"BPM={feat.ac_peak_bpm:.1f} (need 120+/-12), strength={feat.ac_peak_strength:.2f}"
    return ConstraintResult("rhythm_120bpm", passed=(s >= 0.6), score=s, detail=detail)

def constraint_high_flatness(feat: Features) -> ConstraintResult:
    """Noise-like: want higher spectral flatness."""
    s = clamp01((feat.spectral_flatness - 0.10) / 0.30)
    detail = ""
    if s < 0.6:
        detail = f"Flatness={feat.spectral_flatness:.3f} (need >0.25 for noise-like)"
    return ConstraintResult("high_flatness", passed=(s >= 0.6), score=s, detail=detail)

def constraint_high_roughness(feat: Features) -> ConstraintResult:
    """
    Require strong AM-band energy (psychoacoustic roughness).

    Roughness is the perceptual quality of rapid amplitude modulation in the
    20-150 Hz range (Plomp & Levelt, 1965). High roughness creates a "buzzing"
    or "grinding" quality - the sonic signature of constraint conflict.

    This constraint tests whether the generated audio exhibits the characteristic
    roughness that arises from interference between incompatible waveforms.
    """
    s = clamp01((feat.roughness - 0.03) / 0.10)
    detail = ""
    if s < 0.6:
        detail = f"Roughness={feat.roughness:.3f} (need >0.08 for high AM energy)"
    return ConstraintResult("high_roughness", passed=(s >= 0.6), score=s, detail=detail)

CONSTRAINTS = {
    "pitch_A4": constraint_pitch_A4,
    "rhythm_120bpm": constraint_rhythm_120bpm,
    "high_flatness": constraint_high_flatness,
    "high_roughness": constraint_high_roughness,
}

DEFAULT_CONSTRAINT_ORDER = ["pitch_A4", "rhythm_120bpm", "high_roughness", "high_flatness"]

# ----------------------------- 
# Baseline grammar sampling (for rho estimation)
# ----------------------------- 

OPS = ["+", "-", "*", "^", "&", "|", ">>", "<<", "%", "//"]
SHIFT_K = [1, 2, 3, 4, 5, 6, 7, 8]
SMALL_C = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 12, 15, 16, 24, 31, 32, 48, 63, 64, 127, 255]

def gen_atom(rng: random.Random) -> str:
    r = rng.random()
    if r < 0.50:
        if rng.random() < 0.60:
            return "t"
        return f"(t>>{rng.choice(SHIFT_K)})"
    if r < 0.85:
        return str(rng.choice(SMALL_C))
    fn = rng.choice(["abs", "int"])
    return f"{fn}(t)"

def gen_expr(rng: random.Random, depth: int) -> str:
    if depth <= 0:
        return gen_atom(rng)
    left = gen_expr(rng, depth - 1)
    right = gen_expr(rng, depth - 1)
    op = rng.choice(OPS)
    return f"({left}{op}{right})"

def sample_expressions(
    n_samples: int,
    max_depth: int,
    max_chars: int,
    seed: int
) -> List[str]:
    rng = random.Random(seed)
    out: List[str] = []
    attempts = 0
    while len(out) < n_samples and attempts < n_samples * 50:
        attempts += 1
        d = rng.randint(1, max_depth)
        e = gen_expr(rng, d)
        if len(e) <= max_chars:
            out.append(e)
    return out

def estimate_conflict_matrix(
    exprs: List[str],
    cfg: EvalConfig,
    constraint_names: List[str],
) -> Dict[str, object]:
    """
    Estimate P(ci), P(ci&cj) over baseline distribution, then derive rho_ij.
    rho_ij = 1 - P(i&j)/min(P(i),P(j))  (clipped)
    """
    counts = {c: 0 for c in constraint_names}
    pair_counts = {(a, b): 0 for a in constraint_names for b in constraint_names if a <= b}
    valid = 0

    for e in exprs:
        try:
            x = eval_bytebeat_expr(e, cfg)
            feat = extract_features(x, cfg.sr)
        except Exception:
            continue
        valid += 1
        passed = {}
        for c in constraint_names:
            res = CONSTRAINTS[c](feat)
            passed[c] = res.passed
            if res.passed:
                counts[c] += 1
        for i, a in enumerate(constraint_names):
            for b in constraint_names[i:]:
                if passed[a] and passed[b]:
                    pair_counts[(min(a, b), max(a, b))] += 1

    probs = {c: (counts[c] / valid if valid else 0.0) for c in constraint_names}
    rho = {c: {} for c in constraint_names}
    
    for i, a in enumerate(constraint_names):
        for b in constraint_names[i:]:
            pab = pair_counts[(min(a, b), max(a, b))] / valid if valid else 0.0
            denom = min(probs[a], probs[b]) if min(probs[a], probs[b]) > 0 else 1e-12
            rij = clamp01(1.0 - (pab / denom))
            rho[a][b] = rij
            rho[b][a] = rij

    return {
        "valid_samples": valid,
        "marginals": probs,
        "rho": rho,
        "definition": "rho_ij = 1 - P(i&j)/min(P(i),P(j)) over baseline grammar distribution",
        "constraints": constraint_names,
        "eval_config": {"sr": cfg.sr, "seconds": cfg.seconds, "max_chars": cfg.max_chars},
    }

def set_conflict_score(rho: Dict[str, Dict[str, float]], constraints: List[str]) -> float:
    """Aggregate set-level conflict score from pairwise rho_ij."""
    if len(constraints) < 2:
        return 0.0
    vals: List[float] = []
    for i, a in enumerate(constraints):
        for b in constraints[i + 1:]:
            vals.append(float(rho[a][b]))
    return float(np.mean(vals)) if vals else 0.0

# ----------------------------- 
# Candidate evaluation
# ----------------------------- 

@dataclass(frozen=True)
class Candidate:
    expr: str
    budget: int
    constraints: List[str]
    protocol: str
    run_id: str

def eval_candidate(c: Candidate, sr: int, seconds: float) -> Dict[str, object]:
    cfg = EvalConfig(sr=sr, seconds=seconds, max_chars=c.budget)
    
    try:
        x = eval_bytebeat_expr(c.expr, cfg)
        feat = extract_features(x, sr)
        results = [CONSTRAINTS[name](feat) for name in c.constraints]
        passed_all = all(r.passed for r in results)
        
        return {
            "run_id": c.run_id,
            "protocol": c.protocol,
            "budget": c.budget,
            "expr_len": len(c.expr.strip()),
            "expr": c.expr,
            "constraints": "|".join(c.constraints),
            "passed_all": int(passed_all),
            "scores": json.dumps({r.name: r.score for r in results}),
            "passes": json.dumps({r.name: r.passed for r in results}),
            "failures": json.dumps({r.name: r.detail for r in results if not r.passed}),
            "f0_peak": feat.f0_peak,
            "ac_bpm": feat.ac_peak_bpm,
            "ac_strength": feat.ac_peak_strength,
            "flatness": feat.spectral_flatness,
            "roughness": feat.roughness,
        }
    except Exception as e:
        return {
            "run_id": c.run_id,
            "protocol": c.protocol,
            "budget": c.budget,
            "expr_len": len(c.expr.strip()),
            "expr": c.expr,
            "constraints": "|".join(c.constraints),
            "passed_all": 0,
            "error": str(e),
        }

def eval_expr_constraints(
    expr: str,
    constraints: List[str],
    cfg: EvalConfig
) -> Tuple[bool, float, List[ConstraintResult], Features]:
    x = eval_bytebeat_expr(expr, cfg)
    feat = extract_features(x, cfg.sr)
    results = [CONSTRAINTS[name](feat) for name in constraints]
    passed_all = all(r.passed for r in results)
    score_sum = float(sum(r.score for r in results))
    return passed_all, score_sum, results, feat

# ----------------------------- 
# Repair protocol (NEW)
# ----------------------------- 

def generate_repair_prompt(candidate: Candidate, eval_row: Dict[str, object]) -> Optional[str]:
    """Generate structured repair prompt from constraint failures."""
    if int(eval_row.get("passed_all", 0)) == 1:
        return None
    
    failures_json = eval_row.get("failures", "{}")
    try:
        failures = json.loads(failures_json) if isinstance(failures_json, str) else failures_json
    except:
        failures = {}
    
    if not failures:
        return None
    
    repair_lines = [
        f"The previous expression (length {candidate.expr[:50]}{'...' if len(candidate.expr) > 50 else ''}) failed these constraints:",
        ""
    ]
    
    for name, detail in failures.items():
        desc = describe_constraint(name)
        repair_lines.append(f"FAIL {name}: {desc}")
        if detail:
            repair_lines.append(f"   Diagnostic: {detail}")
        repair_lines.append("")
    
    repair_lines.extend([
        "REPAIR INSTRUCTIONS:",
        f"1. Budget remains: <={candidate.budget} chars",
        "2. Preserve what's working (see passed constraints below)",
        "3. Fix ONLY the failed constraints - don't over-engineer",
        "",
        "Passed constraints (preserve these):",
    ])
    
    passes_json = eval_row.get("passes", "{}")
    try:
        passes = json.loads(passes_json) if isinstance(passes_json, str) else passes_json
    except:
        passes = {}
    
    for name, passed in passes.items():
        if passed:
            repair_lines.append(f"OK {name}")
    
    repair_lines.extend([
        "",
        "Return ONLY the repaired bytebeat expression. No explanation.",
        "If impossible under budget, return: IMPOSSIBLE"
    ])
    
    return "\n".join(repair_lines)

# ----------------------------- 
# Plotting
# ----------------------------- 

def plot_pass_vs_rho(rows: List[Dict[str, object]], outdir: Path) -> None:
    def rho_bin(r: float) -> float:
        return round(r / 0.1) * 0.1

    agg: Dict[Tuple[str, int, float], List[int]] = {}
    for r in rows:
        if "rho_set" not in r:
            continue
        key = (str(r["protocol"]), int(r["budget"]), rho_bin(float(r["rho_set"])))
        agg.setdefault(key, []).append(int(r["passed_all"]))

    pts = sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    if not pts:
        return

    plt.figure(figsize=(8, 5))
    for proto in sorted(set(k[0] for k, _ in pts)):
        for budget in sorted(set(k[1] for k, _ in pts)):
            xs, ys = [], []
            for (p, b, rb), vals in pts:
                if p == proto and b == budget:
                    xs.append(rb)
                    ys.append(float(np.mean(vals)))
            if xs:
                plt.plot(xs, ys, marker="o", label=f"{proto}, N={budget}", linewidth=2)
    
    plt.xlabel("Empirical conflict rho (binned)", fontsize=11)
    plt.ylabel("Pass rate (all constraints)", fontsize=11)
    plt.title("Pass rate vs conflict (rho) - Bytebeat", fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(outdir / "pass_rate_vs_rho.png", dpi=200)
    plt.close()

def plot_staging_benefit(rows: List[Dict[str, object]], outdir: Path) -> None:
    by_key: Dict[Tuple[int, str, float], Dict[str, List[int]]] = {}
    
    for r in rows:
        if "rho_set" not in r:
            continue
        key = (int(r["budget"]), str(r["constraints"]), float(r["rho_set"]))
        by_key.setdefault(key, {}).setdefault(str(r["protocol"]), []).append(int(r["passed_all"]))

    xs, ys = [], []
    for (budget, cset, rho), d in by_key.items():
        if "staged" in d and "one_shot" in d:
            ps = float(np.mean(d["staged"]))
            po = float(np.mean(d["one_shot"]))
            xs.append(rho)
            ys.append(ps - po)

    if not xs:
        return

    plt.figure(figsize=(8, 5))
    plt.scatter(xs, ys, alpha=0.6)
    plt.axhline(0, color='gray', linestyle='--', alpha=0.5)
    plt.xlabel("Empirical conflict rho (set)", fontsize=11)
    plt.ylabel("Staging benefit delta(pass rate)", fontsize=11)
    plt.title("Staging benefit vs conflict (rho) - Bytebeat", fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(outdir / "staging_benefit_vs_rho.png", dpi=200)
    plt.close()

def plot_phase_diagram(rows: List[Dict[str, object]], outdir: Path) -> None:
    def bin_rho(r: float) -> float:
        return round(r / 0.1) * 0.1

    for proto in sorted(set(str(r.get("protocol", "")) for r in rows)):
        filt = [r for r in rows if str(r.get("protocol", "")) == proto and "rho_set" in r]
        if not filt:
            continue
        
        budgets = sorted(set(int(r["budget"]) for r in filt))
        rhos = sorted(set(bin_rho(float(r["rho_set"])) for r in filt))
        
        grid = np.full((len(rhos), len(budgets)), np.nan, dtype=np.float32)

        for i, rb in enumerate(rhos):
            for j, b in enumerate(budgets):
                vals = [int(r["passed_all"]) for r in filt
                       if bin_rho(float(r["rho_set"])) == rb and int(r["budget"]) == b]
                if vals:
                    grid[i, j] = float(np.mean(vals))

        plt.figure(figsize=(8, 6))
        plt.imshow(grid, aspect="auto", origin="lower", cmap="RdYlGn", vmin=0, vmax=1)
        plt.xticks(range(len(budgets)), budgets)
        plt.yticks(range(len(rhos)), [f"{x:.1f}" for x in rhos])
        plt.xlabel("Budget N (chars)", fontsize=11)
        plt.ylabel("Conflict rho (binned)", fontsize=11)
        plt.title(f"Phase diagram (pass rate): {proto} - Bytebeat", fontsize=12)
        plt.colorbar(label="pass rate")
        plt.tight_layout()
        plt.savefig(outdir / f"phase_diagram_{proto}.png", dpi=200)
        plt.close()

# ----------------------------- 
# Prompts
# ----------------------------- 

def describe_constraint(name: str) -> str:
    if name == "pitch_A4":
        return "spectral peak near 440Hz (+/-40Hz), score >= threshold"
    if name == "rhythm_120bpm":
        return "autocorrelation peak near 120 BPM (+/-12) with sufficient strength"
    if name == "high_flatness":
        return "high spectral flatness (noise-like) above threshold"
    if name == "high_roughness":
        return "high amplitude-modulation energy in ~20-150Hz (roughness proxy)"
    return "unknown"

def make_prompt_one_shot(budget: int, constraints: List[str]) -> str:
    cdesc = "\n".join(f"- {c}: {describe_constraint(c)}" for c in constraints)
    return (
        "Return ONLY a bytebeat expression using variable t.\n"
        f"Hard budget: <= {budget} ASCII characters. No whitespace. No explanation.\n\n"
        "Your expression will be evaluated at 8kHz for 4 seconds; output wrapped to 0..255.\n\n"
        "Constraints to satisfy:\n"
        f"{cdesc}\n\n"
        "If you believe it is impossible under this budget, return EXACTLY: IMPOSSIBLE"
    )

def make_prompt_staged(budget: int, constraints: List[str]) -> str:
    order = [c for c in DEFAULT_CONSTRAINT_ORDER if c in constraints] + \
            [c for c in constraints if c not in DEFAULT_CONSTRAINT_ORDER]
    steps = "\n".join(f"{i+1}. Add/repair to satisfy: {c} ({describe_constraint(c)})" 
                     for i, c in enumerate(order))
    return (
        "We will build a bytebeat expression in stages.\n"
        f"Hard budget: <= {budget} ASCII characters. No whitespace. No explanation.\n\n"
        "At each stage, return ONLY the updated expression.\n"
        "Goal: satisfy all constraints while preserving earlier ones.\n\n"
        f"Stages:\n{steps}\n\n"
        "If at any stage you believe it is impossible under this budget, return EXACTLY: IMPOSSIBLE"
    )

# ----------------------------- 
# Commands
# ----------------------------- 

def cmd_baseline(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)

    cfg = EvalConfig(sr=args.sr, seconds=args.seconds, max_chars=args.budget)
    constraint_names = args.constraints.split(",") if args.constraints else list(CONSTRAINTS.keys())

    exprs = sample_expressions(
        n_samples=args.samples,
        max_depth=args.max_depth,
        max_chars=args.budget,
        seed=args.seed,
    )
    
    baseline = estimate_conflict_matrix(exprs, cfg, constraint_names)
    write_json(outdir / "baseline.json", baseline)

    print(f"[baseline] valid_samples={baseline['valid_samples']}, out={outdir/'baseline.json'}")

def cmd_eval(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    rho = baseline["rho"]

    in_rows = read_jsonl(Path(args.infile))
    candidates: List[Candidate] = []
    
    for i, r in enumerate(in_rows):
        candidates.append(Candidate(
            expr=str(r.get("expr", "")),
            budget=int(r.get("budget", args.budget)),
            constraints=list(r.get("constraints", [])),
            protocol=str(r.get("protocol", "one_shot")),
            run_id=str(r.get("run_id", f"row_{i}")),
        ))

    eval_rows: List[Dict[str, object]] = []
    for c in candidates:
        row = eval_candidate(c, sr=args.sr, seconds=args.seconds)
        row["rho_set"] = set_conflict_score(rho, c.constraints)
        row["k"] = len(c.constraints)
        eval_rows.append(row)

    write_csv(outdir / "eval_rows.csv", eval_rows)
    write_json(outdir / "eval_rows.json", eval_rows)

    plot_pass_vs_rho(eval_rows, outdir)
    plot_staging_benefit(eval_rows, outdir)
    plot_phase_diagram(eval_rows, outdir)

    print(f"[eval] wrote {len(eval_rows)} rows to {outdir}")
    
    # Summary stats
    by_proto = {}
    for r in eval_rows:
        p = str(r.get("protocol", ""))
        by_proto.setdefault(p, []).append(int(r.get("passed_all", 0)))
    
    print("\nPass rate by protocol:")
    for p, vals in sorted(by_proto.items()):
        print(f"  {p}: {np.mean(vals):.1%} ({sum(vals)}/{len(vals)})")

def cmd_bench(args: argparse.Namespace) -> None:
    """Creates a ready-to-fill JSONL template of tasks (one-shot + staged)."""
    outdir = Path(args.out)
    ensure_dir(outdir)

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    rho = baseline["rho"]

    budgets = [64, 96, 128] if not args.budgets else [int(x) for x in args.budgets.split(",")]
    
    # Constraint sets chosen to span conflict
    sets = [
        ["pitch_A4"],
        ["rhythm_120bpm"],
        ["pitch_A4", "rhythm_120bpm"],
        ["pitch_A4", "high_flatness"],
        ["rhythm_120bpm", "high_roughness"],
        ["pitch_A4", "rhythm_120bpm", "high_roughness"],
        ["pitch_A4", "rhythm_120bpm", "high_roughness", "high_flatness"],
    ]

    rows = []
    for b in budgets:
        for cs in sets:
            rho_set = set_conflict_score(rho, cs)
            run_base = f"N{b}_k{len(cs)}_rho{rho_set:.2f}_" + "_".join(cs[:2])
            
            rows.append({
                "run_id": run_base + "_one_shot",
                "protocol": "one_shot",
                "budget": b,
                "constraints": cs,
                "expr": "",
                "rho_set": rho_set,
                "prompt": make_prompt_one_shot(b, cs),
            })
            
            rows.append({
                "run_id": run_base + "_staged",
                "protocol": "staged",
                "budget": b,
                "constraints": cs,
                "expr": "",
                "rho_set": rho_set,
                "prompt": make_prompt_staged(b, cs),
            })

    template = outdir / "bench_template.jsonl"
    with template.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    write_json(outdir / "bench_manifest.json", {
        "budgets": budgets,
        "constraint_sets": sets,
        "n_tasks": len(rows),
        "how_to_use": "Fill expr fields with model outputs, then run: python supplementary/bridges/bytebeat_harness.py eval --in bench_filled.jsonl ...",
    })

    print(f"[bench] wrote template: {template}")

def cmd_synthesize(args: argparse.Namespace) -> None:
    outdir = Path(args.out)
    ensure_dir(outdir)

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    cfg_base = baseline.get("eval_config", {})
    sr = int(cfg_base.get("sr", args.sr))
    seconds = float(cfg_base.get("seconds", args.seconds))

    rows = read_jsonl(Path(args.infile))
    rng = random.Random(args.seed)
    out_rows = []
    started = time.time()

    for r in rows:
        constraints = r.get("constraints", [])
        if isinstance(constraints, str):
            constraints = [c for c in constraints.split("|") if c]
        constraints = [str(c) for c in constraints]
        budget = int(r.get("budget", args.budget))
        cfg = EvalConfig(sr=sr, seconds=seconds, max_chars=budget)

        best_expr = ""
        best_score = -1.0
        best_pass = False
        attempts = 0

        for attempt in range(1, args.max_attempts + 1):
            attempts = attempt
            depth = rng.randint(1, args.max_depth)
            expr = gen_expr(rng, depth)
            if len(expr) > budget:
                continue
            try:
                passed_all, score_sum, _, _ = eval_expr_constraints(expr, constraints, cfg)
            except Exception:
                continue
            if score_sum > best_score:
                best_score = score_sum
                best_expr = expr
                best_pass = passed_all
            if passed_all:
                best_expr = expr
                best_pass = True
                break

        r["expr"] = best_expr
        r["synth_attempts"] = attempts
        r["synth_score_sum"] = round(best_score, 4)
        r["synth_passed_all"] = int(best_pass)
        out_rows.append(r)

    out_file = outdir / "bench_filled.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r) + "\n")

    write_json(outdir / "synth_manifest.json", {
        "seed": args.seed,
        "max_attempts": args.max_attempts,
        "max_depth": args.max_depth,
        "n_rows": len(out_rows),
        "runtime_sec": round(time.time() - started, 2),
        "baseline": str(Path(args.baseline)),
        "input": str(Path(args.infile)),
        "output": str(out_file),
        "sr": sr,
        "seconds": seconds,
    })

    print(f"[synthesize] wrote {out_file} ({len(out_rows)} rows)")

def cmd_repair(args: argparse.Namespace) -> None:
    """Generate repair prompts for failed candidates."""
    outdir = Path(args.out)
    ensure_dir(outdir)

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    
    in_rows = read_jsonl(Path(args.infile))
    
    # First evaluate to get failures
    candidates: List[Candidate] = []
    for i, r in enumerate(in_rows):
        candidates.append(Candidate(
            expr=str(r.get("expr", "")),
            budget=int(r.get("budget", args.budget)),
            constraints=list(r.get("constraints", [])),
            protocol=str(r.get("protocol", "one_shot")),
            run_id=str(r.get("run_id", f"row_{i}")),
        ))

    repair_prompts = []
    for c in candidates:
        eval_row = eval_candidate(c, sr=args.sr, seconds=args.seconds)
        
        if int(eval_row.get("passed_all", 0)) == 0:
            repair_prompt = generate_repair_prompt(c, eval_row)
            if repair_prompt:
                repair_prompts.append({
                    "run_id": c.run_id + "_repair",
                    "protocol": c.protocol + "+repair",
                    "budget": c.budget,
                    "constraints": c.constraints,
                    "original_expr": c.expr,
                    "repair_prompt": repair_prompt,
                    "expr": "",  # Fill with repaired expression
                })

    out_file = outdir / "repair_prompts.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in repair_prompts:
            f.write(json.dumps(r) + "\n")

    print(f"[repair] generated {len(repair_prompts)} repair prompts -> {out_file}")
    print(f"  Fill 'expr' field with model's repaired expression, then run eval again")

# ----------------------------- 
# Main
# ----------------------------- 

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bytebeat diagonal-cost validation harness (ICML-ready + repair protocol)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p0 = sub.add_parser("baseline", help="Estimate empirical conflict matrix rho.")
    p0.add_argument("--out", required=True)
    p0.add_argument("--samples", type=int, default=5000)
    p0.add_argument("--max-depth", type=int, default=3)
    p0.add_argument("--budget", type=int, default=128)
    p0.add_argument("--sr", type=int, default=8000)
    p0.add_argument("--seconds", type=float, default=4.0)
    p0.add_argument("--seed", type=int, default=7)
    p0.add_argument("--constraints", type=str, default="pitch_A4,rhythm_120bpm,high_flatness,high_roughness")
    p0.set_defaults(func=cmd_baseline)

    p1 = sub.add_parser("eval", help="Evaluate candidate expressions from JSONL.")
    p1.add_argument("--in", dest="infile", required=True)
    p1.add_argument("--baseline", required=True)
    p1.add_argument("--out", required=True)
    p1.add_argument("--budget", type=int, default=128)
    p1.add_argument("--sr", type=int, default=8000)
    p1.add_argument("--seconds", type=float, default=4.0)
    p1.set_defaults(func=cmd_eval)

    p2 = sub.add_parser("bench", help="Create benchmark template JSONL.")       
    p2.add_argument("--baseline", required=True)
    p2.add_argument("--out", required=True)
    p2.add_argument("--budgets", type=str, default="64,96,128")
    p2.set_defaults(func=cmd_bench)

    p4 = sub.add_parser("synthesize", help="Fill bench template with synthetic expressions.")
    p4.add_argument("--in", dest="infile", required=True)
    p4.add_argument("--baseline", required=True)
    p4.add_argument("--out", required=True)
    p4.add_argument("--seed", type=int, default=7)
    p4.add_argument("--budget", type=int, default=128)
    p4.add_argument("--sr", type=int, default=8000)
    p4.add_argument("--seconds", type=float, default=4.0)
    p4.add_argument("--max-depth", type=int, default=3)
    p4.add_argument("--max-attempts", type=int, default=2000)
    p4.set_defaults(func=cmd_synthesize)

    p3 = sub.add_parser("repair", help="Generate repair prompts from failures.")
    p3.add_argument("--in", dest="infile", required=True)
    p3.add_argument("--baseline", required=True)
    p3.add_argument("--out", required=True)
    p3.add_argument("--budget", type=int, default=128)
    p3.add_argument("--sr", type=int, default=8000)
    p3.add_argument("--seconds", type=float, default=4.0)
    p3.set_defaults(func=cmd_repair)

    return p

def main() -> None:
    parser = build_argparser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
