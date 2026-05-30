# Reviewer Quickstart

Paper: The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment
Preprint: arXiv (cs.AI), CC BY 4.0
Certificate status: PASS. 34 cert layers including L32 paper-surface impacting, L33 figure-caption grounding, L34 page-check. 325 of 325 data ties. 9 of 9 caption groundings.

All commands run from the repo root. No GPU required. No API keys required for steps 1-4.

---

## Commands

```
# 1. Verify the full claim certificate (~30 sec, stdlib + json only)
python ci/claim_certificate.py
```
Regenerates ci/claim_certificate.json and prints PASS/FAIL per layer.
Exits 0 on full PASS. Self-hashes the output JSON.

```
# 2. Verify a single headline claim: smooth-regime falsifiability
python ci/claim_data_ties_check.py 2>&1 | grep smooth_regime_total_4272
```
Reads rebuttal/figures/unconditional_pivot_results.json and checks that
d['full_paper_claim']['smooth_total'] == 4272. Expect: PASS computed=4272 expected=4272.

```
# 3. Spot-check the cross-model figure metadata (9 models, 6 providers, N=3120)
python ci/cross_model_metadata_check.py
```
Reads supplementary/experiments/code_constraint_results.json and
rebuttal/figures/cross_model_results.json, then confirms the paper window
(lines 392-543) contains matching phrases. Exits 0 on 3/3 checks passed.

```
# 4. Run the mutation acceptance suite (proves cert catches what it claims to)
python ci/tests/test_cert_mutations.py
```
Injects known-bad values into a scratch copy of the data and verifies the
cert returns FAIL for each mutation. Requires no API keys.

```
# 5. View the 2-page reviewer certificate summary
cat ci/claim_certificate_reviewer.md
```
Lists verdict, what is/is not proven, headline claims table, caveats,
and spot-check recipes.

---

## Light Dependency Path

Steps 1-5 above need only:
  - Python 3.x (stdlib: json, hashlib, pathlib, re, subprocess)
  - pdftotext (optional; used by L12 build equivalence in full mode only)

No torch, no sentence-transformers, no API keys needed to verify the certificate
or run the mutation tests.

Heavy dependencies (torch, sentence-transformers, openai) are needed only to
RE-RUN the original experiments:
  - supplementary/experiments/code_constraint_experiment.py    -- requires openai + local models
  - supplementary/experiments_rebuttal/cross_model/cross_model_harness.py  -- requires openai
  - supplementary/experiments/lipschitz_calibration.py        -- requires sentence-transformers

Stored result JSONs are committed to the repo. You do not need to re-run experiments
to verify that paper numbers match stored values; the cert (step 1) does that.

---

## More Detail

Full claim-to-artifact map:  supplementary/CLAIM_TO_ARTIFACT_MAP.md
Bundle contents by role:     supplementary/REVIEWER_INDEX.md
Full certificate JSON:       ci/claim_certificate.json
Caveat ledger:               ci/caveat_ledger.json
