# Certificate Changelog

## How to read this changelog

Schema versions track breaking changes to `claim_certificate.json` structure.
Layer additions and sub-check additions that do not change the top-level cert
schema are recorded as patch-level entries under the current schema version.

**Current schema version: 3**

Schema version | Key additions
---            | ---
1              | Initial 13-layer cert with L1-L13 blocking layers
2              | `artifact_hashes` block; L14-L15 (anchor-binding, data-ties)
3 (current)    | `git`, `profile`, `mode`, `mode_note` fields; L16-L21 sub-check wrappers via L9

Blocking vs advisory policy:
- A **blocking** layer causes `claim_certificate.py` exit code 1 if it fails.
- An **advisory** layer is recorded in the cert but does not block the exit code.
  Advisory layers are promoted to blocking once coverage reaches 100%.
- To extend with a new check: add a script under `ci/`, invoke it from
  `_populate_subprocess_relations()` in `cross_claim_consistency_check.py`,
  and record the promotion date here when it moves to blocking.

---

Entries are in reverse-chronological order (newest first).

---

## 2026-05-04 (fifth pass) -- Tractable cost reconstruction

### What changed

- `ci/model_pricing.json`: single source of truth for per-million-token
  prices. Anthropic-direct section is hand-edited and dated; OpenRouter
  section is auto-refreshable.
- `ci/refresh_pricing.py`: pulls the OpenRouter `/api/v1/models` endpoint
  and updates the OpenRouter section of the pricing JSON in place. Has
  `--dry-run` and `--keep-only-known` flags. Anthropic section is never
  touched.
- `ci/cost_report.py`: walks every result JSON under
  `supplementary/experiments/outputs/` plus the top-level
  `fixed_point_*_addition.json` siblings, sums tokens by `model_id`, and
  multiplies by current prices. Skips merged / aggregate files
  (`_full10`, `_with_*`) to avoid double-counting. Where token usage is
  recorded, it is used directly; where only response text is available,
  tokens are estimated at ~4 chars per token and flagged. Writes
  `ci/cost_report.json` plus a stdout summary.
