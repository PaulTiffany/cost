"""Agent M12: Numerical stability tests for paper formulas.

Each test reports relative error / behavior at edge cases of the
geometric multi-constraint formulas. NumPy float64 throughout.
"""
import numpy as np
from numpy.linalg import inv, cond
from mpmath import mp, mpf, sqrt as msqrt
mp.dps = 50  # 50-digit reference

def banner(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)

# ---------------------------------------------------------------------------
# Test 1: 1 - rho*(k-1) near boundary
# ---------------------------------------------------------------------------
banner("Test 1: catastrophic cancellation in 1 - rho*(k-1)")
k = 10
rho_star = 1.0 / (k - 1)  # boundary

print(f"k={k}, rho*=1/(k-1)={rho_star!r}")
print(f"{'eps':>10} {'1-rho(k-1) f64':>22} {'mp ref':>22} {'rel_err':>14} {'guard fires':>12}")
for eps in [1e-3, 1e-6, 1e-9, 1e-12, 1e-15, 1e-16]:
    rho = rho_star - eps
    val_f64 = 1.0 - rho * (k - 1)
    mp_val = mpf(1) - mpf(rho) * mpf(k - 1)
    rel_err = abs(val_f64 - float(mp_val)) / abs(float(mp_val)) if mp_val != 0 else float("inf")
    # Guard: rho_hat*(k-1) >= 1
    guard = (rho * (k - 1)) >= 1.0
    print(f"{eps:>10.0e} {val_f64:>22.4e} {float(mp_val):>22.4e} {rel_err:>14.2e} {str(guard):>12}")

# cor:k_uniform Delta-norm: ||Delta|| = sqrt(k) * something / sqrt(1 - rho(k-1))
print("\ncor:k_uniform-style: 1/sqrt(1-rho(k-1)) amplification")
for eps in [1e-3, 1e-6, 1e-9, 1e-12]:
    rho = rho_star - eps
    amp = 1.0 / np.sqrt(max(1.0 - rho * (k - 1), 0.0))
    print(f"  eps={eps:.0e}  amp factor = {amp:.4e}")

# ---------------------------------------------------------------------------
# Test 2: Sherman-Morrison vs direct for 1' Gamma^-1 1
# ---------------------------------------------------------------------------
banner("Test 2: 1' Gamma^-1 1 via direct inverse vs Sherman-Morrison")

def gamma(k, rho):
    return (1 - rho) * np.eye(k) + rho * np.ones((k, k))

def direct(k, rho):
    G = gamma(k, rho)
    return float(np.ones(k) @ inv(G) @ np.ones(k))

def sm(k, rho):
    # 1' [(1-rho)I + rho 11']^-1 1
    # = 1/(1-rho) * (k - rho k^2 / (1 - rho + rho k))
    # Equivalent: k / (1 + rho(k-1))
    return k / (1.0 + rho * (k - 1))

print(f"{'k':>6} {'rho':>10} {'direct':>16} {'SM':>16} {'rel_err':>12} {'cond(G)':>12}")
for k in [5, 10, 50, 200]:
    for rho in [0.01, 0.05, 0.5 / (k - 1), 0.9 / (k - 1), 0.999 / (k - 1)]:
        d = direct(k, rho)
        s = sm(k, rho)
        rel = abs(d - s) / abs(s)
        c = cond(gamma(k, rho))
        print(f"{k:>6} {rho:>10.4e} {d:>16.6e} {s:>16.6e} {rel:>12.2e} {c:>12.2e}")

# ---------------------------------------------------------------------------
# Test 3: 1 - h' G_P^-1 h  preservation, p = k-1
# ---------------------------------------------------------------------------
banner("Test 3: preservation 1 - h' G_P^-1 h at p=k-1, rho=1/(k-1)-1e-6")

def preservation_test(k, rho):
    # G full = (1-rho)I + rho 11'  on k constraints.
    # Drop row/col k -> G_P (k-1 x k-1).  h = correlation column = rho * 1.
    G = (1 - rho) * np.eye(k) + rho * np.ones((k, k))
    G_P = G[:k - 1, :k - 1]
    h = G[:k - 1, k - 1]   # = rho * ones(k-1)
    val = 1.0 - h @ inv(G_P) @ h
    # Closed form: 1 - rho^2 (k-1) / (1 + rho(k-2))
    closed = 1.0 - (rho ** 2) * (k - 1) / (1.0 + rho * (k - 2))
    # Equivalent:  (1 - rho)(1 + rho(k-1)) / (1 + rho(k-2))
    closed2 = (1.0 - rho) * (1.0 + rho * (k - 1)) / (1.0 + rho * (k - 2))
    return val, closed, closed2, cond(G_P)

print(f"{'k':>6} {'eps':>10} {'numeric':>16} {'closed':>16} {'closed2':>16} {'rel_err':>12} {'cond':>12}")
for k in [5, 10, 50]:
    rho_b = 1.0 / (k - 1)
    for eps in [1e-3, 1e-6, 1e-9, 1e-12]:
        rho = rho_b - eps
        v, c1, c2, cnd = preservation_test(k, rho)
        rel = abs(v - c1) / abs(c1) if c1 != 0 else float("inf")
        print(f"{k:>6} {eps:>10.0e} {v:>16.6e} {c1:>16.6e} {c2:>16.6e} {rel:>12.2e} {cnd:>12.2e}")

