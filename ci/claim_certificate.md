# Claim Certificate

**Paper:** The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
**Generated:** 2026-05-03T11:35:10
**Verdict:** **PASS** — all structural checks clean (L1+L2+L4+L7+L8+L9+L10+L11+L12+L13+L14+L15); L3+L5+L16 coverage is advisory, see triage JSONs

## Provenance

- main.tex sha256: `0b7a6e3a4a589c41...` (full hash in JSON)
- main.tex mtime: 2026-05-03T10:53:55
- main.pdf size: 1,080,476 bytes
- main.pdf mtime: 2026-05-03T10:58:02
- registry sha256: `a860cd02ec4eaf6b...`
- certificate self-hash: `674acb417caea0c1...` (sha256 of this payload minus the hash field; recompute to verify integrity)

## Layer-by-layer Results

| Layer | Script | Status | Summary |
|---|---|---|---|
| L1_audit | `ci\claim_audit.py` | PASS | 152/152 verbatim |
| L2_validator | `ci\claim_audit_validator.py` | PASS | 14/14 checks pass |
| L3_sweep | `ci\claim_coverage_sweep.py` | PASS | 94.1% coverage, 66 uncovered |
| L4_lineage | `ci\figure_lineage_check.py` | PASS | 8/8 checks pass, 11 figures fresh |
| L5_figure_values | `ci\figure_value_check.py` | PASS | 77.6% overall figure coverage, 23 uncovered |
| L7_citations | `ci\citation_integrity_check.py` | PASS | 26 cites, 42 bib entries, 0 unresolved, 16 dead |
| L8_links | `ci\link_integrity_check.py` | PASS | 2 URLs, 122 refs / 166 labels, 0 unresolved, 44 dead labels |
| L9_consistency | `ci\cross_claim_consistency_check.py` | PASS | 33/33 consistency relations hold |
| L10_bib | `ci\bib_entry_check.py` | PASS | 42/42 bib entries well-formed |
| L11_scripts | `ci\script_integrity_check.py` | PASS | 13/13 figure scripts pass smoke test |
| L12_build_equiv | `ci\build_equivalence_check.py` | PASS | --quick mode: ?/15 figures fresh; full mode optional |
| L13_cross_tree | `ci\cross_tree_consistency_check.py` | PASS | 11/? cross-tree files match (incl. expected divergences) |
| L14_illustrations | `ci\illustration_lineage_check.py` | PASS | 7/7 illustration provenance checks pass |
| L15_data_ties | `ci\claim_data_ties_check.py` | PASS | 142/142 numerical claims tied to source data |
| L16_author_claims | `ci\author_claims_check.py` | PASS | 22/22 judgment claims have data anchors (100%; advisory) |

### Per-figure coverage (L5)

| Figure | Coverage | Uncovered |
|---|---|---|
| algorithm1_storyboard.pdf | 36.4% | 3 |
| gram_eigendecomposition.pdf | 59.7% | 12 |
| per_task_correlation.pdf | 83.9% | 6 |
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
```