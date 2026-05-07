# Audit Observer ↔ Zeroth-Order Screening Invariant Map

**Companion doc to:** `PRE_REGISTRATION_AUDIT_OBSERVER_v4.md`
**Status:** orientation artifact; not part of the locked pre-registration. Maps the audit observer's design onto a formal substrate from prior symbolic-substrate work.

---

## Why this doc exists

The audit observer (`ci/audit/audit_observer.py`) is not an ad-hoc construction. It is a direct instance of a formal pattern: when a latent object is not directly observable, do not promote an interface statistic into ontology; first establish the zero-order floor, then admit only screening statistics that preserve the relevant regime structure at that interface.

The audit observer's RelationClass enum, predicate logic, and `INSUFFICIENT_OBSERVABILITY` escape-class all derive from this pattern. This doc records the mapping so reviewers can audit the design choice, not just the implementation.

---

## The pattern (one paragraph)

```
latent structure (smooth-vs-pivot regime) → inaccessible to bounded observer
interface data (ObservationPacket: chunk trace, verifier outcome, embedding sequence) → observable
screening statistic (max_chunk_displacement, max_drift_deg, verifier_pass) → admissible only if
                                                                              it preserves regime ordering
claim (RelationClass output) → bounded to the statistic's validated role
```

The audit observer never claims to *see* smoothness. It classifies the *relation* between two black-box observers' interface data using deterministic predicates whose admissible role has been registered in advance.

---

## Operator-phase placement: collapse-residue boundary

Under the broader operator cycle from prior symbolic-substrate work, the audit observer occupies a specific phase: the **collapse-residue handling boundary**. Two adjacent claims about it:

