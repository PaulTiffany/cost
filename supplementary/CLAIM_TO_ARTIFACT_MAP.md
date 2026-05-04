# Claim-to-Artifact Map

Paper: The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
Source manifests: ci/claim_data_ties.json, ci/claim_data_ties_results.json,
                  ci/cross_model_metadata_results.json
All values are L15-verified (304/304 ties PASS).

To run a single check:
  python ci/claim_data_ties_check.py 2>&1 | grep <claim_name>

---

## Section: Abstract / Core Falsification Result

| Claim | Paper Location | Source Artifact | L15 Value | Checker |
|---|---|---|---|---|
| 0/4,272 smooth-regime refutations (across 4 domains, predicted-infeasible region) | main.tex lines 73, 80, 123, 259, 509 | rebuttal/figures/unconditional_pivot_results.json | 4272 (smooth_total) | `python ci/claim_data_ties_check.py 2>&1 \| grep smooth_regime_total_4272` |
| 0 smooth-regime successes (refutation count) | main.tex lines 73, 80, 123, 259, 509 | rebuttal/figures/unconditional_pivot_results.json | 0 (smooth_successes) | `python ci/claim_data_ties_check.py 2>&1 \| grep smooth_regime_successes_zero` |
| 528 pivot-regime trials (across 4 domains) | main.tex line 80 | rebuttal/figures/unconditional_pivot_results.json | 528 (pivot_total) | `python ci/claim_data_ties_check.py 2>&1 \| grep pivot_regime_total_528` |
| 42 detected-pivot successes (out of 528) | main.tex lines 73, 80 | rebuttal/figures/unconditional_pivot_results.json | 42 (pivot_successes_estimated) | `python ci/claim_data_ties_check.py 2>&1 \| grep pivot_successes_42` |

Note: L9 cross-claim relation smooth_plus_pivot_eq_total enforces smooth_total + pivot_total
= 4800 (i.e., 4272 + 528 = 4800). See also CAVEAT_SMOOTH_TOTAL_DENOMINATOR in caveat_ledger.

Value expression for all four: d['full_paper_claim']['<field>'] from unconditional_pivot_results.json.

---

## Section: Cross-Model Benchmark (Figure 3a)

| Claim | Paper Location | Source Artifact | L15 Value | Checker |
|---|---|---|---|---|
| 9 models, 6 providers, N=3120 in cross-model experiment | main.tex lines 518, 525 | supplementary/experiments/code_constraint_results.json + rebuttal/figures/cross_model_results.json | n_models=9, n_providers=6, n_trials=3120 | `python ci/cross_model_metadata_check.py` |
| N=1839 trials (4-model blinded benchmark) | main.tex App C (cross-model) | rebuttal/figures/blinded_external/cross_model_results.json | 1839 (total_results) | `python ci/claim_data_ties_check.py 2>&1 \| grep cross_model_n_total_blinded` |
| text medium ~38% at high tier (4-model avg) | main.tex App C (cross-model) | rebuttal/figures/blinded_external/cross_model_results.json | 38 (tolerance +/-1) | `python ci/claim_data_ties_check.py 2>&1 \| grep cross_model_4model_avg_high_pct` |

For the 9-model check, values are computed by ci/cross_model_metadata_check.py reading both
data files and comparing to paper window lines 392-543.

Model list (from cross_model_metadata_results.json, computed field):
  Qwen2.5-Coder-1.5B, deepseek-coder-1.3b, TinyLlama-1.1B, Qwen2.5-3B,
  llama-3.1-70b, gemini-2.5-flash, deepseek-chat-v3, gpt-4o, gemini-2.5-pro

Provider list: Alibaba, DeepSeek, Google, Meta, OpenAI, TinyLlama

---

## Section: OpenRouter Regression (Appendix)

| Claim | Paper Location | Source Artifact | L15 Value | Checker |
|---|---|---|---|---|
| Test-axis regression at high tier = 1.7% (pooled 8 models, cond. on stage-1 pass_a) | main.tex App: Empirical Regression Rates | supplementary/experiments/openrouter_regression_results.json | 1.7 (tolerance +/-0.1) | `python ci/claim_data_ties_check.py 2>&1 \| grep openrouter_regression_test_pct_high` |
| 8 OpenRouter models in regression experiment | main.tex App (regression rates) | supplementary/experiments/openrouter_regression_results.json | 8 | `python ci/claim_data_ties_check.py 2>&1 \| grep openrouter_regression_n_models_8` |

---

## Section: Claude Family Frontier Transfer

| Claim | Paper Location | Source Artifact | L15 Value | Checker |
|---|---|---|---|---|
| opus-4 22.7x staging ratio (Table 4, row I18) | main.tex: Frontier transfer, Table 4 | supplementary/experiments/fixed_point_claude_family.json | 22.7 (tolerance +/-0.05) | `python ci/claim_data_ties_check.py 2>&1 \| grep claude_family_opus4_ratio` |
| opus-4.1 26.4x staging ratio (Table 4, row I19, highest) | main.tex: Frontier transfer, Table 4 | supplementary/experiments/fixed_point_claude_family.json | 26.4 (tolerance +/-0.05) | `python ci/claim_data_ties_check.py 2>&1 \| grep claude_family_opus41_ratio` |
| opus-4.5 4.8x staging ratio (Table 4, row I22, scaling paradox row) | main.tex: Frontier transfer, Table 4 | supplementary/experiments/fixed_point_claude_family.json | 4.8 (tolerance +/-0.05) | `python ci/claim_data_ties_check.py 2>&1 \| grep claude_family_opus45_ratio` |
| opus-4.5 wins 4/6 tasks (scaling-paradox row) | main.tex: Frontier transfer | supplementary/experiments/fixed_point_claude_family.json | 4 | `python ci/claim_data_ties_check.py 2>&1 \| grep claude_family_opus45_wins` |

Note: claim_id claude_family_opus45_wins corresponds to L15 name "claude_family_opus45_wins"
(not "claude_family_opus45_wins" -- see upstream prompt naming). The wins check uses
exact integer match (tolerance=0.0).

---

## Section: Encoder Lipschitz / Displacement Contract

| Claim | Paper Location | Source Artifact | L15 Value | Checker |
|---|---|---|---|---|
| p99 per-token displacement = 0.246 (Step-Size Distribution) | main.tex: Displacement Contract, Table 1 | supplementary/experiments/lipschitz_calibration_results.json | 0.246 (tolerance +/-0.01) | `python ci/claim_data_ties_check.py 2>&1 \| grep lipschitz_pooled_p99` |
| Maximum observed per-token displacement = 0.447 | main.tex: Table 1 | supplementary/experiments/lipschitz_calibration_results.json | 0.447 (tolerance +/-0.01) | `python ci/claim_data_ties_check.py 2>&1 \| grep lipschitz_pooled_max` |

---

## How to Run the Full Check

  python ci/claim_data_ties_check.py

Prints one line per claim. PASS/FAIL with computed vs expected values.
Exits 0 if all 304 ties pass.

To filter to a specific claim:
  python ci/claim_data_ties_check.py 2>&1 | grep <claim_name>

To check cross-model metadata separately:
  python ci/cross_model_metadata_check.py
