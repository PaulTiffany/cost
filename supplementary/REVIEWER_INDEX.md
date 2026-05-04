# Reviewer Index

Paper: The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
Venue: NeurIPS 2026 (anonymous double-blind submission)
Certificate: PASS -- 16 layers, L9=60/60 relations, L15=304/304 data ties, 110-file bundle

---

## 1. What This Paper Claims

From paper/main.tex (Contributions paragraph, line 124):

(1) Tight Geometric Bound: Two-constraint (Thm 1), k-constraint Gram-matrix (Thm 2, Cor 1),
    staging efficiency (Thm 3).
(2) Displacement Contract: Empirically calibrated token-to-displacement interface; Table 1
    shows stable calibration across model families.
(3) Staging Decomposition: Sequential satisfaction has provably lower cost; Table 2 shows
    up to 4.8x improvement.
(4) Regime Index: rho_hat orders failure regimes (r_s=1.0; 94% router agreement), enabling
    fail-safe routing (Figure: feasibility_surface).
(5) Judge-Free Validation: Deterministic verifiers across four domains (Table: harness_suite);
    0/4,272 smooth-regime refutations; transfers to frontier (Table: claude_family).

---

## 2. What Is Mechanically Certified

Verdict: PASS (generated 2026-05-04T00:43:46)

Generated from: ci/claim_certificate.json

Layer results (all 16 layers run):

  L1_audit        PASS  149/149 registered claims found verbatim in paper (0 drift, 0 missing)
  L2_validator    PASS  14/14 structural schema checks passed
  L3_sweep        PASS  92.9% numeric coverage (1104/1188 tokens; advisory only)
  L4_lineage      PASS  8/8 figure lineage checks; 11 figures in use
  L5_figure_vals  PASS  77.6% figure value coverage (advisory; 3 of 7 figures at 100%)
  L7_citations    PASS  30 citations in paper, 0 unresolved
  L8_links        PASS  2 URLs, 0 malformed, 0 unresolved refs
  L9_consistency  PASS  60/60 cross-claim arithmetic relations
  L10_bib         PASS  42/42 bib entries well-formed
  L11_scripts     PASS  13/13 CI scripts pass integrity check
  L12_build_equiv PASS  7/7 active checks pass (8 skipped in quick mode)
  L13_cross_tree  PASS  12/12 cross-tree pairs match (1 diverged as expected)
  L14_illustr     PASS  7/7 illustration lineage checks
  L15_data_ties   PASS  304/304 numeric claims tied to source data files
  L16_author      PASS  22/22 author claims tied (100%)

What the cert does NOT verify: theorem proofs, experimental design quality, statistical power,
real-world model behavior, API model version identity.

---

## 3. What Is in the Bundle

Grouped by role (source: ci/submission_surface_manifest.json, 110 files total).

