# Exhaustive Claims Checklist v2.0

**Paper:** The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
**Venue:** NeurIPS 2026
**Total Claims:** 153 pattern-coded (25 critical + 72 important + 56 complete) + 269 documented-only

**Note on Tier 3 (complete) sizing:** the original CLAIM_AUDIT design budgeted 325 complete claims (exhaustive table-cell index). In practice the L3 reverse-coverage sweep showed that 48 well-chosen pattern-coded claims drive empirical body-prose coverage to 91.3%, with **every** specific multi-digit decimal in main.tex mapped to some claim pattern. The residual ~118 uncovered numerics are 1-digit ints in math-mode notation (`\\mathbf{1}`, `\\Gamma^{-1}`, `\\frac{1}{1-c^2}`, etc.), not empirical claims. The remaining 277 inventoried Tier 3 entries are documented in the table inventory below for traceability without requiring per-cell pattern coding.

---

## Tiered Verification Architecture

| Tier | Count | Description | Time |
|------|-------|-------------|------|
| **Critical** | 25 | Core theorems, headline results | ~30 sec |
| **Important** | 72 | Main tables, key appendix numbers | ~2 min |
| **Complete** | 56 pattern-coded (out of 325 inventoried) | Granular table cells from L3 triage | ~3 min |

---

# Tier 1: Critical Claims (25)

**Must-verify for reviewer confidence. If any fail, the paper's core contribution is at risk.**

## Theoretical Bounds (7 claims)

| ID | Claim | Source | Verification |
|----|-------|--------|--------------|
| C1 | $\delta_{\min} = \sqrt{2/(1-\rho)}$ diagonal cost | Theorem 3.1 | `gradient_r_sanity_v2.py` |
| C2 | $\delta_{\min} = \sqrt{k}$ orthogonal k-scaling | Theorem 3.4 | `gram_matrix_verification.py` |
| C3 | 0/4,800 smooth-regime refutations | Abstract | `code_constraint_results.json` |
| C4 | $r_s = 1.0$ rank correlation | Table 2 | `calibration_results.json` |
| C5 | 4.8× staging at frontier (opus-4.5) | Table 4 | `fixed_point_claude_family.json` |
| C6 | 26.4× staging (opus-4.1, highest) | Table 4 | `fixed_point_claude_family.json` |
| C7 | $\delta_{\min} = \sqrt{k/(1-\rho(k-1))}$ generalized | Corollary 3.5 | `gram_matrix_verification.py` |

## Empirical Calibration (6 claims)

| ID | Claim | Source | Verification |
|----|-------|--------|--------------|
| C8 | $\hat{L} \in [0.019, 0.031]$ across 4 models | Table 1 | `calibration_results.json` |
| C9 | 89% smooth, 11% pivot regime | Section 1 | `calibration_results.json` |
| C10 | 94% router agreement across encoders | Table 6 | `conflict_detection/results.json` |
| C11 | 7.37% constitution conflict rate | Appendix W | `constitution_analysis.json` |
| C12 | 93%→3% feasibility decay (k=2→k=10) | Appendix V | `charitable_feasibility.json` |

## Routing & Efficiency (8 claims)

| ID | Claim | Source | Verification |
|----|-------|--------|--------------|
| C13 | $\hat{\rho}_{\text{stage}} = 0.15$ | Algorithm 1 | Definition |
| C14 | $\hat{\rho}_{\text{fail}} = 0.5$ | Algorithm 1 | Definition |
| C15 | 18% pass, 384 tokens, 0.67× efficiency | Table 11 | `code_constraint_results.json` |
| C16 | ≥91% threshold robustness (±20%) | Table 7 | `code_constraint_results.json` |
| C17 | $r_s \geq 0.94$ across encoders | Table 6 | `conflict_detection/results.json` |
| C18 | 94% trajectories with drift <0.15 | Section 4 | `code_constraint_results.json` |
| C19 | 6% high-drift = pivot completions | Appendix J | `code_constraint_results.json` |
| C20 | <2% regret vs oracle | Table 11 | `code_constraint_results.json` |

## Domain Transfer (4 claims)

| ID | Claim | Source | Verification |
|----|-------|--------|--------------|
| C21 | 100/100 vs 0/100 hard-negative | Appendix D.5 | `high_k_opus_results.json` |
| C22 | 59%→0% Bytebeat collapse | Section 5.2 | `bytebeat_harness.py` |
| C23 | 0% IF-DSL at ρ≥1.0 | Section 5.2 | `if_dsl_harness.py` |
| C24 | 77.5%→100% JSON-NL failure | Appendix H | `json_nl_v4_results.json` |

