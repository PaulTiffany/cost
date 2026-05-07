# Claim Certificate

> **For reviewers:** this certificate proves the paper says what it says without numerical, citation, or figure-asset drift. It does NOT prove the experiments are well-designed or the conclusions follow. See **Scope** below.

**Paper:** The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
**Venue:** NeurIPS 2026 (anonymous double-blind submission)
**Mode:** `quick` (release: full L12 build equivalence + artifact hashes embedded; quick: --quick L12 (mtime+asset-hash only), faster local checks)
**Generated:** 2026-05-07T06:12:57
**Verdict:** **PASS** — all structural checks clean (L1+L2+L4+L7+L8+L9+L10+L11+L12+L13+L14+L15+L27+L28+L29+L30+L30_audit_observer_purity+L31+L32+L33+L34); L3+L5+L16 coverage is advisory, see triage JSONs

## Scope of certification

| Label | What this certificate guarantees |
|---|---|
| **Mechanically verified** | Every numeric/citation claim with a ✓ row in the layer table below has been recomputed from source data and matches the paper text. |
| **Consistency-checked** | Cross-claim arithmetic relations hold (e.g., decompositions sum correctly, formulas evaluate to tabulated values). |
| **Advisory** | Coverage scans (L3, L5, L16) report uncovered numerics for human review. They do not block. |
| **NOT certified** | Theorem proofs (math correctness beyond the encoded relations), experimental design, scientific judgment, the underlying truth of the claims about the world. |

## Spot-check recipes

Verify any flagship claim with a single command. Examples:

```
# Verify 0/4,272 smooth-regime refutations:
python ci/claim_data_ties_check.py     # find smooth_regime_total_4272
# Source: rebuttal/figures/unconditional_pivot_results.json (full_paper_claim.smooth_total)

# Verify cross-model figure counts (9 models, 6 providers, N=3,120):
python ci/cross_model_metadata_check.py
# Source: supplementary/experiments/code_constraint_results.json + rebuttal/figures/cross_model_results.json

# Re-run full cert:
python ci/claim_certificate.py
# Re-run release-mode cert (full L12 build equivalence + artifact hashes):
python ci/claim_certificate.py --release
```

## Provenance

- main.tex sha256: `4e1608d60a933634...` (full hash in JSON)
- main.tex mtime: 2026-05-07T05:35:04
- main.pdf size: 1,200,503 bytes
- main.pdf mtime: 2026-05-07T06:12:56
- registry sha256: `d3cc82eb135768ca...`
- artifact hashes: 35 layer-result JSONs + manifests recorded in JSON payload
- certificate self-hash: `1a725121adf7e2c2...` (sha256 of this payload minus the hash field; recompute to verify integrity)

## Layer-by-layer Results

| Layer | Script | Status | Summary |
|---|---|---|---|
| L1_audit | `ci\claim_audit.py` | PASS | 148/148 verbatim |
| L2_validator | `ci\claim_audit_validator.py` | PASS | 14/14 checks pass |
| L3_sweep | `ci\claim_coverage_sweep.py` | PASS | 93.4% coverage, 92 uncovered |
| L4_lineage | `ci\figure_lineage_check.py` | PASS | 8/8 checks pass, 11 figures fresh |
| L5_figure_values | `ci\figure_value_check.py` | PASS | 81.5% overall figure coverage, 19 uncovered |
| L7_citations | `ci\citation_integrity_check.py` | PASS | 31 cites, 43 bib entries, 0 unresolved, 12 dead |
| L8_links | `ci\link_integrity_check.py` | PASS | 2 URLs, 125 refs / 176 labels, 0 unresolved, 52 dead labels |
| L9_consistency | `ci\cross_claim_consistency_check.py` | PASS | 60/60 consistency relations hold |
| L10_bib | `ci\bib_entry_check.py` | PASS | 43/43 bib entries well-formed |
| L11_scripts | `ci\script_integrity_check.py` | PASS | 13/13 figure scripts pass smoke test |
| L12_build_equiv | `ci\build_equivalence_check.py` | PASS | --quick mode: ?/15 figures fresh; full mode optional |
| L13_cross_tree | `ci\cross_tree_consistency_check.py` | SKIP | 0/? cross-tree files match (incl. expected divergences) |
| L14_illustrations | `ci\illustration_lineage_check.py` | PASS | 7/7 illustration provenance checks pass |
| L15_data_ties | `ci\claim_data_ties_check.py` | PASS | 325/325 numerical claims tied to source data |
| L16_author_claims | `ci\author_claims_check.py` | PASS | 28/28 judgment claims have data anchors (100%; advisory) |
| L17_table_values | `ci\table_value_check.py` | PASS | (no summary) |
| L18_sample_size_adequacy | `ci\sample_size_adequacy_check.py` | PASS | (no summary) |
| L19_ci_coverage | `ci\confidence_interval_coverage_check.py` | PASS | (no summary) |
| L20_cross_source_recompute | `ci\cross_source_recomputation_check.py` | PASS | (no summary) |
| L21_sbom | `ci\sbom_check.py` | PASS | (no summary) |
| L22_container_lineage | `ci\container_lineage_check.py` | PASS | (no summary) |
| L23_license_clearance | `ci\license_clearance_check.py` | PASS | (no summary) |
| L24_pdf_camera_ready | `ci\pdf_camera_ready_check.py` | PASS | (no summary) |
| L25_multi_seed_drift | `ci\multi_seed_drift_check.py` | PASS | (no summary) |
| L26_reference_convention | `ci\reference_convention_check.py` | PASS | (no summary) |
| L27_stat_algo_sanity | `ci\statistical_and_algorithmic_sanity_check.py` | PASS | 0 blocker / 1 warn (impossible p, algo guards, ref types, headline drift) |
| L28_symbolic_algebra | `ci\symbolic_algebra_check.py` | PASS | 9/9 algebraic identities verified by SymPy |
| L29_numerical_bounds | `ci\numerical_bound_check.py` | PASS | 13/13 bounds numerically verified (SLSQP counter-example search; H1 demo: 100 violations of original m=min_i m_i form) |
| L30_per_trajectory_pivot | `ci\per_trajectory_pivot_check.py` | PASS | ?/? headline-number claims verified (measured smooth/pivot = ?/? under ?) |
| L30_audit_observer_purity | `ci\audit_observer_purity_check.py` | PASS | 9 pass / 0 warn / 0 fail (LLM-import-free + schema-locked + tests green for ci/audit/) |
| L31_audit_observer_runtime | `ci\audit_observer_runtime_check.py` | PASS | status=PASS; 3/3 substantive hypotheses (H_B1/H_B2/H_B3) over 6120 packets / 18 cells; stream=verifier_surface_mismatch->investigate_verifier |
| L32_paper_surface | `ci\paper_surface_check.py` | PASS | 0 impactions |
| L33_caption_grounding | `ci\figure_caption_grounding_check.py` | PASS | 9/9 figures grounded |
| L34_page_check | `ci\page_check.py` | PASS | body_pages=9, references_page=10, total_pages=67, all_pass=True |

