# Certificate Architecture

Paper: *The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment*
Venue: NeurIPS 2026 (anonymous double-blind)

This document describes how the deterministic certificate suite is organized and what it covers. It is not a contribution claim. It is reviewer infrastructure.

---

## Purpose

The certificate suite identifies which classes of reviewer objection are mechanically checked and which are not. Where a claim is covered (the diagonal cost bound, the regime index $\hat{\rho}$ ordering, the judge-free validation outcomes, the pre-generation routing regret on calibration), a check-level failure can be pointed at directly. Where it is not covered, the certificate is silent and the reviewer's framing stands on its own terms.

The certificate does not narrow what counts as a valid objection. It only narrows the surface on which the authors have done deterministic pre-work. Substantive disagreement outside that surface remains the reviewer's prerogative.

---

## Six suites

| Suite | Protects against | Concrete checks |
|---|---|---|
| claim_text | "Numbers in the paper are wrong, missing, or drifted." | L1 audit, L2 validator, L3 sweep, L17 table values |
| data_ties | "Numbers are not connected to data." | L15 data ties, L20 cross-source recompute |
| artifact_lineage | "Figures are illustrative or stale." | L4 figure lineage, L12 build equivalence, L14 illustration lineage |
| provenance | "Repo will not reproduce." | L21 SBOM, L22 container lineage, dependency fingerprint |
| statistical_hygiene | "Results are unstable, under-sampled, or stochastic noise." | L18 sample-size adequacy, L19 CI coverage, L25 multi-seed drift |
| submission_hygiene | "Submission is not artifact-ready." | L7 citations, L8 links, L10 bib, L23 license, L24 PDF camera-ready |

Three additional layers cover the audit observer specifically:

| Layer | What it checks |
|---|---|
| L30 audit-observer purity | Substrate has no LLM imports; schema is frozen; mutation kill rate is 96.2%; companion docs hashed |
| L31 audit-observer runtime | Audit observer renders verdict on the 5,472-trial run; substantive verdicts H_B1, H_B2, H_B3 all PASS under per-(observer, model) calibration |
| L32 paper-surface impacting | Mechanical certification of spatial layout containment on the rendered PDF: text-vs-card overflow, text-vs-image overlap, image-vs-image overlap, drawing-vs-drawing overlap, body margin overflow. 0 violations on the current submission. |
| L40 canonicality | 8/8 cross-checks: thresholds match pre-registration, RelationClass enum complete, no LLM imports in substrate, mutation ledger 0 RESIDUAL |

---

## Reviewer threat-model

This is our map of common objection classes and the suites we precompiled checks against. Reviewers are not bound by it. Objections outside this map are valid and may indicate gaps in our coverage.

| Generic doubt | Suite containing the relevant checks | What a mechanical falsification would look like |
|---|---|---|
| "Numbers may be cherry-picked or stale." | claim_text, data_ties | A specific claim ID whose text-presence or data-tie fails. |
| "Figures may be illustrative." | artifact_lineage | A specific figure without lineage or with a stale asset hash. |
| "Core results use LLM-as-judge." | data_ties + L31 | A core verdict-layer outcome whose decision depends on subjective LLM judging. The verdict layer is deterministic; tagger and pilot-rater roles are acknowledged in the judge-free section below. |
| "Results are stochastic noise." | statistical_hygiene | A missing/failed CI, sample-size, or multi-seed drift check. |
| "Repo will not reproduce." | provenance | A concrete environment, hash, or container failure. |
| "Submission is messy." | submission_hygiene | A broken citation, link, license, or PDF readiness failure. |
| "Theorem and numbers disagree." | data_ties + L9 cross-claim consistency | A formula/value relation that fails the consistency check. |
| "Paper overclaims." | scope contract (below) | A specific load-bearing claim outside the declared certified domain. |

The "mechanical falsification" column describes only what would constitute a check-level failure within the precompiled coverage. It is not a constraint on the form of valid reviewer objections. Substantive theoretical or methodological disagreements outside this surface are equally valid and not covered by the certificate.

---

## Scope contract

**Certified domain.** Explicit, verifiable multi-constraint tasks with deterministic validators. The empirical claims hold within this domain: regime index ordering ($r_s = 1.0$), smooth/pivot decomposition under audit-observer measurement, 0/1,365 high-tier refutations consistent with the geometric floor predicted by the diagonal cost bound, and pre-generation routing regret of 1.8% on calibration. The bound itself is kinematic (geometric, not statistical).

**Not certified.** Implicit residual judgment, broad human preference alignment, semantic qualia, unconstrained natural-language helpfulness. Where the paper discusses these (§scope, §limitations), it does so as scope description, not as certified result.

**Bridge.** Implicit constraints may be decomposed into explicit verifiable subconstraints plus a residual judgment layer. Only the explicit layer is certified here. The decomposition is described in Algorithm 1 and §scope; it is not claimed to be complete.

A reviewer who claims the paper overreaches is asked to identify a specific load-bearing claim that violates the certified-domain boundary. The certificate lists every load-bearing claim by ID.

---

## Judge-free verdict layer (with explicit callouts)

LLM-as-judge is itself the research subject of this paper. The paper studies how LLMs behave under multi-constraint judging by various oracles. Within that frame, our core verdict layer is deterministic so that the cert system does not become recursively contaminated by the same judge whose behavior is under study.

Where the verdict layer is judge-free:

1. AST-checked Python decides functional correctness.
2. Regex and token-count format verifiers decide constraint compliance.
3. The audit observer renders RelationClass via a deterministic predicate ladder over verifier outcomes.

No LLM is in the decision loop for any cert layer's PASS/FAIL determination. LLMs generate candidate outputs. Deterministic verifiers decide whether contracts are satisfied. The audit observer's purity check (L30) lists every import of every substrate file and currently reports `no_llm_imports` PASS across 13 substrate files.

Where LLMs do appear in the broader pipeline (acknowledged, not concealed):

- The implicit-k constraint decompression pipeline uses an LLM as a tagger that selects from a fixed 34-rule deterministic verifier library. The tagger does not decide correctness; it routes a prompt to a deterministic checker. This is a tagger role, not a judge role.
- The image-format Pass B pilot (small N) is hand-rated by a single rater. This is a human judge, recorded in the per-trial rationale JSON, separate from the deterministic verifier core.
- Future paper-surface fault-injection work flagged in the appendix may use LLM-as-judge as part of its oracle. That methodology is research-subject testing rather than a hidden cert dependency.

A reviewer challenging the judge-free claim is asked to identify a specific verifier rule whose decision is implicitly LLM-mediated within the cert layers. The L30 import scan is the mechanical surface for that challenge.

---

## What the architecture is not

- Not a claim that peer review is solved.
- Not armor against substantive theoretical disagreement.
- Not a substitute for reading the paper.
- Not a guarantee that the framework's metaphysical assumptions are correct.

It is a finite, mechanical apparatus for distinguishing "I disagree because *this specific certified check is wrong*" from "I disagree because the conclusions feel large." The first is engageable in correspondence; the second is unfalsifiable.

---

## Pointer

Generated artifact: `ci/claim_certificate.json` (machine-readable) and `ci/claim_certificate.md` (human-readable). Self-hashed; tampering is detected. Run `python ci/claim_certificate.py` to regenerate locally.