## Constitution Analysis (1 claim)

| ID | Claim | Source | Verification |
|----|-------|--------|--------------|
| C25 | 20 principles → 190 pairs | Appendix W | `constitution_analysis.json` |

---

# Tier 2: Important Claims (72)

**Key empirical results from main paper tables and appendix headline numbers.**

## Table 1: Lipschitz Calibration (6 claims)

| ID | Model | $\hat{L}$ | Tightness | Source |
|----|-------|-----------|-----------|--------|
| I1 | Qwen-2.5-Coder | 0.023 ± 0.008 | - | `calibration_results.json` |
| I2 | Qwen-2.5-Coder | - | 91% | `calibration_results.json` |
| I3 | DeepSeek-Coder | 0.019 ± 0.006 | - | `calibration_results.json` |
| I4 | DeepSeek-Coder | - | 93% | `calibration_results.json` |
| I5 | TinyLlama-1.1B | 0.031 ± 0.011 | - | `calibration_results.json` |
| I6 | TinyLlama-1.1B | - | 84% | `calibration_results.json` |

## Table 2: Regime Performance (4 claims)

| ID | Tier | $\hat{\rho}$ | 1-shot | Failure | Source |
|----|------|--------------|--------|---------|--------|
| I7 | Control | 0.05 | 76% | 24% | `calibration_results.json` |
| I8 | Low | 0.20 | 56% | 44% | `calibration_results.json` |
| I9 | Moderate | 0.40 | 23% | 77% | `calibration_results.json` |
| I10 | High | 0.65 | 2% | 98% | `calibration_results.json` |

## Table 3: Feasibility Boundary (5 claims)

| ID | ρ | $\delta_{\min}$ (theory) | $\delta_{\min}$ (numerical) |
|----|---|-------------------------|-----------------------------|
| I11 | 0.0 | 1.4142 | 1.4142 |
| I12 | 0.3 | 1.6903 | 1.6903 |
| I13 | 0.5 | 2.0000 | 2.0000 |
| I14 | 0.7 | 2.5820 | 2.5820 |
| I15 | 0.9 | 4.4721 | 4.4721 |

## Table 4: Claude Family (7 claims)

| ID | Model | Gen | Wins | Ratio | Source |
|----|-------|-----|------|-------|--------|
| I16 | haiku-3 | 3 | 3/6 | 2.5× | `fixed_point_claude_family.json` |
| I17 | sonnet-4 | 4 | 5/6 | 4.4× | `fixed_point_claude_family.json` |
| I18 | opus-4 | 4 | 5/6 | 22.7× | `fixed_point_claude_family.json` |
| I19 | opus-4.1 | 4 | 5/6 | 26.4× | `fixed_point_claude_family.json` |
| I20 | haiku-4.5 | 4.5 | 5/6 | 23.3× | `fixed_point_claude_family.json` |
| I21 | sonnet-4.5 | 4.5 | 5/6 | 23.8× | `fixed_point_claude_family.json` |
| I22 | opus-4.5 | 4.5 | 4/6 | 4.8× | `fixed_point_claude_family.json` |

## Table 5: Embedding Analysis (3 claims)

| ID | Encoder | $\rho_{\max}$ | Rank | Source |
|----|---------|---------------|------|--------|
| I23 | MiniLM-L6-v2 | 0.12 ± 0.02 | 4 | `constitution_analysis.json` |
| I24 | mpnet-base-v2 | 0.18 ± 0.03 | 4 | `constitution_analysis.json` |
| I25 | multilingual-MiniLM | 0.20 ± 0.04 | 4 | `constitution_analysis.json` |

## Table 6: Encoder Sensitivity (3 claims)

| ID | Encoder | $\hat{\rho}$ Range | Router | Outcome |
|----|---------|-------------------|--------|---------|
| I26 | MiniLM-L6-v2 | [0.05, 0.65] | --- | --- |
| I27 | mpnet-base-v2 | [0.08, 0.71] | 94% | 91% |
| I28 | multilingual-MiniLM | [0.06, 0.68] | 96% | 93% |

## Table 7: Threshold Sensitivity (2 claims)

| ID | Perturbation | Agreement | ΔPass |
|----|--------------|-----------|-------|
| I29 | −20% (conservative) | 93% | +1.2% |
| I30 | +20% (aggressive) | 91% | −2.1% |

## Table 8: k-Scaling (5 claims)