1. **Inputs are collapse residues, not primitives.** Each `ObservationPacket` is what *remains* after a black-box generation has run to completion: the verifier outcome (a residue from the irreversible verification act), the chunk trace (a residue from streaming generation), the embedding sequence (a residue from the encoder's frozen apparatus). The audit observer never touches a generation in flight; it only handles what collapse leaves behind.

2. **Outputs are typed bounded claims, not ontology.** The `RelationClass` enum is the typing of those residues: `AGREEMENT`, `LOCAL_CONTRACT_DIVERGENCE`, `CHART_TRANSITION`, etc. Each is a **bounded comparison contract** between two collapsed observations. None of them claim to *be* the latent regime; each only classifies what the post-collapse residues admit when compared.

The audit observer is therefore the deterministic apparatus that prevents the most common error in this phase: **promoting collapse residue to ontology**. The phase's discipline is exactly: residue → typed claim-fiber → bounded routing/comparison/falsification, never residue → final truth. The `INSUFFICIENT_OBSERVABILITY` class is the phase's escape hatch — when the residues do not admit a typed comparison within the registered observer frame, the audit refuses to type rather than fabricating a class.

This phase placement matters for one practical reason: it tells the reader why the audit observer has to be deterministic and LLM-free. A residue handler that itself runs an LLM is not handling residue — it is generating new residue. The bound only holds at this phase boundary if the apparatus that crosses it carries no generative capacity.

## Six invariant families → audit observer implementation

### State invariant
> The operator acts on interface data plus a declared statistic, not on the latent object itself.

**Audit observer:** `AuditObserver.classify_cell(packets_a, packets_b)` operates on `ObservationPacket` lists. It never touches model internals, gradients, or the latent regime. The only things it reads from packets are: `max_chunk_displacement`, `max_drift_deg`, `verifier_result`, `error`, and the four hash fields.

### Budget invariant
> The observer has no unbounded access to internals, gradients, global geometry, or ontology. All claims are constrained by the interface.

**Audit observer:** Cert layer L30 grep-tests `ci/audit/*.py` for forbidden imports (`anthropic`, `openai`, `openrouter`, `transformers`, `torch`). The audit observer's substrate is pure Python + numpy + scipy. No LLM call, no neural inference, no parameter access. The bound is literal at the `import` statement.

### Coherence invariant
> The statistic must preserve the task-relevant regime structure across allowed representation changes, apparatus changes, or encoder/operator variants.

**Audit observer:** Cross-encoder check (pre-reg §7) — re-embed final completions in `mpnet-base-v2`, compute Cohen's κ between MiniLM and mpnet smooth/pivot classifications. If κ ≤ 0.6, emit `CONSTRUCT_ENCODER_DEPENDENT` flag. Cross-observer check — `chart_agreement(A, B)` requires `L_hat(A)` and `L_hat(B)` within 50%. Both are explicit tests of representation-change preservation.

### Diagnostic invariant
> A judge-free report exists: rank, sign, threshold, regret, calibration error, operator dispersion, or falsification count.

**Audit observer:** Output is the typed `AuditResult` dataclass — `RelationClass` enum + structured `relation_evidence` dict containing the deterministic predicate values that drove the classification. No LLM judgment anywhere in the report. A reviewer can recompute the entire `AuditResult` from the packet streams + the audit observer's source code.

### Transition invariant
> If the statistic preserves regime structure, the next move is routing, integration, sampling, or refinement. If it fails, the channel is rejected or re-specified.

**Audit observer:** Pre-reg §9 decision tree maps each majority RelationClass to a paper action: `AGREEMENT` → keep + add measurement; `CHART_TRANSITION` → reframe per-family L_hat; `LOCAL_CONTRACT_DIVERGENCE` → scope claim; `INSUFFICIENT_OBSERVABILITY` → defer. The transition rule is deterministic and registered.

### Falsification invariant
> The statistic is invalid for its declared task if it fails to preserve the predeclared ordering, sign, threshold, or fail-safe condition under controls.

**Audit observer:** `TRUE_CERTIFICATE_REFUTATION` is the predeclared falsification predicate: any (smooth, high-tier, verifier_passed) trial corroborated across both observers triggers it. Pre-reg §9 binds this to a mandatory paper rewrite. The falsifier exists, is computable, and has consequences locked in writing.

---

## Forbidden lifts — what the audit observer never does

The pattern explicitly forbids:

```
screening statistic → direct ontology
```

The audit observer never claims:
- "this trial *is* smooth"
- "this model *has* a low Lipschitz constant"
- "the bound *is* operative"

The audit observer only claims:
- "under O_audit_v1, this packet's `max_chunk_displacement` is ≤ 2.5·L̂_calibration AND `max_drift_deg` ≤ 15° AND `verifier_result.pass_both` is True"
- "the relation between these two packet streams is classified as `<RelationClass>` per the predicate logic registered at pre_reg_hash `<sha256>`"

The first is an interface measurement. The second is a bounded comparison contract. Neither is an ontological claim.

---

## Allowed lifts — what the audit observer does

```
screening statistic → bounded routing / comparison / falsification contract
```

Concretely:
- **Routing:** the audit's per-cell `RelationClass` output drives the paper's decision tree (pre-reg §9). This is a routing decision: keep claim, reframe, scope, defer.
- **Comparison:** `chart_agreement`, `regime_agreement`, `contract_agreement` are explicitly comparison contracts between two observer streams, not absolute claims about either.
- **Falsification:** the `TRUE_CERTIFICATE_REFUTATION` predicate is a registered falsification trigger. It can fire. Its consequences are pre-registered. It is not retroactively definable.

---

## The one documented stub (per pre-reg §13)

> The audit observer cannot deterministically distinguish (α) genuine within-frame smooth-success exceptions from (β) frame-transition events without human review.

This is the explicit boundary, not a hidden gap. In invariant terms: the audit's diagnostic predicate cannot autonomously promote `SMOOTH_SUCCESS_EXCEPTION` to `TRUE_CERTIFICATE_REFUTATION` without cross-observer corroboration. When the corroboration is missing, the audit emits the offending packet's diagnostic data and explicitly defers. This is consistent with the falsification invariant: a screening statistic should not falsify alone; it should trigger a documented review.

---

## What goes in the paper (anonymized framing)

> Our deterministic audit observer is constructed as an instance of the zero-order screening pattern: we do not promote any interface statistic (chunk-displacement, drift angle, verifier outcome) into an ontological claim about smoothness or pivot. Instead, the audit observer is registered to a single bounded task — classifying the relation between two independent black-box observer streams into a pre-declared enum — and its substrate is mutation-tested under that bound. The classification is reviewable, reproducible from packet streams alone, and explicitly admits `INSUFFICIENT_OBSERVABILITY` when its predicate logic cannot decide within its registered observer frame. This separation between interface statistic and ontological claim is what permits the certificate to be a real check rather than a circular restatement.

---

## Cross-reference to the paper's own zeroth-order framing

The audit observer is not a methodological add-on; it is an extension of the same pattern the paper applies internally to ρ̂. The paper says (§3, §lipschitz_calibration): ρ̂ is a calibrated zero-order screening statistic for text-interface constraint regimes; its admissible meaning is the bounded routing task, not "true latent conflict." The audit observer applies the same pattern one level up: the smooth/pivot decomposition is itself a screening structure, not an ontology, and the audit observer is the bounded apparatus that registers, compares, and falsifies it.

This is the recursion the paper has always implied. We make it explicit.
