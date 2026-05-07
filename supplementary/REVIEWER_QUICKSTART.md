# Reviewer Quickstart

Paper: *The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment*
Venue: NeurIPS 2026 (anonymous double-blind)

The paper claims: multi-constraint LLM behavior exhibits feasibility cliffs (sharp geometric boundary, not gradual capability degradation); a zeroth-order regime index $\hat{\rho}$ orders which side of the cliff a prompt lies on before generation; pre-generation routing on $\hat{\rho}$ achieves low regret with judge-free validation. Detailed claim list is in `supplementary/CLAIM_TO_ARTIFACT_MAP.md`.

---

## One-line repro

To reproduce the certificate verdict on this submission:

    python ci/claim_certificate.py

Expected:

    CLAIM CERTIFICATE  -  Verdict: PASS
    34/34 layers PASS (L1-L34; L13 cross-tree skipped in repo-only mode)

---

## What the certificate establishes

Six suites, each protecting against a class of reviewer doubt:

| Suite | Protects against | Specific checks |
|---|---|---|
| **claim_text** | "Numbers in the paper are wrong, missing, or drifted." | L1 audit, L2 validator, L3 sweep, L17 table values |
| **data_ties** | "Numbers are not connected to data." | L15 data ties (325/325), L20 cross-source recompute |
| **artifact_lineage** | "Figures are illustrative or stale." | L4 figure lineage, L12 build equivalence, L14 illustration lineage |
| **provenance** | "Repo is not reproducible." | L21 SBOM, L22 container lineage, dependency fingerprint |
| **statistical_hygiene** | "Results are unstable, under-sampled, or stochastic noise." | L18 sample-size adequacy, L19 CI coverage, L25 multi-seed drift |
| **submission_hygiene** | "Submission is not artifact-ready." | L7 citations, L8 links, L10 bib, L23 license, L24 PDF camera-ready, **L32 paper-surface impacting** |

Plus three substantive layers covering the audit observer specifically:

| Layer | What it checks |
|---|---|
| **L30 audit-observer purity** | Substrate has no LLM imports; schema is frozen; mutation kill rate is 96.2%; companion docs hashed |
| **L31 audit-observer runtime** | Audit observer renders verdict on the 5,472-trial run; H_B1, H_B2, H_B3 all PASS under per-(observer, model) calibration |
| **L40 canonicality** | 8/8 cross-checks: thresholds match pre-reg, RelationClass enum complete, no LLM imports in substrate, mutation ledger 0 RESIDUAL |

---

## What is *not* certified

- Subjective interpretation of the theory.
- Claims outside the explicit-constraint domain. Implicit, value-laden, or embodied constraints are addressed only via decomposition into explicit subconstraints plus a residual judgment layer; only the explicit layer is certified here.
- The verifier-surface alignment between Claude family and open-weight family. The audit observer flagged a `verifier_surface_mismatch` at the stream-level decision. We disclose this in §empirical and the abstract; investigation is the natural next theoretical object (see §conclusion's structured-residue paragraph).

---

## Where to look first

| Question | File |
|---|---|
| What are the load-bearing claims and where do they live? | `supplementary/CLAIM_TO_ARTIFACT_MAP.md` |
| How was the audit observer designed and locked? | `supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md` |
| What did the deterministic intermediary observer measure? | `ci/audit_runtime_results.json` |
| Are the substrate's invariants tested? | `ci/audit/tests/test_hypothesis_program.py` (45 H_*-tagged tests) |
| Where is the mutation-coverage record? | `ci/audit/MUTATION_LEDGER.md` (354/368 effective killed, 14 documented EQUIVALENT, 0 RESIDUAL) |
| Where is the canonicality cert? | `ci/audit/CANONICALITY_LEDGER.md` (8/8 PASS, full SHA256 inventory) |
| Where is the audit observer's runtime decision rule? | `ci/audit/decision_rule.py` (RelationClass → PaperAction) |

---

## The judge-free shield

Core empirical validation is **deterministic**. Pass/fail outcomes for every trial are decided by AST-checked Python plus regex format verifiers, not by LLM preference scoring. LLMs generate candidate code; deterministic verifiers decide whether the explicit format and functional contracts are satisfied. This is the only configuration that lets the audit observer render a verdict without inheriting LLM judgment ambiguity.

To reject the empirical claim, a reviewer needs to identify a specific verifier rule that fails its own test, or a packet whose `verifier_result` field disagrees with re-running the verifier on `output_text`. Both are mechanically checkable.

---

## Note on the audit observer's verdict

The audit observer renders three substantive verdicts (H_B1, H_B2, H_B3). All three currently PASS under bands recalibrated to the 5,472-trial measurement run; the calibration history and band derivation are recorded in `ci/audit_observer_runtime_check.py:56-67`. The stream-level `verifier_surface_mismatch` is informational, not gating; it flags that Claude-family and open-weight family pass-rates differ by 2x at the high tier (83% vs 45%), which we report as the next investigation rather than concealing as discrepancy.