| ID | k | $\delta_{\min}$ (theory) | $\delta_{\min}$ (empirical) |
|----|---|-------------------------|-----------------------------|
| I31 | 2 | 1.4142 | 1.4142 |
| I32 | 3 | 1.7321 | 1.7321 |
| I33 | 4 | 2.0000 | 2.0000 |
| I34 | 5 | 2.2361 | 2.2361 |
| I35 | 8 | 2.8284 | 2.8284 |

## Table 11: Baselines (6 claims)

| ID | Protocol | Pass% | Tokens | Speedup |
|----|----------|-------|--------|---------|
| I36 | One-shot (always) | 5% | 256 | 1.0× |
| I37 | Staged (always) | 18% | 512 | 0.5× |
| I38 | Best-of-4 sampling | 12% | 1024 | 0.25× |
| I39 | Self-refine (≤3 iter) | 15% | 640 | 0.4× |
| I40 | Geometric router | 18% | 384 | 0.67× |
| I41 | Oracle (per-instance) | 21% | 320 | 0.8× |

## Table 12: Transfer (4 claims)

| ID | Domain | Router Agree | ΔPass | Regret |
|----|--------|--------------|-------|--------|
| I42 | Code (calibration) | 100% | --- | 1.8% |
| I43 | JSON-NL (transfer) | 94% | +2% | 2.1% |
| I44 | IF-DSL (transfer) | 91% | −1% | 2.4% |
| I45 | Bytebeat (transfer) | 88% | +1% | 3.1% |

## Hyperparameters (6 claims)

| ID | Parameter | Value | Source |
|----|-----------|-------|--------|
| I46 | Token budgets | 128, 192, 256, 384, 512 | Section 5, App F |
| I47 | Sample size (main) | N=60 per condition | Section 5 |
| I48 | Sample size (spot-checks) | N=100 | Supplementary D.5 |
| I49 | Default $\hat{L}$ | ~0.025 | Table 1 |
| I50 | Pivot step threshold | 2.5 × $\hat{L}$ | Section 1 |
| I51 | Direction drift threshold | 15° | Section 1 |

## Derived Statistics (7 claims)

| ID | Statistic | Value | Source |
|----|-----------|-------|--------|
| I52 | Benchmark structure | 12 × 4 × 4 | Section 5 |
| I53 | Total trials | 4,800 | Abstract |
| I54 | Median direction drift | 0.07 (IQR: 0.03–0.12) | Appendix J |
| I55 | 95th percentile step | 0.041 | Appendix K |
| I56 | 99th percentile step | 0.067 | Appendix K |
| I57 | Maximum step | 0.12 | Appendix K |
| I58 | Semantic jump rate | 2.3% (>3σ) | Appendix K |

## Appendix Key Claims (17 claims)

| ID | Appendix | Claim | Source |
|----|----------|-------|--------|
| I59 | B | 0.00% error for k=2,3,4,5,8 | Numerical |
| I60 | V | k=2: 93%, k=3: 79%, k=4: 63%, k=5: 46% (current paper tab:charity_k row, half-up rounded from charitable_feasibility.json) | `charitable_feasibility.json` |
| I61 | V | k=6: 31%, k=8: 12%, k=10: 3% (current paper tab:charity_k row) | `charitable_feasibility.json` |
| I62 | W | 190 pairs, 7.37% high conflict | `constitution_analysis.json` |
| I63 | W | Mean ρ = 0.267, max = 0.86 | `constitution_analysis.json` |
| I64 | X | 15 compound tasks, ρ 0.15-0.75 | Section |
| I65 | H | JSON-NL: 22.5%, 10%, 0%, 0% | `json_nl_v4_results.json` |
| I66 | I | Gradient-ρ: r≈0.4, r_s=1.0 | `gradient_r_sanity_v2.py` |
| I67 | P | <10ms router latency | Section |
| I68 | Q | AM 20-70Hz mapping | Section |
| I69 | R | IF-DSL: 0% at ρ≥1.0 | `if_dsl_harness.py` |
| I70 | S | Bytebeat cliff at ρ≥0.8 | `bytebeat_harness.py` |
| I71 | U | <5min CPU, ~20h GPU | Section |
| I72 | E | 5% vs 58%/71% conjunction | Section |
| I73 | M | Phase transition 1/(k-1) | Theorem |
| I74 | D | ρ_max ~ 0.18 | `constitution_analysis.json` |
| I75 | D | 24% reinforcing pairs | Section |

---

# Tier 3: Complete Claims (325)

**Exhaustive table-by-table index of every verifiable number.**

## Table Inventory (25 tables, ~350 cells)

### Main Paper Tables (1-6)

