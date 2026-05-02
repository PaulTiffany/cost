# Supplementary Validation Harnesses

This directory contains **evaluation harnesses** that operationalize the Diagonal Cost Bound theory from the main paper. These are instruments for measuring axis-vs-diagonal difficulty with objective verification.

## What These Harnesses Measure

Each harness tests the paper's core prediction: **simultaneous satisfaction of $k$ conflicting constraints is harder than sequential satisfaction, with difficulty scaling as predicted by the diagonal cost bound.**

| Harness | Domain | Constraints | Verification |
|---------|--------|-------------|--------------|
| `if_dsl_harness.py` | Interactive Fiction | Connectivity, solvability, path length, bottlenecks | BFS interpreter |
| `bytebeat_harness.py` | Audio Synthesis | Spectral peaks, rhythm patterns, waveform properties | FFT analysis |

## Key Metrics

- **Pass rate**: Fraction of attempts satisfying all constraints
- **Conflict estimate (ρ)**: Empirical co-occurrence deficit from baseline sampling
- **Staging benefit**: Pass rate difference between staged (sequential) and simultaneous protocols

## Quick Start

All commands run from the repository root. Requires Python 3.8+ with numpy, matplotlib.

### 1. Estimate Baseline Conflict Matrix

```bash
# IF DSL harness
python3 supplementary/bridges/if_dsl_harness.py baseline \
  --out supplementary/bridges/out_if_base --samples 3000 --seed 7

# Bytebeat harness
python3 supplementary/bridges/bytebeat_harness.py baseline \
  --out supplementary/bridges/out_bb_base --samples 5000 --seed 7
```

This generates `baseline.json` with the empirical conflict matrix ρ, computed as:
```
ρ_ij = 1 - P(i ∧ j) / min(P(i), P(j))
```
Higher ρ means constraints are harder to satisfy together.

### 2. Create Benchmark Templates

```bash
python3 supplementary/bridges/if_dsl_harness.py bench \
  --baseline supplementary/bridges/out_if_base/baseline.json \
  --out supplementary/bridges/out_if_bench

python3 supplementary/bridges/bytebeat_harness.py bench \
  --baseline supplementary/bridges/out_bb_base/baseline.json \
  --out supplementary/bridges/out_bb_bench
```

Generates `bench_template.jsonl` with prompts spanning low→high conflict (ρ).

### 3. Fill Templates (Two Options)

**Option A: Synthetic solver (no model required)**
```bash
python3 supplementary/bridges/if_dsl_harness.py synthesize \
  --in supplementary/bridges/out_if_bench/bench_template.jsonl \
  --baseline supplementary/bridges/out_if_base/baseline.json \
  --out supplementary/bridges/out_if_bench
```

**Option B: Model-generated outputs**
Fill the `dsl` field in each JSONL line with model output, then proceed to eval.

### 4. Evaluate

```bash
python3 supplementary/bridges/if_dsl_harness.py eval \
  --in supplementary/bridges/out_if_bench/bench_filled.jsonl \
  --baseline supplementary/bridges/out_if_base/baseline.json \
  --out supplementary/bridges/out_if_eval
```

### 5. Optional: Generate Repair Prompts

For failed candidates, generate structured error feedback:
```bash
python3 supplementary/bridges/if_dsl_harness.py repair \
  --in bench_filled.jsonl \
  --baseline supplementary/bridges/out_if_base/baseline.json \
  --out supplementary/bridges/out_if_repair
```

## Outputs

Each harness produces:

| File | Description |
|------|-------------|
| `eval_rows.csv` / `eval_rows.json` | Per-candidate results |
| `pass_rate_vs_rho.png` | Pass rate vs conflict (should decrease) |
| `staging_benefit_vs_rho.png` | Staging benefit vs conflict (should increase) |
| `phase_diagram_{protocol}.png` | Heatmap: k constraints × ρ conflict |
| `repair_prompts.jsonl` | Structured feedback for failures |

## Interpreting Results

**If the theory holds:**
- Pass rate should **decrease** as ρ increases
- Staging benefit should **increase** as ρ increases
- Phase diagram should show a "diagonal barrier" region where one-shot fails but staged succeeds

**Falsification criteria:**
- If pass rate is constant across ρ → conflict doesn't affect difficulty
- If staging benefit is zero or negative at high ρ → staging doesn't help
- If high-ρ one-shot outperforms low-ρ staged → geometry is not the limiting factor

## Connection to Paper Sections

- **Theory**: Section 3 (Diagonal Cost Bound), Section 4 (Conflict Amplification)
- **Harness Specification**: Section 6.1 (Table 1)
- **Ground-Truth Validation**: Section 6.2
- **Sonification Bridge**: Section 6.6, Appendix G

## Requirements

```
numpy>=1.20
matplotlib>=3.4
```

No API keys or external services required. Runs on CPU in <5 minutes.
