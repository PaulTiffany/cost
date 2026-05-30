# Claim Certificate (Reviewer Summary)

## Verdict

**PASS**

- Paper: The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
- Venue: arXiv preprint (preprint mode)
- Mode: `quick`
- Generated: 2026-05-30T19:51:41
- Rationale: all structural checks clean (L1+L2+L4+L7+L8+L9+L10+L11+L12+L13+L14+L15+L27+L28+L29+L30+L30_audit_observer_purity+L31+L32+L33+L34); L3+L5+L16 coverage is advisory, see triage JSONs

## What this certificate proves and does NOT prove

**Mechanically verified:**

- Every numeric claim with a PASS row below was recomputed from the
  committed source data file and matched the paper text exactly.
- Cross-claim arithmetic relations hold (decompositions, formulas,
  table values).
- All figure assets are fresh relative to their generating scripts.
- All citations resolve; all cross-references resolve.
- All 149 registered claims appear verbatim in the paper.

**NOT certified:**

- Theorem proofs (math correctness beyond the encoded arithmetic
  relations checked by L9).
- Experimental design quality or statistical power.
- Scientific judgment (whether the benchmark tasks are representative,
  whether the rubric is well-calibrated).
- Real-world truth of the claims about language model behavior.
- API model version identity (see CAVEAT_FRONTIER_API_DRIFT).

## Threat model -- what this cert catches and misses

**Designed to catch:**

- Stale paper text after data updates (L15 data ties, L1 verbatim audit).
- Stale figure assets after script or data edits (L4 lineage, L12 build equiv).
- Wrong denominator on a true number, e.g., '4,800 smooth-regime' when
  smooth_total=4,272 (L9 decomposition + L15 paper_render_negate).
- Table row/column substitution where all numbers still appear somewhere
  (L9 cross-claim consistency, L2 validator).
- Aggregate JSON edited without raw-output or script provenance (L20).
- Reviewer bundle missing files referenced by the certificate (L13 cross-tree).

**Does NOT catch:**

- Coordinated source + data + paper fraud with synchronized edits.
- API model version drift (provider can update silently; see caveat).
- Manual scoring rubric quality (image transfer Pass-B; see caveat).
- Whether benchmark task distribution is representative.
- Statistical interpretation errors in prose.

## Headline claims

*Flagship numerical claims from L15; all 325/325 ties pass.*