| Table | Label | Title | Cells | Data Source |
|-------|-------|-------|-------|-------------|
| 1 | tab:notation | Notation | 8 | N/A (definitions) |
| 2 | tab:lipschitz_calibration | Lipschitz Calibration | 9 | `calibration_results.json` |
| 3 | tab:baselines_main | Regime Performance | 16 | `calibration_results.json` |
| 4 | tab:feasibility | Feasibility Boundary | 15 | `gradient_r_sanity_v2.py` |
| 5 | tab:delta_capacity | Delta Capacity | 15 | `delta_capacity_results.json` |
| 6 | tab:calibration | Calibration Results | 16 | `calibration_results.json` |

### Appendix Tables (A1-A19)

| Table | Label | Title | Cells | Data Source |
|-------|-------|-------|-------|-------------|
| A1 | tab:harness_suite | Harness Suite | 18 | N/A (descriptions) |
| A2 | tab:ablation | Ablation Study | 15 | `gram_matrix_verification.py` |
| A3 | tab:claude_family | Claude Family | 28 | `fixed_point_claude_family.json` |
| A4 | tab:embedding_R | Embedding R | 9 | `constitution_analysis.json` |
| A5 | tab:encoder_sensitivity | Encoder Sensitivity | 12 | `conflict_detection/results.json` |
| A6 | tab:robustness_stress | Robustness Stress | 12 | `code_constraint_results.json` |
| A7 | tab:threshold_sensitivity | Threshold Sensitivity | 9 | `code_constraint_results.json` |
| A8 | tab:baselines | Full Baselines | 24 | `code_constraint_results.json` |
| A9 | tab:transfer | Transfer Results | 16 | Multiple |
| A10 | tab:llm_bridge | LLM Bridge | 32 | `code_constraint_results.json` |
| A11 | tab:delta_capacity_model | Delta Capacity Model | 20 | `delta_capacity_results.json` |
| A12 | tab:json_nl | JSON-NL | 24 | `json_nl_v4_results.json` |
| A13 | tab:L_robustness | L Robustness | 9 | `calibration_results.json` |
| A14 | tab:hyperparams | Hyperparameters | 20 | Spot-check |
| A15 | tab:overhead | Overhead | 12 | Computed |
| A16 | tab:sonification_map | Sonification | 9 | Constants |
| A17 | tab:if_dsl | IF-DSL | 18 | `if_dsl_harness.py` |
| A18 | tab:bytebeat | Bytebeat | 16 | `bytebeat_harness.py` |
| A19 | tab:compatibility_taxonomy | Compatibility | 12 | `constitution_analysis.json` |

---

## Appendix Section Claims (A-X)

| Section | Title | Key Claims | Verifiable Numbers |
|---------|-------|------------|-------------------|
| A | Harness Suite | 1 | 6 |
| B | k-Scaling Verification | 5 | 5 |
| C | Frontier Model Results | 7 | 14 |
| D | Embedding Analysis | 10 | 15 |
| E | Code Constraint Full | 8 | 20 |
| F | Delta Capacity by Model | 20 | 40 |
| G | Interactive Demos | 5 | 5 |
| H | JSON-NL Transfer | 4 | 16 |
| I | Gradient-ρ Sanity | 2 | 10 |
| J | Direction Stability | 3 | 6 |
| K | Lipschitz Details | 12 | 24 |
| L | Preservation Bounds | 2 | 4 |
| M | Proofs | 5 | 5 |
| N | Budgeted Decoding | 3 | 6 |
| O | Experiment Details | 20 | 30 |
| P | Additional Results | 5 | 8 |
| Q | Sonification | 4 | 6 |
| R | IF-DSL Harness | 3 | 9 |
| S | Bytebeat Harness | 4 | 12 |
| T | Diagnostic Framing | 0 | 0 |
| U | Reproducibility | 2 | 4 |
| V | Charitable Feasibility | 7 | 14 |
| W | Constitution Analysis | 8 | 20 |
| X | Compound Tasks | 3 | 15 |

---

# Verification Harnesses

| Harness | Command | Validates |
|---------|---------|-----------|
| Gradient-ρ | `python supplementary/bridges/gradient_r_sanity_v2.py` | C1, C7, I11-I15, I66 |
| Gram Matrix | `python supplementary/bridges/gram_matrix_verification.py` | C2, I31-I35, I59 |
| Bytebeat | `python supplementary/bridges/bytebeat_harness.py` | C22, I45, I70 |
| IF-DSL | `python supplementary/bridges/if_dsl_harness.py` | C23, I44, I69 |
| JSON-NL | `python supplementary/bridges/json_nl_experiment_v4.py` | C24, I43, I65 |
| Charitable | `python supplementary/bridges/charitable_feasibility_simulation.py` | C12, I60, I61 |

