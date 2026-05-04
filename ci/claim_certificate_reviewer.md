# Claim Certificate (Reviewer Summary)

## Verdict

**PASS**

- Paper: The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
- Venue: NeurIPS 2026 (anonymous double-blind submission)
- Mode: `quick`
- Generated: 2026-05-03T23:52:34
- Rationale: all structural checks clean (L1+L2+L4+L7+L8+L9+L10+L11+L12+L13+L14+L15); L3+L5+L16 coverage is advisory, see triage JSONs

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

*Flagship numerical claims from L15; all 304/304 ties pass.*

| Claim excerpt | Section | Source artifact | Status |
|---|---|---|---|
| 0/4,272 smooth-regime refutations (across 4 domains, predicted-infe... | Abstract / Results | `unconditional_pivot_results.json` | PASS (L15) |
| 0 smooth-regime successes (refutation count) | Abstract / Results | `unconditional_pivot_results.json` | PASS (L15) |
| 528 pivot-regime trials (across 4 domains) | Results | `unconditional_pivot_results.json` | PASS (L15) |
| N=1839 trials (4-model blinded benchmark) | App C (cross-model) | `cross_model_results.json` | PASS (L15) |
| text medium ~38% at high tier (4-model avg) | App C (cross-model) | `cross_model_results.json` | PASS (L15) |
| 8 OpenRouter models in regression experiment (gemini-flash, gpt-4o-... | App (regression rates) | `openrouter_regression_results.json` | PASS (L15) |
| Test-axis regression at high tier = 1.7% (pooled across 8 models, c... | App (regression rates) | `openrouter_regression_results.json` | PASS (L15) |
| 7 Claude family models analyzed (haiku-3 through opus-4.5) | Frontier transfer | `fixed_point_claude_family.json` | PASS (L15) |
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

**1. 0/4,272 smooth-regime refutations (across 4 domains, predicted-infeasible region**
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
| L13_cross_tree | PASS |
| L14_illustrations | PASS |
| L15_data_ties | PASS |
| L16_author_claims | PASS |

Full certificate JSON: `ci/claim_certificate.json` (self-hash: `d85e8c7a1c1df094...`)