| Claim excerpt | Section | Source artifact | Status |
|---|---|---|---|
| N=1839 trials (4-model blinded benchmark) | App C (image-format text-medium comparator) | `cross_model_results.json` | PASS (L15) |
| text medium ~38% at high tier (4-model avg) | App C (image-format text-medium comparator) | `cross_model_results.json` | PASS (L15) |
| 8 OpenRouter models in regression experiment (gemini-flash, gpt-4o-... | App (regression rates) | `openrouter_regression_results.json` | PASS (L15) |
| Test-axis regression at high tier = 1.7% (pooled across 8 models, c... | App (regression rates) | `openrouter_regression_results.json` | PASS (L15) |
| 10 Claude models in Tab:claude_family (canonical 7 plus opus-4.6, s... | Frontier transfer | `fixed_point_claude_family_full10.json` | PASS (L15) |
| opus-4 22.7x staging ratio (I18, Table 4) | Frontier transfer | `fixed_point_claude_family.json` | PASS (L15) |
| N=24 trials total in Run D | App C (image transfer) | `image_transfer_runD_passB.json` | PASS (L15) |

## Caveats

### Science
- **CAVEAT_SMOOTH_TOTAL_DENOMINATOR**: Smooth-regime denominator corrected from 4,800 to 4,272 after excluding pivot-regime overlap.
  - Remaining risk: If the source JSON is regenerated with different domain subsets, smooth_total could shift without triggering a git diff on paper text. L15 check would catch this.
- **CAVEAT_MANUAL_IMAGE_SCORING**: Image-medium transfer (Run D) Pass-B scores were assigned by manual human rater review, not automated.
  - Remaining risk: Rubric subjectivity means two careful raters could disagree on borderline cases. Effect sizes in the paper (33% at high tier) are large enough that modest rater disagreement would not reverse the qualitative conclusion, but exact percentages should be treated as approximate.
- **CAVEAT_SMALL_N_IMAGE_TRANSFER**: Image-transfer Run D uses N=24 trials total (8 per tier), which is small for statistical inference.
  - Remaining risk: Readers who focus on the 33% figure without noting N=24 may over-interpret precision. The cert cannot enforce adequate hedging language in prose.

### Reproducibility
- **CAVEAT_CLOSED_MODEL_RERUN**: Experiments querying closed frontier models (Claude, GPT, Gemini, Command-R+) cannot be re-run by reviewers without API access and budget.
  - Remaining risk: Stored JSONs could in principle be manually edited to match incorrect paper text. Coordinated fraud of this form is outside the cert's threat model.
- **CAVEAT_FRONTIER_API_DRIFT**: Model versions accessed via OpenRouter and Anthropic API can be silently updated by the provider; re-running scripts on a later date may query a different model weight than when results were recorded.
  - Remaining risk: There is no cryptographic proof that the stored JSON was produced by the stated model version. Provider attestation would be needed for that level of assurance, which is not currently available.

### Advisory
- **CAVEAT_SCHEMATIC_NOT_EVIDENCE**: Several figures are author-drawn schematics illustrating the geometric argument, not empirical data plots.
  - Remaining risk: A reviewer might mistake a schematic for a data figure. The cert cannot enforce caption clarity.

## Spot-check recipes

**1. smooth_regime_total_4272**
- Open: `rebuttal/figures/unconditional_pivot_results.json`
- Compute: `d['full_paper_claim']['smooth_total']`
- Compare to: paper/main.tex near '0/4,272 smooth-regime'
- Re-run check: `python ci/claim_data_ties_check.py`

**2. N=1839 trials (4-model blinded benchmark)**
- Open: `rebuttal/figures/blinded_external/cross_model_results.json`
- Compute: `d['total_results']`
- Compare to: paper/main.tex near 'N=1,839'
- Re-run check: `python ci/cross_model_metadata_check.py`

**3. Test-axis regression at high tier = 1.7% (pooled across 8 models, conditional on**
- Open: `supplementary/experiments/openrouter_regression_results.json`
- Compute: `regression rate at high tier (pooled 8 models)`
- Compare to: paper/main.tex Empirical Regression Rates section
- Re-run check: `python ci/claim_data_ties_check.py`

## Layer summary

*PASS/FAIL only. See `ci/claim_certificate.md` for per-layer detail.*

| Layer | Status |
|---|---|
| L1_audit | PASS |
| L2_validator | PASS |
| L3_sweep | PASS |
| L4_lineage | PASS |
| L5_figure_values | PASS |
| L7_citations | PASS |
| L8_links | PASS |
| L9_consistency | PASS |
| L10_bib | PASS |
| L11_scripts | PASS |
| L12_build_equiv | PASS |
| L13_cross_tree | SKIP |
| L14_illustrations | PASS |
| L15_data_ties | PASS |
| L16_author_claims | PASS |
| L17_table_values | PASS |
| L18_sample_size_adequacy | PASS |
| L19_ci_coverage | PASS |
| L20_cross_source_recompute | PASS |
| L21_sbom | PASS |
| L22_container_lineage | PASS |
| L23_license_clearance | PASS |
| L24_pdf_camera_ready | PASS |
| L25_multi_seed_drift | PASS |
| L26_reference_convention | PASS |
| L27_stat_algo_sanity | PASS |
| L28_symbolic_algebra | PASS |
| L29_numerical_bounds | PASS |
| L30_per_trajectory_pivot | PASS |
| L30_audit_observer_purity | PASS |
| L31_audit_observer_runtime | PASS |
| L32_paper_surface | PASS |
| L33_caption_grounding | PASS |
| L34_page_check | PASS |

Full certificate JSON: `ci/claim_certificate.json` (self-hash: `ea85faf59c87ddd2...`)

## Reviewer FAQ (pre-answered)

### 1. Which headline claims are mechanically verified?

Claims with both data-identity AND paper-locality verification (`paper_render_pattern` set):

- **high_tier_total_1365**: 0/1,365 high-tier refutations under audit-observer measurement _(location: main.tex lines 86, 137, 273, 523, 550)_
- **high_tier_refutations_zero**: 0 high-tier refutations (audit-observer falsification claim) _(location: main.tex lines 86, 137, 273, 523)_
- **audit_observer_pivot_total_3962**: 3,962 pivot-regime trials under audit-observer measurement _(location: main.tex - Smooth/pivot decomposition appendix (4%/96% split))_
- **audit_observer_high_tier_passes_923**: 923 high-tier passes (67.6% pooled) under audit observer _(location: main.tex - Pivot Regime in the Predicted-Infeasible Region)_

### 2. Which claims rely on closed-model API outputs?

These result files were produced by querying closed frontier model APIs. They are observational records -- **not bitwise reproducible** without API access and equivalent model versions.

- `rebuttal/figures/blinded_external/cross_model_results.json`
- `rebuttal/figures/cross_model_results.json`
- `supplementary/experiments/fixed_point_claude_family.json`
- `supplementary/experiments/fixed_point_claude_family_full10.json`
- `supplementary/experiments/fixed_point_claude_family_opus46_addition.json`
- `supplementary/experiments/fixed_point_claude_family_opus47_addition.json`
- `supplementary/experiments/fixed_point_claude_family_sonnet46_addition.json`
- `supplementary/experiments/fixed_point_floor_opus_43.json`
- `supplementary/experiments/fixed_point_model_family_opus46_addition.json`
- `supplementary/experiments/fixed_point_model_family_opus47_addition.json`
- `supplementary/experiments/fixed_point_model_family_sonnet46_addition.json`
- `supplementary/experiments/openrouter_regression_results.json`
- `supplementary/experiments/outputs/high_k_opus/high_k_opus_results.json`
- `supplementary/experiments/outputs/high_k_opus46/high_k_opus46_results.json`
- `supplementary/experiments/outputs/high_k_opus47/high_k_opus47_results.json`
- `supplementary/experiments/outputs/high_k_sonnet46/high_k_sonnet46_results.json`
- `supplementary/experiments/outputs/implicit_k/implicit_k_results.json`
- `supplementary/experiments/outputs/policy_density/policy_density_results.json`

### 3. Which claims rely on manual scoring?

Passed manual-scoring files (rubric applied by human rater):

- `supplementary/experiments_rebuttal/image_transfer/image_transfer_runD_passB.json` (rubric_hash: `e410813316931ec8...`)

### 4. Which figures are empirical vs schematic?

**Schematics** (3) -- author-drawn TikZ/SVG illustrations, not empirical data plots:

- `staging_vs_refine.tex`: schematic illustration only; not empirical evidence. Three-protocol compariso...
- `algorithm1_routing.tex`: schematic illustration only; not empirical evidence. Decision-tree shape of A...
- `interface_assumption.tex`: schematic illustration only; not empirical evidence. Hand-authored from the s...

**Empirical figures** (21) -- generated from experiment data:

- `algorithm1_storyboard.pdf`
- `gram_eigendecomposition.pdf`
- `per_task_correlation.pdf`
- `constrained_decoding.pdf`
- `cross_model_cliff.pdf`
- `soft_constraint_cliff.pdf`
- `related_envelope_combined.tex`
- `limitations_envelope.tex`
- `related_work_quadrant.tex`
- `interface_assumption.tex`
- `algorithm1_routing.tex`
- `staging_vs_refine.tex`
- `image_transfer.pdf`
- `image_transfer_conflict.pdf`
- `image_format_cliff.pdf`
- `proxy_ablation.pdf`
- `sonification_defense.pdf`
- `sonification_defense_rho_sweep.pdf`
- `prompt_length_sweep.png`
- `constitution_wheel_full.wav`
- `diagonal_all_four.wav`

### 5. Which warnings affect scientific interpretation?

- **CAVEAT_SMOOTH_TOTAL_DENOMINATOR**: Smooth-regime denominator corrected from 4,800 to 4,272 after excluding pivot-regime overlap. -- applies to: smooth_regime_total_4272, smooth_regime_successes_zero
- **CAVEAT_MANUAL_IMAGE_SCORING**: Image-medium transfer (Run D) Pass-B scores were assigned by manual human rater review, not automated. -- applies to: runD_high_pass_b_pct, runD_control_pass_b_pct, runD_n_trials_total
- **CAVEAT_SMALL_N_IMAGE_TRANSFER**: Image-transfer Run D uses N=24 trials total (8 per tier), which is small for statistical inference. -- applies to: runD_n_trials_total, runD_high_pass_b_pct, cross_model_4model_avg_high_pct

### 6. How do I verify one claim locally?

Three example spot-check commands:

```bash
# 1. Re-run all L15 data-tie checks (full registry):
python ci/claim_data_ties_check.py

# 2. Re-run the cross-model metadata check:
python ci/cross_model_metadata_check.py

# 3. Re-run the full claim audit (L1 verbatim):
python ci/claim_audit.py
```

### 7. What changed since the previous certificate?

See `ci/CERTIFICATE_CHANGELOG.md` for the full diff history between certificate versions.