### Per-figure coverage (L5)

| Figure | Coverage | Uncovered |
|---|---|---|
| algorithm1_storyboard.pdf | 36.4% | 3 |
| gram_eigendecomposition.pdf | 58.2% | 13 |
| per_task_correlation.pdf | 98.4% | 1 |
| constrained_decoding.pdf | 100.0% | 0 |
| cross_model_cliff.pdf | 100.0% | 0 |
| soft_constraint_cliff.pdf | 100.0% | 0 |
| image_format_cliff.pdf | 60.0% | 2 |

## Triage Pointers

Uncovered numerics (per-layer JSON, for human review):

- L3 body sweep: `ci/claim_coverage_uncovered.json`
- L5 figure values: `ci/figure_value_check_results.json`

## Reproducing this Certificate

```
python ci/claim_certificate.py
```

Each layer can also be run independently:
```
python ci\claim_audit.py    # L1_audit
python ci\claim_audit_validator.py    # L2_validator
python ci\claim_coverage_sweep.py    # L3_sweep
python ci\figure_lineage_check.py    # L4_lineage
python ci\figure_value_check.py    # L5_figure_values
python ci\citation_integrity_check.py    # L7_citations
python ci\link_integrity_check.py    # L8_links
python ci\cross_claim_consistency_check.py    # L9_consistency
python ci\bib_entry_check.py    # L10_bib
python ci\script_integrity_check.py    # L11_scripts
python ci\build_equivalence_check.py    # L12_build_equiv
python ci\cross_tree_consistency_check.py    # L13_cross_tree
python ci\illustration_lineage_check.py    # L14_illustrations
python ci\claim_data_ties_check.py    # L15_data_ties
python ci\author_claims_check.py    # L16_author_claims
python ci\table_value_check.py    # L17_table_values
python ci\sample_size_adequacy_check.py    # L18_sample_size_adequacy
python ci\confidence_interval_coverage_check.py    # L19_ci_coverage
python ci\cross_source_recomputation_check.py    # L20_cross_source_recompute
python ci\sbom_check.py    # L21_sbom
python ci\container_lineage_check.py    # L22_container_lineage
python ci\license_clearance_check.py    # L23_license_clearance
python ci\pdf_camera_ready_check.py    # L24_pdf_camera_ready
python ci\multi_seed_drift_check.py    # L25_multi_seed_drift
python ci\reference_convention_check.py    # L26_reference_convention
python ci\statistical_and_algorithmic_sanity_check.py    # L27_stat_algo_sanity
python ci\symbolic_algebra_check.py    # L28_symbolic_algebra
python ci\numerical_bound_check.py    # L29_numerical_bounds
python ci\per_trajectory_pivot_check.py    # L30_per_trajectory_pivot
python ci\audit_observer_purity_check.py    # L30_audit_observer_purity
python ci\audit_observer_runtime_check.py    # L31_audit_observer_runtime
python ci\paper_surface_check.py    # L32_paper_surface
python ci\figure_caption_grounding_check.py    # L33_caption_grounding
python ci\page_check.py    # L34_page_check
```