# ---------------------------------------------------------------------------
# Test 4: cross term  ||a1 + a2||^2 = a1^2 + a2^2 + 2 rho a1 a2  at small rho
# ---------------------------------------------------------------------------
banner("Test 4: alpha^2 + 2 rho a1 a2 at very small rho")
a1 = a2 = 1.0
print(f"{'rho':>10} {'expand':>20} {'fma':>20} {'mp ref':>22} {'rel_err_expand':>16}")
for rho in [1e-4, 1e-7, 1e-10, 1e-13, 1e-16]:
    expand = a1 * a1 + a2 * a2 + 2.0 * rho * a1 * a2
    fma = np.fma(2.0 * rho, a1 * a2, a1 * a1 + a2 * a2) if hasattr(np, "fma") else expand
    mp_val = mpf(a1) ** 2 + mpf(a2) ** 2 + mpf(2) * mpf(rho) * mpf(a1) * mpf(a2)
    rel = abs(expand - float(mp_val)) / abs(float(mp_val))
    print(f"{rho:>10.0e} {expand:>20.16f} {fma:>20.16f} {float(mp_val):>22.16f} {rel:>16.2e}")

# ---------------------------------------------------------------------------
# Test 5: Algorithm 1 guard at rho_hat = 1/(k-1) + 1e-16
# ---------------------------------------------------------------------------
banner("Test 5: Algorithm 1 guard at boundary + 1e-16")
for k in [5, 10, 100]:
    rho_b = 1.0 / (k - 1)
    for delta in [-1e-16, 0.0, 1e-16, 1e-15, 1e-12, 1e-9]:
        rho_hat = rho_b + delta
        prod = rho_hat * (k - 1)
        guard_geq1 = prod >= 1.0
        guard_strict = prod > 1.0
        print(f"  k={k:>3} rho_hat=1/(k-1)+{delta:+.0e}  rho*(k-1)={prod!r}  >=1: {guard_geq1}  >1: {guard_strict}")

# ---------------------------------------------------------------------------
# Test 6: Lipschitz accumulated displacement
# ---------------------------------------------------------------------------
banner("Test 6: Lipschitz contract L_hat * 4096 numerical range")
for L in [0.025, 0.048]:
    bound = L * 4096
    print(f"  L_hat={L} -> L*4096 = {bound:.4f}  (float64 ulp at this scale ~ {np.spacing(bound):.2e})")
# Check that adding noise of magnitude ulp doesn't dominate signal
print("  embedding L2 norms (MiniLM) typically ~1.0; range 100-200 well above ulp")

# ---------------------------------------------------------------------------
# Test 7: cosine similarity noise floor for rho_hat threshold
# ---------------------------------------------------------------------------
banner("Test 7: cosine-similarity noise vs rho_hat<0.15 threshold")
rng = np.random.default_rng(0)
d = 384  # MiniLM dim
trials = 20000
samples = []
for _ in range(trials):
    x = rng.standard_normal(d); x /= np.linalg.norm(x)
    y = rng.standard_normal(d); y /= np.linalg.norm(y)
    samples.append(float(x @ y))
samples = np.array(samples)
print(f"  cosine of random unit vectors in d={d}: mean={samples.mean():.2e}  std={samples.std():.4f}")
print(f"  |cos| < 0.05: {np.mean(np.abs(samples) < 0.05) * 100:.1f}% of pairs")
print(f"  |cos| < 0.15: {np.mean(np.abs(samples) < 0.15) * 100:.1f}% of pairs")
# Note: 1e-7 'noise' in problem statement refers to FP error of dot product itself,
# not the random-direction baseline. Test that too:
x = rng.standard_normal(d); x /= np.linalg.norm(x)
y = rng.standard_normal(d); y /= np.linalg.norm(y)
truth = float(mpf(0) + sum(mpf(float(xi)) * mpf(float(yi)) for xi, yi in zip(x, y)))
fp = float(x @ y)
print(f"  FP dot-product error vs 50-digit ref: {abs(fp - truth):.2e}")

# ---------------------------------------------------------------------------
# Test 8: f''(rho) = (3 sqrt(k)/4) (k-1)^2 (1 - rho(k-1))^(-5/2)
# ---------------------------------------------------------------------------
banner("Test 8: convexity f''(rho)")
def fpp(k, rho):
    return (3.0 * np.sqrt(k) / 4.0) * (k - 1) ** 2 * (1.0 - rho * (k - 1)) ** (-2.5)

print(f"{'k':>6} {'rho':>14} {'f''(rho)':>18} {'positive?':>10}")
for k in [5, 10, 50]:
    for rho in [1e-6, 1e-3, 0.01, 1.0 / (k - 1) - 1e-3, 1.0 / (k - 1) - 1e-9]:
        v = fpp(k, rho)
        print(f"{k:>6} {rho:>14.6e} {v:>18.6e} {str(v > 0):>10}")

print("\n[done]")
