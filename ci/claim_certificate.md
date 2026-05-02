# Claim Certificate

**Paper:** The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
**Generated:** 2026-05-02T14:04:38
**Verdict:** **PASS** — all structural checks clean; coverage above thresholds

## Provenance

- main.tex sha256: `ead744fa65b868e4...` (full hash in JSON)
- main.tex mtime: 2026-05-02T13:12:58
- main.pdf size: 1,009,654 bytes
- main.pdf mtime: 2026-05-02T13:35:40
- registry sha256: `6612f0791ba6df43...`

## Layer-by-layer Results

| Layer | Script | Status | Summary |
|---|---|---|---|
| L1_audit | `ci\claim_audit.py` | PASS | 97/97 verbatim |
| L2_validator | `ci\claim_audit_validator.py` | PASS | 13/13 checks pass |
| L3_sweep | `ci\claim_coverage_sweep.py` | PASS | 66.4% coverage, 507 uncovered |
| L4_lineage | `ci\figure_lineage_check.py` | PASS | 6/6 checks pass, 7 figures fresh |
| L5_figure_values | `ci\figure_value_check.py` | PASS | 58.8% overall figure coverage, 36 uncovered |

### Per-figure coverage (L5)

| Figure | Coverage | Uncovered |
|---|---|---|
| algorithm1_storyboard.pdf | 18.2% | 4 |
| gram_eigendecomposition.pdf | 20.9% | 17 |
| per_task_correlation.pdf | 80.6% | 8 |
| constrained_decoding.pdf | 85.7% | 1 |
| cross_model_cliff.pdf | 97.5% | 1 |
| soft_constraint_cliff.pdf | 0.0% | 5 |

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
```