---

# Data File Mappings

| File | Path | Validates |
|------|------|-----------|
| code_constraint_results.json | `supplementary/experiments/` | C3, I7-I10, I36-I42, I53 |
| calibration_results.json | `supplementary/experiments/` | C4, C8, C9, I1-I6 |
| fixed_point_claude_family.json | `supplementary/experiments/` | C5, C6, I16-I22 |
| constitution_analysis.json | `supplementary/experiments/outputs/constitution/` | C11, C25, I23-I25, I62-I63, I74 |
| charitable_feasibility.json | `supplementary/experiments/outputs/charitable/` | C12, I60, I61 |
| conflict_detection/results.json | `supplementary/experiments/outputs/` | C10, C17, I26-I28 |
| high_k_opus_results.json | `supplementary/experiments/outputs/high_k_opus/` | C21 |
| json_nl_v4_results.json | `supplementary/experiments/` | C24, I43, I65 |
| delta_capacity_results.json | `supplementary/experiments/` | I52 |
| gram_eigendecomposition_results.json | `rebuttal/figures/` | C11, I63, eigenvalue annotation (4.31) |
| per_task_correlation_results.json | `rebuttal/figures/` | r_s=-0.9417 (Section 5.1), per-tier means (I7-I10) |
| proxy_ablation_results.json | `rebuttal/figures/` | proxy comparison: real/shuffled/random rho-hat |
| soft_constraint_results.json | `rebuttal/figures/` | soft-constraint cliff per-trial outcomes |
| unconditional_pivot_results.json | `rebuttal/figures/` | C3 backing: 8/480 high-tier success, 0/427 smooth |
| certificates.txt | `supplementary/experiments/outputs/compatibility_analysis/` | §2528 example pairs (P4_P18 rho=0.8972 -> "rho-hat=0.90"; P8_P19 rho=0.6828) |
| constitution_wheel_full.wav | `supplementary/demos/audio_demos/` | §2289 sonification reference (named in paper) |
| diagonal_all_four.wav | `supplementary/demos/audio_demos/` | §2290 sonification reference (named in paper) |

### Audio demonstrations (supplementary/demos/audio_demos/)

The full audio demonstration set is regenerated by `python supplementary/demos/sonification.py` and contains 31 .wav files: 6 conflict-rho discrete steps + sweep + phase transition (8), 4 principle singles, 6 pairwise combinations, 3 diagonal compositions, 6 simultaneous-vs-staged comparisons, plus conflict resolution / wheel / safety variants. Only `constitution_wheel_full.wav` and `diagonal_all_four.wav` are individually referenced by name in main.tex; the rest are reviewer-evidence demonstrations.

---

# Figure-only Annotations

Some numeric values appear only in figures (as annotations or labels), not in the body text. They are not Tier 1/2 claims because L1 audits main.tex prose, but they are documented here for traceability. The L5 figure-value-check sweep surfaces uncovered figure numerics; entries here explain their source.

| Figure | Annotation | Source data file | Notes |
|--------|------------|------------------|-------|
| gram_eigendecomposition.pdf | lambda_min ~ -4.31 | rebuttal/figures/gram_eigendecomposition_results.json | Dominant negative eigenvalue annotation in the constitution Gram spectrum panel. JSON has lambda_min = -4.309884522530611. L5 surfaces "4.31" as uncovered (not in main.tex prose); this row documents its provenance. |

---

# Quick Verification Commands

```powershell
# Tier 1: Critical claims only (reviewer-focused)
python verify.py --tier critical    # 25 claims, ~30 sec

# Tier 2: Critical + Important (all key results)
python verify.py --tier important   # 100 claims, ~2 min

# Tier 3: Complete (every verifiable number)
python verify.py --tier complete    # 425 claims, ~10 min

# Specific table verification
python verify.py --table tab:claude_family

# Specific appendix section
python verify.py --appendix V  # Charitable feasibility

# Run all critical harnesses
python verify.py --harnesses
```

---

# Summary Statistics

| Metric | Value |
|--------|-------|
| **Total Claims** | 425 |
| Critical | 25 |
| Important | 72 |
| Complete | 325 |
| **Total Tables** | 25 |
| Main Paper | 6 |
| Appendix | 19 |
| **Total Table Cells** | ~350 |
| **Appendix Sections** | 24 (A-X) |
| **Verification Harnesses** | 6 |
| **Data Files** | 9 |
| **Smooth-regime Refutations** | 0/4,800 |

---

*Exhaustive claims index for NeurIPS 2026 submission*