### evidence_asset (ships to reviewers; load-bearing for claims)
  paper/main.tex                       -- primary paper source
  paper/main.pdf                       -- compiled PDF (must match main.tex)
  paper/figures/algorithm1_storyboard.pdf
  paper/figures/constrained_decoding.pdf
  paper/figures/cross_model_cliff.pdf  -- 9 models, 6 providers, N=3120
  paper/figures/gram_eigendecomposition.pdf
  paper/figures/image_format_cliff.pdf -- N=1839 blinded text-medium comparator
  paper/figures/image_transfer.pdf
  paper/figures/image_transfer_conflict.pdf
  paper/figures/per_task_correlation.pdf
  paper/figures/soft_constraint_cliff.pdf
  paper/figures/*.tex (6 TikZ sources for schematic figures)
  supplementary/experiments/outputs/charitable/*.tex (2 generated tables)

### raw_observation (runD outputs shipped for spot-check)
  supplementary/experiments_rebuttal/image_transfer/outputs/runD/[23 PNGs]

### reviewer_aid (supplementary material, not evidence)
  supplementary/demos/audio_demos/constitution_wheel_full.wav
  supplementary/demos/audio_demos/diagonal_all_four.wav
  supplementary/demos/interactive_demo.ipynb
  supplementary/demos/sonification.py
  rebuttal/figures/prompt_length_sweep.png
  rebuttal/figures/proxy_ablation.pdf
  rebuttal/figures/sonification_defense.pdf
  rebuttal/figures/sonification_defense_rho_sweep.pdf

### provenance_source (not shipped but hashed; backs evidence_asset files)
  rebuttal/figures/unconditional_pivot_results.json  -- backs 0/4272 claim
  rebuttal/figures/cross_model_results.json          -- backs N=3120 figure
  rebuttal/figures/blinded_external/cross_model_results.json  -- N=1839
  supplementary/experiments/openrouter_regression_results.json
  supplementary/experiments/fixed_point_claude_family.json
  supplementary/experiments_rebuttal/constrained_decoding/constrained_decoding_results.json
  supplementary/experiments_rebuttal/image_transfer/image_transfer_results_runD.json
  supplementary/experiments/lipschitz_calibration_results.json
  supplementary/experiments/code_constraint_results.json
  ci/claim_data_ties.json
  ci/claim_certificate.json
  ci/figure_lineage.json
  ci/derivations/*.json (7 derived value files)
  ... (additional provenance JSONs; see ci/submission_surface_manifest.json)

### internal_or_excluded (not shipped; archive or pilot runs)
  supplementary/experiments_rebuttal/image_transfer/outputs/runA, runB, runC
  supplementary/demos/mobile_theorem34_demo.ipynb  -- stale venue tokens, excluded
  supplementary/GRADED_METRICS_SPEC.md             -- draft planning doc, excluded
  supplementary/demos/audio_demos/[29 non-curated WAVs]

---

## 4. Three Fastest Spot Checks

From ci/claim_certificate_reviewer.md:

CHECK 1: 0/4,272 smooth-regime refutations
  Open:    rebuttal/figures/unconditional_pivot_results.json
  Compute: d['full_paper_claim']['smooth_total']  -- expect 4272
  Rerun:   python ci/claim_data_ties_check.py 2>&1 | grep smooth_regime_total_4272

CHECK 2: N=1,839 blinded cross-model benchmark
  Open:    rebuttal/figures/blinded_external/cross_model_results.json
  Compute: d['total_results']  -- expect 1839
  Rerun:   python ci/cross_model_metadata_check.py

CHECK 3: High-tier regression rate = 1.7% (8 OpenRouter models)
  Open:    supplementary/experiments/openrouter_regression_results.json
  Compute: regression rate at high tier (pooled 8 models, conditional on stage-1 pass_a)
  Rerun:   python ci/claim_data_ties_check.py 2>&1 | grep openrouter_regression_test_pct_high

---

## 5. Known Caveats

From ci/caveat_ledger.json (6 caveats, all blocking=false).

### Science
- CAVEAT_SMOOTH_TOTAL_DENOMINATOR: Smooth-regime denominator corrected from 4,800 to 4,272
  after excluding pivot-regime overlap. Falsification claim (0 successes) unchanged; only
  denominator changed. L9 relation smooth_plus_pivot_eq_total enforces 4272+528=4800.
- CAVEAT_MANUAL_IMAGE_SCORING: Image-transfer Run D Pass-B scores assigned by manual human
  rater, not automated. N=24 trials; borderline rater disagreement could shift exact percentages
  but not qualitative direction (effect sizes large).
- CAVEAT_SMALL_N_IMAGE_TRANSFER: Run D has N=24 total (8 per tier). 95% CI on 33% high-tier
  rate spans roughly 10-65%. Presented as replication, not primary result.

### Reproducibility
- CAVEAT_CLOSED_MODEL_RERUN: Frontier model experiments (Claude, GPT, Gemini, Command-R+)
  require API access and budget to rerun. Stored result JSONs are committed; L15 verifies
  paper numbers match stored values.
- CAVEAT_FRONTIER_API_DRIFT: API providers may silently update model weights. Re-running later
  may query a different model version. Stored JSONs are the ground truth for this submission.

### Advisory
- CAVEAT_SCHEMATIC_NOT_EVIDENCE: Figures staging_vs_refine, algorithm1_routing, and
  interface_assumption are author-drawn pedagogical schematics, not data plots. Captions
  identify them as illustrative.

---

## 6. Where to Look First

- Core falsification result: Figure cross_model_cliff.pdf + paper Sec 4 / Abstract
- Staging benefit (4.8x): Table delta_capacity in paper Sec 3 / Contributions (3)
- Frontier transfer: Table claude_family (7 Claude models, opus-4.5 26.4x ratio)
- Regime index calibration: Table lipschitz_calibration (p99 displacement = 0.246)
- Cross-model cliff plot (9 models): paper/figures/cross_model_cliff.pdf
- Gram eigendecomposition (geometric mechanism): paper/figures/gram_eigendecomposition.pdf

---

## 7. What This Is NOT

- This is NOT a certified theorem prover. Math correctness (beyond arithmetic consistency)
  is not machine-verified.
- This is NOT an audit of experimental design quality or task representativeness.
- This is NOT independent replication. Closed-model experiments cannot be independently
  re-run without API access (see CAVEAT_CLOSED_MODEL_RERUN).
- The audio demos (WAV files) are perceptual illustrations only; they are NOT evidence figures.
- The interactive notebook (interactive_demo.ipynb) is a reviewer aid; its outputs are NOT
  registered as evidence.