- NeurIPS checklist (\texttt{paper/checklist.tex}, "Experiments compute
  resources" item): replaced the hand-waved "$15--25" with a pointer to
  `ci/cost_report.json` and a dated total ("$26.89 at prices verified
  2026-05-04").

### Why not a cert layer

Cost reconstruction is a budgeting and reproducibility surface, not a
verification one. The cert verifies internal consistency of claims; cost
data is informational. The cost report is regenerable on demand
(`python ci/cost_report.py`), runs without API access, and lives next to
the cert outputs.

### Snapshot at first generation

- 14 of 36 result JSONs have measured token data
- Grand total: $26.89 (502K input + 504K output tokens)
- Top experiments by cost: policy_density v3 ($15.97), implicit_k v3
  ($4.78), fixed_point_model_family canonical ($1.50)
- Top model by cost: claude-opus-4-7 ($6.05 across 180 calls)

---

## 2026-05-04 (fourth pass) -- L25 multi_seed_drift; closes the API-side BIS gap

### What changed

- `ci/multi_seed_drift_runner.py` (new, API-spending; user-invoked, NOT
  run by the cert): reruns one task on three models five times each at
  temperature 0.7, writes the cache to `ci/multi_seed_drift_data.json`.
- `ci/multi_seed_drift_check.py` (new, API-free; L25 in the cert):
  reads the cache, computes per-cell variance, advisory-flags any cell
  whose response-length stdev exceeds the threshold (400 chars).
- Suite registry expanded: `statistical_hygiene` now has 4 members
  (caveat_ledger, sample_size_adequacy, ci_coverage, multi_seed_drift).
- Paper `app:claude_genome` gains a "Multi-seed drift baseline" paragraph
  reporting the finding (opus-4.6 returns identical output across 5
  reruns at T=0.7; sonnet-4.5 stdev ~261 chars, sonnet-4.6 stdev ~330
  chars).

### Why the runner-vs-check split

API spend should never run as part of `python ci/claim_certificate.py`.
The runner is the only API surface and is invoked by hand when refreshing
the cache. The check is read-only on the cache. If the cache is missing,
the check returns 0 advisory ("no cache present") so a fresh clone of
the repo passes the cert without needing API keys.

### Verdict

- Cert: PASS, 24 of 24 layers, 6 of 6 suites.
- L25 cache: 3 models, 15 calls, no cells over the variance threshold.
- API spend for the drift run: ~$0.75, wall ~30 s.

---

## 2026-05-04 (third pass) -- BIS gap-fill: 8 new checks across all suites

### What changed

The suite reorganization (entry below) made the asymmetry visible:
`statistical_hygiene` had 1 member, every other suite had 4. This pass
adds 8 new checks to close the gap, divided across 4 parallel agents
that produced their checks independently.

New layers (advisory unless noted):

| Layer | Script | Suite | Severity |
|---|---|---|---|
| L17_table_values | `table_value_check.py` (existing, promoted) | claim_text | blocker |
| L18_sample_size_adequacy | `sample_size_adequacy_check.py` | statistical_hygiene | advisory |
| L19_ci_coverage | `confidence_interval_coverage_check.py` | statistical_hygiene | advisory |
| L20_cross_source_recompute | `cross_source_recomputation_check.py` | data_ties | blocker |
| L21_sbom | `sbom_check.py` | provenance | blocker |
| L22_container_lineage | `container_lineage_check.py` | artifact_lineage | advisory |
| L23_license_clearance | `license_clearance_check.py` | submission_hygiene | blocker |
| L24_pdf_camera_ready | `pdf_camera_ready_check.py` | submission_hygiene | advisory |

Supporting artifacts also added: `requirements.lock.txt` (8 packages
pinned), `ci/sbom_manifest.json` (8 entries with licenses),
`Dockerfile`, `CODE_OF_CONDUCT_ATTESTATION.md`.

The `paper_says_N_layer_certificate` relation in L9 was retired in favor
of `paper_says_N_suite_certificate` (suites are stable across layer
additions; layer count is not).

### Suite balance after expansion

| Suite | Members |
|---|---|
| claim_text | 6 |
| data_ties | 5 |
| artifact_lineage | 5 |
| provenance | 5 |
| statistical_hygiene | 3 |
| submission_hygiene | 6 |

### Verdict

- Cert: PASS, 23 of 23 layers, 6 of 6 suites.
- L15 data ties: still 325 of 325.
- API-spending checks (multi-seed drift) deferred for separate scoping.

---

## 2026-05-04 (later) -- Suite reorganization (additive; schema v3 patch)

### What changed

- Added `ci/suite_registry.json`: maps each blocking layer and L9 sub-check
  into one of six named suites (`claim_text`, `data_ties`, `artifact_lineage`,
  `provenance`, `statistical_hygiene`, `submission_hygiene`). Source: Gemini
  collaborator note (notetoclaude2.json) recommending a standard test
  taxonomy.
- `claim_certificate.py` now emits `suites[]` alongside the existing `layers[]`
  array. The suite roll-up reports per-suite passed/failed/skipped counts and
  a derived suite status. Layer outcomes not yet mapped land in an `unmapped`
  bucket.
- Paper Table 5 (`app:cert_scope`) reorganized to lead with the six suites;
  legacy L-numbers preserved as a fourth column for downstream tooling.
- `README.md` updated to mention the suite organization alongside the layer
  ladder.

### Why additive

- `layers[]` stays in the cert payload byte-for-byte; downstream consumers
  (cert markdown renderer, pre-commit hook, mutation harness, claim_data_ties)
  see no schema break.
- The self-hash (`certificate_self_hash`) now covers both arrays; tampering
  with either is detected.
- Migration steps from notetoclaude2.json followed: freeze L1-L16 PASS as
  baseline (done earlier 2026-05-04), add suite registry, update orchestrator
  to emit both, update reviewer prose, defer renaming until one stable pass.

### Verdict after migration

- Cert: PASS, 15/15 layers, 6/6 suites.
- L15 data ties: 325 of 325.
- No prior consumer of `claim_certificate.json` required modification.

---

## 2026-05-04 -- Schema v3; acceptance test suite; 100% provenance; L20 blocking

### Schema v3 fields added to claim_certificate.json

- `git`: dict with `commit`, `branch`, `dirty` (bool), `describe` fields.
  Captured at cert-generation time. `--release` flag rejects dirty git trees;
  override with `--allow-dirty` for CI draft runs.
- `profile`: hardware/Python version snapshot (platform, Python, GPU if present).
- `mode`: one of `"draft"`, `"release"`, `"ci"`. Defaults to `"draft"`.
- `mode_note`: free-text string set automatically by `--release` or `--venue`.
- `artifact_hashes`: sha256 hashes of every result JSON referenced by L15
  entries. Catches silent result-file mutation between runs.
- `--venue` flag defaults to `neurips` for double-blind safety; reviewer-mode
  markdown (`ci/claim_certificate_reviewer.md`) strips author-identifying
  content before rendering.

### L17 decomposition consistency folded into L9

L17 (`decomposition_consistency_check.py`) is no longer a standalone named
layer. Its 7 relations are now invoked by L9 via `_populate_decomposition_relations()`.
This preserves the 16-layer nominal count while adding the full decomposition
surface to the L9 structural pass. The layer count in the paper appendix (16)
remains correct.

### L18-L21 sub-checks added via L9 subprocess wrapper

All launched as L9 sub-processes via `_populate_subprocess_relations()`.
Relation names in cross_claim_consistency_results.json: `subproc__<name>`.

- `subproc__cross_model_metadata` (L19) -- blocking. Verifies n_models,
  n_providers, n_trials computed from source JSONs appear in the
  fig:cross_model paper window. 9 models, 6 providers, N=3,120.
- `subproc__result_provenance` (L20) -- promoted advisory to blocking
  (see section below). Verifies every tracked result JSON has a recorded
  provenance chain (script + inputs + run date).
- `subproc__manifest_schema` -- blocking. Verifies claim_data_ties.json
  schema integrity (required fields, value_expr safety, tolerance contract).
- `subproc__monotonic_cliff` -- blocking. Verifies calibration pass-rates are
  non-increasing and failure-rates non-decreasing across tiers in four
  source JSONs.
- `subproc__pdf_source_equivalence` -- blocking. Verifies that
  `paper/main.pdf` is byte-for-byte equivalent to the last committed PDF
  (catches uncommitted build drift).
- `subproc__cert_anonymity` -- blocking. Verifies cert artifacts (claim_certificate.md,
  claim_certificate_reviewer.md) contain no author-identifying strings
  (ICML double-blind compliance).
- `subproc__caveat_ledger` -- blocking. Verifies every caveat in
  `ci/caveat_ledger.json` has status `"open"` or `"resolved"` and that
  no resolved caveat has regressed.

### 100% provenance coverage achieved (26/26 sources)

All 26 source result files now have full `_meta` provenance blocks (script,
inputs, run date, random seed where applicable). The 11 pre-existing files
that predated the L20 layer were backfilled.

### L20 promoted advisory to blocking

`result_provenance_check.py` launched as advisory. Promoted to blocking after
100% coverage was confirmed across all tracked experiment result files.
Any new result file added to the repo without a provenance record now fails
the cert at exit code 1.

### text_normalization helper added

`ci/text_normalization.py`: shared macro-alias regex table used by L1
(`claim_audit.py`) and L15 (`claim_data_ties_check.py`). Handles:
- `\rhohat` -> `\hat{\rho}` alias expansion (T1 rhohat fix that was causing
  false negatives on frontier-ratio claims)
- LaTeX thousand-separator normalization (`4{,}272`, `4\,272`, `4,272` -> same)
- Command normalization for regex matching across different TeX encodings

### L15 paper-locality fields

`claim_data_ties.json` extended with optional per-entry fields:
- `paper_render_pattern`: regex that MUST match somewhere in `paper/main.tex`.
  Present on ~15 flagship claims including `smooth_regime_total_4272`,
  `smooth_regime_successes_zero`, `pivot_regime_total_528`, `pivot_successes_42`.
- `paper_render_negate`: regex that MUST NOT match (catches "wrong denominator
  in right window" drift, e.g. "4,800 smooth-regime" when smooth_total=4,272).

All 15 new patterns verified against current `paper/main.tex` before commit.
L15 coverage: 304/304 pass including paper-locality checks.

### L14 anchor-based source binding

Load-bearing data blocks in `paper/main.tex` now enclosed with:
```
% cert:block:start <block_id>
...
% cert:block:end <block_id>
```
L14 verifies the block markers are present and that claim_data_ties entries
referencing those blocks still resolve. Prevents silent anchor drift.

### --release flag and --venue default

- `claim_certificate.py --release`: exits 2 if git tree is dirty (un-committed
  changes). Override with `--allow-dirty` for in-progress CI runs.
- `--venue neurips` (default): activates double-blind safety checks in
  `cert_anonymity_check.py`. Use `--venue icml` for the ICML submission branch.

### Reviewer-only markdown renderer

`ci/claim_certificate_reviewer.py`: renders `ci/claim_certificate_reviewer.md`
from the cert JSON with author-identifying content stripped. Safe to share
with reviewers or attach to supplementary materials.

### Caveat ledger initialized

`ci/caveat_ledger.json` created with 6 starter caveats:
1. `single_rater_image_scoring` -- image_transfer_runD_passB.json scored by one rater only.
2. `openrouter_api_pricing_drift` -- OpenRouter pricing used in cost estimates may change.
3. `frontier_api_nondeterminism` -- Claude family API results not reproducible to exact values.
4. `lipschitz_sample_size` -- L_hat calibration uses 900 completions; larger N could narrow CI.
5. `constitution_version` -- Claude Constitution v1.1 used; later versions may change rho estimates.
6. `cross_model_selection_bias` -- 9 models selected for coverage; not a random sample of all LLMs.

### Acceptance test suite (9 mutation tests)

`ci/tests/test_cert_mutations.py` extended to 9 tests. New tests (7-9) probe
semantic-swap mutations:
- **Test 7** `test_table_row_swap_fails`: swaps Low and Moderate rows in
  tab:calibration; paper_render_pattern with tier-label+rho binding must catch it.
- **Test 8** `test_denominator_noun_swap_fails`: replaces "smooth-regime" with
  "pivot-regime" at the 4,272 site; L15 paper_render_pattern for
  `smooth_regime_total_4272` must fail.
- **Test 9** `test_provider_count_drift_fails`: changes "6 providers" to
  "5 providers" in the fig:cross_model caption; cross_model_metadata_check
  n_providers_in_paper_window must fail.

---

## 2026-05-03 -- 4,272 reconciliation; cross-model; bib restores; Lipschitz

### 4,800 to 4,272 smooth-regime denominator reconciliation

The smooth-regime denominator was corrected from 4,800 to 4,272 across the
paper and all registry entries. 4,800 is total_infeasible (smooth+pivot combined);
4,272 is smooth_total only.

- `paper/main.tex`: "0/4,800" references updated to "0/4,272" at lines 73, 80,
  123, 259, 509 where they referred to the smooth-regime count. "4,800" retained
  where it refers to total_infeasible (smooth+pivot combined).
- `ci/claim_data_ties.json`: `smooth_regime_total_4272` entry updated from
  expected=4800 to expected=4272; `paper_render_pattern` and `paper_render_negate`
  fields added.
- `ci/decomposition_consistency_check.py`: `_no_4800_smooth_regime_in_paper`
  relation added to catch any future regression where "4,800" is placed adjacent
  to "smooth-regime" in the paper text.
- `rebuttal/figures/unconditional_pivot_results.json`: `full_paper_claim` block
  confirmed with `smooth_total=4272`, `pivot_total=528`, `total_infeasible=4800`.
- `ci/cross_claim_consistency_check.py`: `smooth_plus_pivot_eq_total` relation
  added (4272+528==4800); per-domain decomposition checks added. Total L9
  relations: 42/42 pass.

### Cross-model count reconciliation (9 models, 6 providers, N=3,120)

Corrected from an earlier draft that cited "5 models, N=1,200".

- `paper/main.tex` fig:cross_model caption updated to "9 models, 6 providers,
  N=3,120".
- T45 in `ci/claim_audit.py` updated to pattern `6\s+providers`.
- `ci/cross_model_metadata_check.py` created (new script) to verify computed
  counts from source JSONs appear in the paper window.

### 7 RESTORE bib citations

Seven citations that had been commented out or incorrectly listed were restored
to `references.bib` and their `\cite{}` commands verified in `paper/main.tex`:
- `wei2023jailbroken` (jailbreaking LLMs)
- `carlini2021extracting` (extracting training data)
- `bai2022training` (RLHF / Constitutional AI)
- `turner2023activation` (activation additions)
- `zou2023universal` (universal adversarial triggers)
- `dhuliawala2023chain` (chain-of-verification)
- `cosmos2024` (cosmos token constraints)

### Piaget restoration

`piaget1970structuralism` restored to `references.bib`; `\cite{}` reinstated
in footnote 3 of Section 1 (constructive cognition framing). This citation is
load-bearing context for the Hypothesis Surface framing.

### OpenRouter regression experiment

New experiment: `supplementary/experiments/openrouter_regression_results.json`.
- 8 models, 960 trials, ~$5 USD in API costs.
- Verifies monotone cliff pattern replicates on OpenRouter-hosted models.
- Results: 239/56/23/0 pass-B counts across control/low/moderate/high tiers.
- Added to `monotonic_cliff_check.py` as `openrouter_regression_stage1_pass_count`.

### Lipschitz calibration measured for first time

`supplementary/experiments/lipschitz_calibration_results.json` created with
empirically measured values replacing the placeholder table:
- Qwen-2.5-Coder-1.5B: L_hat=0.048 ± 0.008
- DeepSeek-Coder-1.3B: L_hat=0.049 ± 0.007
- TinyLlama-1.1B: L_hat=0.045 ± 0.008
- Overall range: [0.045, 0.049] (paper claim C8)

### Three drift fixes

- `binary_search` task description in paper: rephrased to remove ambiguous
  "iterative" phrasing that conflated one-shot and staged protocols.
- Encoder in cross_model experiment: E5-small updated to multilingual-MiniLM-L6
  to match the encoder actually used in the experiment scripts.
- `paper/main.tex` fig:cross_model caption corrected: "9 models, 6 providers"
  (was "9 models, 5 providers" in a draft version).

---

## Status policy summary (as of 2026-05-04)

Layer | Script | Status | Notes
----- | ------ | ------ | -----
L1    | claim_audit.py | blocking | 304 claims, T1-T56
L2    | citation_integrity_check.py | blocking |
L3    | bib_entry_check.py | blocking |
L4    | page_check.py | blocking |
L5    | anonymity_check.py | blocking |
L6    | claim_certificate.py | aggregator | not self-invoked
L7    | author_claims_check.py | blocking |
L8    | figure_value_check.py | blocking |
L9    | cross_claim_consistency_check.py | blocking | 42 relations + decomp + subproc wrappers
L10   | script_integrity_check.py | blocking |
L11   | link_integrity_check.py | blocking |
L12   | build_equivalence_check.py | blocking |
L13   | illustration_lineage_check.py | blocking |
L14   | claim_data_ties_check.py (anchor) | blocking |
L15   | claim_data_ties_check.py (data+locality) | blocking |
L16   | cross_tree_consistency_check.py | blocking |
L17*  | decomposition_consistency_check.py | folded into L9 | relations appended to L9 at import time
L18   | figure_lineage_check.py | blocking |
L19   | cross_model_metadata_check.py | blocking | via L9 subproc
L20   | result_provenance_check.py | blocking | promoted 2026-05-04 after 100% coverage
L21   | manifest_schema_check.py | blocking | via L9 subproc

*L17 folded into L9; the 16-layer count in the paper is preserved because L17
is no longer counted as a separate named layer.
