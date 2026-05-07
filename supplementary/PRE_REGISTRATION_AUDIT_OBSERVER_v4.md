# Pre-Registration: Deterministic Audit Observer for Smooth/Pivot Decomposition (v4)

**Status:** LOCKED. Hash-pinned. No edits after sha256 is committed to git AND the first ObservationPacket is emitted to the output JSONL. (Updates permitted before first packet emission, since no data has been collected.)
**Locked-at:** 2026-05-04 (UTC; exact timestamp recorded in `_meta.locked_at` of all output JSONs)
**Companion docs (orientation; not part of the locked predicate logic):**
- `supplementary/AUDIT_OBSERVER_INVARIANT_MAP.md` — maps the audit observer onto the six invariant families (state / budget / coherence / diagnostic / transition / falsification) of prior symbolic-substrate work. Provides the formal grounding for the design choices below.
- `ci/audit/README.md` — substrate-level documentation, public interface, determinism guarantees, the documented stub.
**Pre-registered against:** "The Cost of Cacophony" headline claims
- "0/4,272 smooth-regime refutations" (abstract, §empirical)
- "11% pivot regime admits 42/528 successes" (abstract)
- "1.7% high-tier unconditional pass rate" (abstract; verified directly)
- "89% of generation trajectories obey per-token Lipschitz bounds" (abstract)

**Why this exists:** L30 (per-trajectory pivot check) showed the original 89%/11%/42/528 numbers
are arithmetic outputs of hard-coded constants in
`supplementary/experiments_rebuttal/unconditional_pivot_analysis.py`, not measurements.
This experiment generates real per-trial trajectory + verifier-outcome data and validates
the headlines through a deterministic audit observer that ingests typed ObservationPackets
from two independent black-box generative observers.

---

## §1. Observer Definition (instantiated before any data is collected)

```
O_audit_v1 := (
  chunker         = Anthropic streaming-chunk boundary OR OpenRouter streaming-chunk
                    boundary; documented per packet via `chunker_id` field
  encoder         = sentence-transformers/all-MiniLM-L6-v2
                    (encoder package version recorded per packet)
  verifier        = supplementary/experiments/code_constraint_verifier.verify_both
                    (sha256 hash recorded per packet via `verifier_hash`)
  threshold_rule  = (max_chunk_displacement <= 2.5 * L_hat_calibration) AND
                    (max_drift_deg <= 15.0)
  task_family     = {factorial, fibonacci, is_palindrome, gcd, reverse_words, fizzbuzz}
                    (subset of code_constraint_tasks.py; selected for solution-length variety
                     and stable verifier behavior, NOT for cliff exhibition)
  model_interfaces = {
    "claude_family": Anthropic Messages API streaming, temp=1.0
                     except claude-opus-4-7 which uses default sampling
                     (4-7 rejects the temperature parameter per project memory),
    "open_weight":   OpenRouter chat completions API streaming, temp=1.0
  }
  L_hat_calibration_set = control-tier trials per (model, task), per black-box observer
  validation_set        = low + moderate + high tier trials, headline analysis on high-tier
  primary_thresholds    = (2.5 * L_hat, 15 deg)
  sensitivity_grid      = {2.0, 2.5, 3.0} * L_hat × {10, 15, 20} deg
)
```

Everything below is a claim **under O_audit_v1**, not a claim about smoothness simpliciter.
Outside O_audit_v1 (different chunker, different encoder, different verifier), no claim.

---

## §2. Black-Box Observers

| Observer ID | Implementation | Purpose |
|---|---|---|
| `B_claude` | Claude haiku-4-5 + sonnet-4-6 + opus-4-7 via Anthropic Messages API | Primary measurement on frontier family |
| `B_openweight` | qwen-2.5-coder-7b-instruct via OpenRouter | Positive control: same pipeline on the model class the original paper measured |

Each black-box observer emits one `ObservationPacket` per (model, task, tier, trial) cell.

---

## §3. ObservationPacket Schema (frozen)

```python
@dataclass(frozen=True)
class ObservationPacket:
    packet_schema_version: str          # "v4.0" or "v4.1"
    observer_id: str                    # "B_claude" or "B_openweight"
    model_id: str                       # API model name
    api_model_snapshot: str             # exact snapshot returned by API at call time
    task_id: str
    tier: str                           # "control" | "low" | "moderate" | "high"
    trial_idx: int
    prompt_text: str                    # exact prompt sent
    prompt_hash: str                    # sha256 of prompt
    output_text: str                    # complete completion (no truncation)
    output_hash: str                    # sha256 of output
    chunk_trace: list[dict]             # per-chunk: {chunk_idx, text_appended, displacement, drift_deg, cumulative_chars}
    n_chunks: int
    max_chunk_displacement: float
    mean_chunk_displacement: float
    max_drift_deg: float
    verifier_result: dict               # {pass_a, pass_b, pass_both, msg_a, msg_b}
    verifier_hash: str                  # sha256 of code_constraint_verifier.py at run time
    timestamp_utc: str                  # iso8601
    encoder_id: str                     # "sentence-transformers/all-MiniLM-L6-v2"
    encoder_package_version: str        # output of sentence_transformers.__version__
    pre_reg_hash: str                   # sha256 of THIS document
    manifest_hash: str                  # sha256 of (pre_reg + audit substrate + experiment script)
    error: str | None                   # None on success; message on API/verifier failure
    # --- v4.1 additive provenance fields (Optional; default None) ---
    api_request_id: str | None          # Anthropic message.id / OpenRouter top-level id
    actual_token_usage: dict | None     # provider-verbatim usage dict (no normalization)
    stop_reason: str | None             # Anthropic stop_reason / OpenRouter finish_reason
    openrouter_provider: str | None     # underlying host (Together / Fireworks / DeepInfra / ...);
                                        #   always None on B_claude packets
    wall_time_ms: float | None          # whole-trial latency captured via time.monotonic()
```

**Schema v4.1 (additive expansion of v4.0):** five new optional fields for
provenance — `api_request_id`, `actual_token_usage`, `stop_reason`,
`openrouter_provider`, `wall_time_ms`. Default `None` for backward
compatibility. v4.0 packets remain valid: any JSON dict in the v4.0 shape
(no v4.1 keys) constructs a clean `ObservationPacket` whose v4.1 fields
are all `None`. Pre-registration version remains v4 (additive expansion,
no predicate logic change; v4.1 is a schema-shape revision only).

---

## §3c. Hypothesis Program (locked, anchored to invariants)

**Editability note.** This section was expanded after initial v4 lock to mechanically cover all invariants from the external invariant spec (`AUDIT_OBSERVER_INVARIANT_MAP.md`). The expansion is permitted under §1 because no production `ObservationPacket` has been emitted yet. No predicate from the original H_A/H_B program is altered; new parts are additive. Document version remains v4.

The expanded program tests **eight kinds of hypotheses**, kept structurally separate:

- **Part A — Apparatus soundness** (H_A1..H_A6): each hypothesis tests one invariant family the audit observer claims to satisfy. Refutation here means the audit itself is methodologically broken; no substantive conclusion can be drawn until it's fixed.
- **Part B — Substantive paper claims** (H_B1..H_B8): each hypothesis tests one of the paper's headline numbers (or a substantive expansion thereof) under O_audit_v1. Refutation here means the paper claim does not survive direct measurement; mandatory reframe.
- **Part C — Operator-card core invariants** (H_C1..H_C5): each hypothesis tests one core invariant of the collapse-phase operator card from the external invariant spec, applied to the audit observer's commit / trace / lock / route / residue surface.
- **Part F — Lift discipline** (H_F1..H_F2): tests that audit output never lifts the screening statistic into a free-form ontological proposition (forbidden) and that every typed output maps to a bounded routing / comparison / falsification / deferral category (allowed).
- **Part X — Operator-card falsification criteria** (H_X1..H_X4): tests that the audit can OBSERVE the four pre-declared falsifiers of the collapse-phase operator card from the external invariant spec.
- **Part D — Decision rule** (H_D1): tests that the locked RelationClass → PaperAction mapping (codified in `ci/audit/decision_rule.py`) is total over the enum and deterministic on lookup.
- **Part H — Hash-chain integrity** (H_H1..H_H4): tests that every `ObservationPacket` carries the five required hashes and that the audit observer rejects streams whose `pre_reg_hash` or `encoder_id` drifts.
- **Part M — AAA gold-standard meta-tests** (M1..M10): NOT a substantive hypothesis. A separate quality-of-tests check at `ci/audit/tests/test_aaa_gold_standard.py` asserting the test corpus itself is Arrange-Act-Assert compliant, has no LLM/network/filesystem imports, has no skipped/xfail tests, and uses no class-level mutable state. Failure means the test program is structurally unreliable; substantive verdicts are held in abeyance until repaired. Listed here for completeness; not anchored to an invariant family.

A substantive hypothesis (Part B) can only be evaluated once **all six** apparatus hypotheses (Part A), all five operator-card invariant checks (Part C), both lift-discipline checks (Part F), all four falsification-observability checks (Part X), the decision-rule totality check (Part D), and all four hash-chain integrity checks (Part H) have been confirmed. If any check in A/C/F/X/D/H fails, Part B is held in abeyance.

**Test file location.** All hypotheses except Part M are implemented in `ci/audit/tests/test_hypothesis_program.py`, organized into pytest classes:

| Pytest class | Hypotheses |
|---|---|
| `TestApparatusHypotheses` | H_A1..H_A6 |
| `TestSubstantiveHypothesisLogic` | H_B1..H_B3 |
| `TestSubstantiveExpansions` | H_B4..H_B8 |
| `TestOperatorCardCoreInvariants` | H_C1..H_C5 |
| `TestLiftDiscipline` | H_F1..H_F2 |
| `TestOperatorCardFalsificationCriteria` | H_X1..H_X4 |
| `TestDecisionRule` | H_D1 |
| `TestHashChainIntegrity` | H_H1..H_H4 |
| `TestHypothesisProgramCompleteness` | meta-coverage assertion |

Part M is implemented in `ci/audit/tests/test_aaa_gold_standard.py`. A spec-correspondence bridge at `ci/audit/tests/test_invariant_spec_correspondence.py` asserts every invariant family in the external invariant map is covered by at least one Part A/C/F/X hypothesis.

### Part A — Apparatus soundness

Each apparatus hypothesis is anchored to one of the six invariant families. The invariant family describes what kind of bounded apparatus is allowed; the hypothesis is the testable claim that our audit observer is one such apparatus.

| ID | Invariant family | Hypothesis | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_A1** | *State* (operator acts on declared interface statistics, not latent object) | The audit observer's `RelationClass` output depends only on `(max_chunk_displacement, max_drift_deg, verifier_result.pass_both, error)` and the declared thresholds | Two invocations of `AuditObserver.classify_cell` on identical packet inputs yield byte-identical `AuditResult` | Any field-by-field difference between the two `AuditResult` outputs |
| **H_A2** | *Budget* (no unbounded access; bounded apparatus only) | The audit substrate `ci/audit/` contains no LLM-API import, no model-weight access, and no unrestricted I/O | AST scan of every `.py` file under `ci/audit/` finds no `import` of `{anthropic, openai, openrouter, transformers, torch}` (`tests/test_purity.py`) | Any forbidden import in any audit module |
| **H_A3** | *Coherence* (statistic preserves task-relevant regime structure across representation changes) | Smooth/pivot classifications under MiniLM-L6-v2 agree with re-embedding under mpnet-base-v2 at Cohen's κ > 0.6 | Re-embed final completions in mpnet-base-v2; recompute smooth/pivot with same threshold rule; compute κ vs MiniLM classifications | κ ≤ 0.6 → flag `CONSTRUCT_ENCODER_DEPENDENT`; the screening statistic does not preserve regime structure across encoder variants |
| **H_A4** | *Diagnostic* (judge-free report exists; rank/sign/threshold/falsification countable) | `AuditResult.relation_evidence` is fully recomputable from packet contents and the audit observer's source; no field requires interpretation | Run `AuditObserver.classify_cell` on the released packet streams from a clean checkout; `AuditResult` byte-matches the shipped one | Any divergence; any field that requires LLM judgment to populate |
| **H_A5** | *Transition* (success → routing/integration; failure → channel rejected or re-specified) | Per-cell `RelationClass` output drives exactly one paper action via the §9 decision tree, with `INSUFFICIENT_OBSERVABILITY` mapping to "defer" rather than to a substantive class | The decision tree §9 is total over the enum (every `RelationClass` value maps to a paper action); no class falls through | Any `RelationClass` value with no §9 mapping; any §9 branch that fabricates a class for `INSUFFICIENT_OBSERVABILITY` |
| **H_A6** | *Falsification* (predeclared falsification predicate exists and is computable) | `TRUE_CERTIFICATE_REFUTATION` predicate is emittable from the predicate logic when pre-registered conditions are met | Synthetic test case in `tests/test_audit_observer.py` constructs packets satisfying the predicate and asserts the class fires | The class never fires under any input; the predicate is uncomputable |

**Decision rule on apparatus failure:** if any of H_A1..H_A6 is refuted, the audit observer is methodologically broken. The substrate must be repaired and re-tested; **no substantive (Part B) inference is permitted until all apparatus hypotheses are confirmed.** Refutation of an apparatus hypothesis does NOT trigger any paper action under §9 — it triggers a substrate fix.

### Part B — Substantive paper claims

Each substantive hypothesis tests one of the paper's headline numbers under O_audit_v1. Refutation here triggers the §9 decision tree.

| ID | Paper headline | Hypothesis (under O_audit_v1) | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_B1** | "89% of trajectories obey per-token Lipschitz bounds" | Per-observer `smooth_fraction` is within ±5pp of 0.89 (i.e., in [0.84, 0.94]) for both B_claude and B_openweight | Compute `smooth_fraction` per observer from emitted packets using the §4.1 predicate with L̂_calibration from control-tier; check both fall in the band | Either observer's `smooth_fraction` outside [0.84, 0.94] |
| **H_B2** | "1.7% high-tier unconditional pass rate" | Per-observer `high_tier_pass_rate = passed_count_high / total_high` is within ±0.5pp of 0.017 (i.e., in [0.012, 0.022]) for both observers | Direct count: `sum(p.verifier_result["pass_both"] for p in high_tier_packets) / count(high_tier_packets)` per observer | Either observer's `high_tier_pass_rate` outside [0.012, 0.022] |
| **H_B3** | "0 smooth-regime refutations across N trials" | The count of high-tier packets satisfying `smooth(p) AND passed(p)` is 0, summed across both observer streams | `sum(1 for p in all_packets if p.tier == "high" and smooth(p, l_hat) and p.verifier_result["pass_both"])` | Count ≥ 1 (at least one (smooth, high-tier, passed) packet exists). The audit then classifies the offending packet(s) per §4.2 (SMOOTH_SUCCESS_EXCEPTION single-observer; TRUE_CERTIFICATE_REFUTATION cross-observer) |

**Decision rule on substantive failure:**
- **H_B1 fails** → reframe headline: replace "89%" with the measured per-observer Wilson 95% interval; report both fractions explicitly.
- **H_B2 fails** → reframe high-tier rate to per-observer measured value with Wilson CI.
- **H_B3 fails** → mandatory rewrite of the "0/N" framing per §9. The count-with-Wilson-upper-bound replaces the absolute claim. Per-observer breakdown shipped.

### Part B (continuation) — Substantive expansions

Each row tests a downstream substantive claim that can be read off the same packet streams without re-running the experiment. Refutation routes through §9 the same way as H_B1..H_B3.

| ID | Substantive claim | Hypothesis (under O_audit_v1) | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_B4** | Cross-generation chart stability (within one model family) | When two model generations within the same family carry L̂ values with relative gap ≤ 50%, the cross-pair classifies as `AGREEMENT` or `CHART_TRANSITION` (never `LOCAL_CONTRACT_DIVERGENCE`) | For each cross-generation pair, run `AuditObserver.classify_cell` over the per-generation control + low-tier packets; assert `relation_class ∈ {AGREEMENT, CHART_TRANSITION}` | Any cross-generation pair with L̂ relative gap ≤ 0.50 classified as `LOCAL_CONTRACT_DIVERGENCE` |
| **H_B5** | Cross-family chart stability is observable as `CHART_TRANSITION`, not `AGREEMENT` | When `B_claude` and `B_openweight` carry matched pass/smooth fractions but L̂ values whose relative gap exceeds 50%, the audit emits `CHART_TRANSITION` | Run `classify_cell` on a matched-pass / divergent-L̂ packet pair; assert `relation_class == CHART_TRANSITION` | `relation_class == AGREEMENT` despite L̂ relative gap > 0.50 |
| **H_B6** | Staging-benefit ratio is a measurable downstream quantity | The pivot-stream pass rate divided by the smooth-stream pass rate is recoverable from `AuditResult.pass_rate_a` and `pass_rate_b` to within 5% of the constructed ratio | Construct streams with known smooth/pivot pass-rate ratio; assert recovered `pass_rate_b / pass_rate_a` lies in [0.95, 1.05] × constructed ratio | Recovered ratio outside that band; or `pass_rate_a == 0` (denominator unrecoverable) |
| **H_B7** | Cliff form: per-observer pass rate is monotonically non-increasing across tiers (low → moderate → high) | For both observers, `pass_rate(tier)` strictly decreases as tier escalates given monotonically declining input | Call `classify_cell` per tier with monotonic pass counts; collect `pass_rate_a`, `pass_rate_b` per tier; assert strict monotonic decrease | Any non-monotonic step in either observer's per-tier pass-rate sequence |
| **H_B8** | Constitutive-loop pivot-fraction band: pivot fraction (1 − smooth_fraction) lies in [0.05, 0.20] under the predicted loop-coherence regime | Run `classify_cell` on packets shaped to the H_B1 regime; assert `1 − smooth_fraction_{a,b} ∈ [0.05, 0.20]` for both observers | Either observer's pivot fraction outside [0.05, 0.20] |

**Decision rule on Part-B-continuation failure:** same routing as H_B1..H_B3. The reframe is the per-observer measured value with Wilson 95% CI, plus an explicit footnote naming which sub-claim was refuted.

### Part C — Operator-card core invariants (collapse-phase operator card from external invariant spec)

Each row tests one core invariant of the collapse-phase operator card from the external invariant spec, applied to the audit observer surface. Refutation here means the audit observer is structurally non-conforming to the invariant spec and must be repaired before any Part B verdict is admissible. No paper-action lift fires from a Part C refutation; it triggers a substrate fix, identical to Part A.

| ID | Invariant | Hypothesis | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_C1** | *Commitment boundary* | The audit only classifies packets representing COMPLETED generation; an `error != None` packet must NOT yield a substantive (non-`INSUFFICIENT_OBSERVABILITY`) class | Inject an `error="incomplete generation"` packet into the stream; call `classify_cell`; assert `relation_class == INSUFFICIENT_OBSERVABILITY` | Any non-`INSUFFICIENT_OBSERVABILITY` class on a stream containing an incomplete-generation marker |
| **H_C2** | *Trace preservation* | When `SMOOTH_SUCCESS_EXCEPTION` or `TRUE_CERTIFICATE_REFUTATION` fires, `AuditResult.offending_packet_hashes` is non-empty | Trigger refutation; assert `len(result.offending_packet_hashes) > 0` | Refutation fires with empty `offending_packet_hashes` (trace lost on collapse) |
| **H_C3** | *Irreversibility* | `AuditResult` is a frozen dataclass; once classified, no field can be mutated | Attempt to assign to `result.relation_class` and `result.reason`; assert both raise | Either assignment succeeds |
| **H_C4** | *Routing discipline* | High-conflict streams (large displacement, large drift, divergent pass rates) MUST route to a recovery / disagreement / deferral class — never `AGREEMENT` | Construct a maximally divergent A/B pair; assert `relation_class != AGREEMENT` and `relation_class ∈ RelationClass \ {AGREEMENT}` | High-conflict stream classified as `AGREEMENT` |
| **H_C5** | *Residue re-expansion hook* | When refutation fires, both `offending_packet_hashes` and `reason` are populated, so a follow-up integration pass can be seeded | Trigger refutation; assert `len(offending_packet_hashes) > 0` AND `isinstance(reason, str) and len(reason) > 0` | Refutation fires with empty `reason` or empty `offending_packet_hashes` |

### Part F — Lift discipline (forbidden / allowed)

| ID | Discipline | Hypothesis | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_F1** | *Forbidden lift* | `AuditResult.relation_class` is always a typed `RelationClass` enum value, never a string or free-form proposition | Exercise every realistic classification path (`AGREEMENT`, `INSUFFICIENT_OBSERVABILITY`, `TRUE_CERTIFICATE_REFUTATION`); assert each output `isinstance(r.relation_class, RelationClass)` | Any output where `relation_class` is not a `RelationClass` enum value |
| **H_F2** | *Allowed lift* | Every `RelationClass` value maps to exactly one of four bounded categories: ROUTING, COMPARISON, FALSIFICATION, DEFERRAL | Maintain a locked categorization dict in the test; assert every enum value is keyed and every value is one of the four allowed categories; assert dict size == enum size == 8 | Any unmapped enum value, any value outside the four categories, or size mismatch |

### Part X — Operator-card falsification criteria

Each row asserts that the audit can OBSERVE the corresponding falsifier. Final field verdicts run at L31; this layer only verifies that the observability path exists.

| ID | Falsifier | Hypothesis | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_X1** | One-shot success in predicted-infeasible region without pivot signatures | A high-tier smooth (low displacement, low drift) verifier-pass packet must trigger `SMOOTH_SUCCESS_EXCEPTION` or `TRUE_CERTIFICATE_REFUTATION` | Construct a high-tier smooth+pass packet in observer A while observer B is pivot+failing; assert refutation class fires | Refutation class does not fire on a smooth + pass + high-tier input |
| **H_X2** | Router persistently misses observable high-conflict states | Repeated identical inputs yield byte-identical `relation_class` (no stochastic component that could mask persistent miss) | Call `classify_cell` 25 times on identical inputs; assert all returned `relation_class` values are identical | Any pair of identical-input invocations returns different `relation_class` values |
| **H_X3** | Refusal/staging fails to dominate one-shot under predicted cliff | Bimodal smooth+fail / pivot+pass streams produce `smooth_fraction_{a,b}` in [0.3, 0.7] (i.e., the bimodal structure is observable in `AuditResult` typed fields) | Construct 50/50 smooth+fail / pivot+pass packets; assert `0.3 ≤ smooth_fraction_a ≤ 0.7` and same for `_b` | Either smooth-fraction outside that band (signal lost in aggregation) |
| **H_X4** | Collapse residue cannot seed re-expansion | Every hash listed in `offending_packet_hashes` matches the `output_hash` of at least one input packet | Trigger refutation; for each `h` in `offending_packet_hashes` assert `h ∈ {p.output_hash for p in a ∪ b}` | Any hash in result not present in input packets' `output_hash` set |

### Part D — Decision rule (RelationClass → PaperAction)

| ID | Property | Hypothesis | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_D1** | Total + deterministic | `RELATION_TO_PAPER_ACTION` (in `ci/audit/decision_rule.py`) contains a key for every `RelationClass` enum value, every value is a `PaperAction` instance, and `apply_decision_rule(rc)` is referentially transparent | `set(RELATION_TO_PAPER_ACTION.keys()) ⊇ set(RelationClass)`; every value `isinstance(_, PaperAction)`; `apply_decision_rule(rc) == apply_decision_rule(rc)` for all `rc` | Any missing enum key, any non-`PaperAction` value, or any non-deterministic lookup |

### Part H — Hash-chain integrity

| ID | Hash anchor | Hypothesis | Operational predicate | Refutation predicate |
|---|---|---|---|---|
| **H_H1** | Required hashes present | Every `ObservationPacket` populates non-empty values of `pre_reg_hash`, `manifest_hash`, `verifier_hash`, `output_hash`, `prompt_hash`, each of length ≥ 16 | For each hash field, assert `isinstance(value, str)`, non-empty, `len(value) >= 16` | Any field missing, empty, non-string, or under length |
| **H_H2** | `pre_reg_hash` consistency | A stream containing two distinct `pre_reg_hash` values is classified as `INSUFFICIENT_OBSERVABILITY` with `"pre_reg_hash"` cited in `reason` | Inject a second `pre_reg_hash` into half the stream; assert `relation_class == INSUFFICIENT_OBSERVABILITY` and `"pre_reg_hash" in reason` | Any other class returned, or `reason` does not cite `pre_reg_hash` |
| **H_H3** | `encoder_id` consistency | A cross-stream `encoder_id` mismatch is classified as `INSUFFICIENT_OBSERVABILITY` with `"encoder"` cited in `reason` | Run A with the canonical encoder, B with a different encoder; assert `INSUFFICIENT_OBSERVABILITY` and `"encoder" in reason` | Any other class returned, or `reason` does not cite encoder |
| **H_H4** | Offending-hash provenance | Every `offending_packet_hashes` entry returned by the audit matches the `output_hash` of some input packet | Trigger `SMOOTH_SUCCESS_EXCEPTION`; assert each returned hash is in `{p.output_hash for p in a ∪ b}` | Any returned hash not present in input `output_hash` set |

### Part M — AAA gold-standard meta-tests (quality-of-tests, NOT substantive)

Implemented in `ci/audit/tests/test_aaa_gold_standard.py`. Listed for completeness; failure halts substantive verdicts but never lifts to a paper action.

| ID | Property checked |
|---|---|
| **M1** | Every test function has a docstring |
| **M2** | Every test function contains at least one `assert` |
| **M3** | Test function names follow AAA / `test_<id>_<predicate>` convention |
| **M4** | No test imports an external LLM library |
| **M5** | No test depends on network or filesystem outside the repo |
| **M6** | Test function naming convention is strictly enforced |
| **M7** | No skipped tests outside documented skip reasons |
| **M8** | No `xfail` in audit substrate tests |
| **M9** | Test isolation: no class-level mutable state |
| **M10** | Substrate-wide AAA compliance summary |

### Hypothesis-to-RelationClass map

The §4 RelationClass enum implements the apparatus-soundness checks structurally. Mapping:

| RelationClass | Apparatus / invariant check (A, C, F, X, D, H) | Substantive hypothesis check (B) |
|---|---|---|
| `INSUFFICIENT_OBSERVABILITY` | flags H_A1–H_A6 candidate failure; corroborates H_A5 (DEFERRAL routing); fires on H_C1 (commitment), H_H2 (pre_reg_hash), H_H3 (encoder_id) | blocks Part B inference for the affected cell |
| `VERIFIER_SURFACE_MISMATCH` | partial H_A4 failure (verifier returns incomparable structures); H_F2 maps to COMPARISON | blocks Part B for the affected cell |
| `SMOOTH_SUCCESS_EXCEPTION` | apparatus-OK; H_X1 / H_X4 observability path; H_C2, H_C5, H_H4 trace-preservation checks | candidate H_B3 refutation (single-observer) |
| `TRUE_CERTIFICATE_REFUTATION` | apparatus-OK; H_A6 falsification predicate fires; H_C2, H_C5 trace populated | confirmed H_B3 refutation (cross-observer) |
| `LOCAL_CONTRACT_DIVERGENCE` | apparatus-OK; cross-observer disagreement; H_F2 maps to ROUTING; H_B4 refutation guard | candidate H_B1 / H_B5 refutation (contract gap, not chart shift) |
| `CHART_TRANSITION` | apparatus-OK; H_F2 maps to ROUTING; H_B4, H_B5 expected class for cross-generation / cross-family chart shifts | corroborates H_B5 (cross-family chart stability is observable) |
| `PIVOT_DISAGREEMENT` | apparatus-OK; H_F2 maps to ROUTING | candidate H_B1 / H_B8 refutation |
| `AGREEMENT` | apparatus-OK; H_F2 maps to ROUTING; H_X2 determinism corroborated; H_C4 routing discipline negative-case bound | strongest support for H_B1..H_B8 |

Across all classes: H_D1 asserts every row above maps to exactly one `PaperAction` per `ci/audit/decision_rule.py`; H_F1 asserts the row identifier is always a typed enum value (never a free-form proposition).

This map fixes ahead of time which hypotheses each RelationClass tests. No re-interpretation of classes after the experiment.

### Why structure the hypothesis program this way

Three reasons:

1. **Separating apparatus soundness from substantive truth.** A single mixed hypothesis ("the smooth/pivot decomposition holds") conflates "is our way of measuring it valid?" with "is the underlying claim true?" The cleanest experiments separate these. We test the apparatus first (Parts A, C, F, X, D, H); only when every apparatus / invariant check is confirmed sound do we let it speak about the substantive claim (Part B).

2. **Anchoring each hypothesis to an invariant family makes the apparatus tests exhaustive in advance.** The invariant families (state / budget / coherence / diagnostic / transition / falsification) enumerate what a bounded observer must satisfy to be admissible at all. The collapse-phase operator card from the external invariant spec adds five core invariants (commitment, trace, irreversibility, routing, residue) plus four falsifiers and a forbidden/allowed lift discipline. By writing one hypothesis per family / invariant / falsifier, we have pre-registered coverage of every way the apparatus could fail. There is no "we forgot to check X" path; the spec-correspondence bridge at `ci/audit/tests/test_invariant_spec_correspondence.py` enforces this mechanically.

3. **Quality of the test program is itself checked.** Part M (gold-standard meta-tests) asserts the test corpus follows AAA discipline, has no LLM/network/filesystem couplings, and has no skipped or `xfail` cases. Substantive verdicts depend on tests that pass; this layer guarantees the tests themselves are not silently broken.

This is the discipline that makes the audit observer a real check rather than a circular restatement of the paper claim.

## §4. RelationClass Enum (locked, pre-declared)

```python
class RelationClass(Enum):
    AGREEMENT                     = "agreement"
    LOCAL_CONTRACT_DIVERGENCE     = "local_contract_divergence"
    CHART_TRANSITION              = "chart_transition"
    PIVOT_DISAGREEMENT            = "pivot_disagreement"
    VERIFIER_SURFACE_MISMATCH     = "verifier_surface_mismatch"
    SMOOTH_SUCCESS_EXCEPTION      = "smooth_success_exception"
    TRUE_CERTIFICATE_REFUTATION   = "true_certificate_refutation"
    INSUFFICIENT_OBSERVABILITY    = "insufficient_observability"
```

### §4.1 Predicate Logic (deterministic, no LLM, hash-pinned)

For a single packet `p`:
```
smooth(p) := (p.max_chunk_displacement <= 2.5 * L_hat_observer_for_model)
             AND (p.max_drift_deg <= 15.0)
passed(p) := p.verifier_result["pass_both"] == True
```

For a single high-tier packet `p`:
```
smooth_success_exception(p) := smooth(p) AND passed(p)
```

For a stream comparison between observers A and B at fixed (task, tier):
```
contract_agreement(A, B) := |pass_rate(A) - pass_rate(B)| <= 0.10
chart_agreement(A, B)    := |L_hat(A) - L_hat(B)| / max(L_hat(A), L_hat(B)) <= 0.50
                            (i.e., chart constants within 50%)
regime_agreement(A, B)   := |smooth_fraction(A) - smooth_fraction(B)| <= 0.10
```

### §4.2 RelationClass assignment rules

For each (task, tier) cell, the audit observer outputs ONE RelationClass:

```
IF any packet in cell from A or B has `error != None`:
    RelationClass.INSUFFICIENT_OBSERVABILITY
    (reason: "observer error in stream")

ELIF tier == "high" AND ANY (smooth_success_exception(p) for p in A or B):
    IF the same exception appears in BOTH A and B (overlapping (task) → same task-level pattern):
        RelationClass.TRUE_CERTIFICATE_REFUTATION
    ELSE:
        RelationClass.SMOOTH_SUCCESS_EXCEPTION
    (output the offending packet for human review; do NOT auto-extrapolate)

ELIF NOT contract_agreement(A, B):
    IF chart_agreement(A, B):
        RelationClass.LOCAL_CONTRACT_DIVERGENCE
    ELSE:
        RelationClass.VERIFIER_SURFACE_MISMATCH

ELIF NOT chart_agreement(A, B):
    RelationClass.CHART_TRANSITION

ELIF NOT regime_agreement(A, B):
    RelationClass.PIVOT_DISAGREEMENT

ELSE:
    RelationClass.AGREEMENT
```

INSUFFICIENT_OBSERVABILITY is also emitted when:
- L_hat_observer_for_model cannot be computed (control-tier sample too small: n < 10)
- Encoder version mismatch between A and B
- Pre-reg hash mismatch in any input packet

---

## §5. Sample Design (locked)

| Parameter | Value | Justification |
|---|---|---|
| Black-box A models | claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-7 | Exhaustive frontier Claude tiers; not selected for outcome |
| Black-box B models | qwen-2.5-coder-7b-instruct (via OpenRouter) | Same model class as paper's original calibration |
| Tasks | 6: factorial, fibonacci, is_palindrome, gcd, reverse_words, fizzbuzz | Selected for verifier stability, NOT cliff exhibition |
| Tiers | control, low, moderate, high | Standard 4-tier from code_constraint_verifier.FORMAT_TIERS |
| Trials per cell | 10 | Large enough for tier-level Wilson CIs; total tractable in budget |
| Total Claude trials | 3 × 6 × 4 × 10 = 720 | High-tier subset: 180 |
| Total OpenRouter trials | 1 × 6 × 4 × 10 = 240 | High-tier subset: 60 |
| Temperature | 1.0 (claude-opus-4-7: default) | Real sampling variance, not deterministic clones |
| Max tokens | 1024 | Covers typical code completions |
| Concurrency | 8 per provider | Within rate limits |
| Total estimated cost | ≈ $30 | Mixed Claude + OpenRouter rates |

---

## §6. Calibration / Validation Split (locked)

- **Calibration set** (per black-box observer, per model): all control-tier trials.
  L_hat = mean per-chunk displacement over all calibration packets for that (observer, model).
- **Validation set**: low + moderate + high tier trials.
- **Headline analysis**: high-tier trials only (the regime where the paper claim lives).
- **No retuning**: thresholds 2.5 and 15° are paper-stated, applied unchanged.

---

## §7. Statistical Methods (locked)

- **Proportion CIs**: Wilson 95% intervals (preferred over normal-approximation near 0/1).
  Reported alongside point estimates for every smooth/pivot fraction and pass rate.
- **Clustered CIs**: task-clustered bootstrap (1000 resamples at task level) reported as
  secondary, more honest interval (Wilson assumes independence; trials cluster within tasks).
- **Bonferroni correction** applied across the 3 hypotheses (H1, H2, H3): α = 0.05/3 ≈ 0.017.
- **Separability detection** (H1): all four of {Hartigan dip-test, skewness > 2,
  KDE mode count via scipy.signal.find_peaks on log-density, upper-tail mass > 5% via
  empirical 95th percentile / median ratio}; H1 supported if ANY satisfied.
- **Sensitivity grid**: report results at all 9 cells of {2.0, 2.5, 3.0} × {10, 15, 20}.
  Primary inference uses (2.5, 15°). If primary differs from sensitivity by >5pp, note it.
- **Encoder cross-check**: re-embed final completions in mpnet-base-v2;
  Cohen's κ between MiniLM and mpnet smooth/pivot classifications must be > 0.6
  for the construct to be encoder-stable. If κ ≤ 0.6, flag as
  CONSTRUCT_ENCODER_DEPENDENT in the audit output.

---

## §8. Power Analysis (pre-registered, honest)

At N=180 high-tier Claude trials and observed 0 smooth-success exceptions:
- Wilson 95% one-sided upper CI on true smooth-failure rate = 2.04%
- We can rule out true rates > 2.04% at α=0.05.
- We CANNOT distinguish 0% from rates in (0%, 2%).
- This is weaker than the paper's "0/4,272" upper CI of 0.07%.
- We report this honestly; we do not claim equivalent strength.

---

## §9. Decision Tree (locked, no improvising)

After the audit observer outputs RelationClass per (task, tier) cell, aggregate to a
**stream-level decision** by majority class with INSUFFICIENT_OBSERVABILITY taking precedence:

```
IF >=1 cell is INSUFFICIENT_OBSERVABILITY:
    PROCEED but flag the unobservable cells; do not make global claims about them.

IF any cell is TRUE_CERTIFICATE_REFUTATION:
    DECISION: paper headline "0/4,272 smooth refutations" REFUTED at minimum on tested models.
    ACTION: rewrite headlines to remove the absolute "0/N" claim;
            replace with "no smooth-regime refutations observed in {open-weight} (paper's original);
                          one observed in {Claude family at task X tier Y} (this experiment);
                          this evidences chart-stratification of the contract."
    paper update: mandatory.

ELIF any cell is SMOOTH_SUCCESS_EXCEPTION (single-observer):
    DECISION: candidate refutation in one observer, not corroborated by other.
    ACTION: report the exception with full diagnostic data (chunk trace, drift, verifier output).
            Classify as either (α) within-frame exception or (β) frame-transition event.
            Do NOT auto-collapse to global refutation; flag for human review.
    paper update: add a footnote acknowledging the single-observer exception.

ELIF majority of cells are AGREEMENT:
    DECISION: O_audit_v1 supports the paper claim under both observer streams.
    ACTION: add Claude measurement table + OpenRouter measurement table to paper as
            cross-family corroboration. Keep headlines.
    paper update: additive (new table, no headline changes).

ELIF majority of cells are CHART_TRANSITION:
    DECISION: same outcome, different chart positions across observer families.
              The contract exists but L_hat is family-specific.
    ACTION: reframe paper to per-family L_hat; report L_hat range across families.
    paper update: small reframe of §lipschitz_calibration.

ELIF majority of cells are PIVOT_DISAGREEMENT or LOCAL_CONTRACT_DIVERGENCE:
    DECISION: smooth/pivot decomposition does not transfer cleanly across observer families.
    ACTION: scope the contract to the originally-measured family (open-weight code generation).
            Drop or qualify the cross-family transfer claim.
    paper update: substantive reframe of §empirical and abstract.

ELIF majority is VERIFIER_SURFACE_MISMATCH:
    DECISION: the verifier itself behaves differently across observer streams.
    ACTION: investigate verifier; this is a verifier-validity finding, not a paper headline.
            Flag for follow-up.
    paper update: footnote noting verifier surface dependence; full investigation in v.next.
```

**No improvisation.** If the audit produces a result not listed above (it should not, given the
enum is exhaustive), halt and document.

---

## §10. Honest Scope Statement (will appear verbatim in the paper)

> The smooth/pivot decomposition reported in §empirical was originally derived from a
> calibrated parametric model on 3 open-weight code-generation models (Qwen-2.5-Coder-1.5B,
> DeepSeek-Coder-1.3B, TinyLlama-1.1B), not from a per-trial trajectory + verifier-outcome
> join. To validate the decomposition empirically, we instantiated a deterministic audit
> observer (`O_audit_v1`, see supplementary `ci/audit/`) over two independent black-box
> generative observers (Claude family via Anthropic API; qwen-2.5-coder-7b via OpenRouter)
> producing typed ObservationPackets with identical schema. The audit observer's source
> contains no LLM-API import (verified mechanically by cert layer L30) and is mutation-tested
> via Cosmic Ray (results in `ci/audit/mutation_test_results.json`). Predicate logic and
> decision rules were locked in this pre-registration before any data was collected
> (sha256: `<computed>`). Outcome under `O_audit_v1`: **<filled after run>**.

---

## §10b. Substrate Implementation Notes (non-deviations from spec)

The substrate at `ci/audit/` implements §3 schema + §4 predicate logic exactly. Two storage-layer details to clarify the spec without changing semantics:

1. **`chunk_trace` and `verifier_result` are stored as tuples in the frozen dataclass** (so `ObservationPacket` is hashable and hash-deterministic), but exposed via `chunk_trace_list()` and `verifier_dict()` accessors that return the dict-list view spec'd in §3. Semantic content is identical; only the in-memory container is immutable. JSONL serialization round-trips cleanly to/from the §3 dict-list shape.

2. **`AuditObserver.audit_streams(stream_a, stream_b)` pools control-tier packets per-stream**, not per-(observer, model). The per-(observer, model) calibration spec'd in §6 is the responsibility of the experiment driver: it passes pre-filtered control packet sets to `AuditObserver.classify_cell(packets_a, packets_b, control_a, control_b)` directly. Both APIs exist; both are tested.

Substrate verification at first-packet-emission time:
- `python -m pytest ci/audit/tests/ -q` returns 45/45 pass
- `python -m pytest ci/audit/tests/ --cov=ci/audit --cov-report=term-missing --cov-branch` returns 100%/100%
- AST-based purity check (`test_purity.py`) confirms no LLM imports
- `cosmic-ray` mutation pass on `ci/audit/audit_observer.py` and `ci/audit/observer.py` runs separately; result file at `ci/audit/mutation_test_results.json` (kill rate is reported, not asserted at any specific level since the documented stub of §13 may produce one acceptable surviving mutant on the SMOOTH_SUCCESS_EXCEPTION → TRUE_CERTIFICATE_REFUTATION promotion logic).

## §11. Output Specification (locked)

Three output files, all include `pre_reg_hash` field for audit chaining:

1. `supplementary/experiments/outputs/audit_v4/claude_observation_packets.jsonl`
   (one ObservationPacket per line, ~720 lines)
2. `supplementary/experiments/outputs/audit_v4/openrouter_observation_packets.jsonl`
   (one ObservationPacket per line, ~240 lines)
3. `ci/audit_runtime_results.json`
   - `_meta`: pre_reg_hash, manifest_hash, audit_observer_id, run_timestamp
   - `per_cell_audit`: list of {observer_pair, task, tier, RelationClass, evidence}
   - `stream_decision`: {dominant_class, decision_rule_applied, paper_action}
   - `headlines_under_O_audit`: {smooth_fraction_per_observer, pivot_fraction_per_observer,
                                  high_tier_pass_rate_per_observer, L_hat_per_observer,
                                  smooth_success_exceptions: [<offending_packet_hashes>]}

### Per-run manifest provenance fields (additive, v4-compatible)

Each per-run `run_manifest.json` (under `supplementary/experiments/outputs/audit_v4/run_<UTC>/`)
also carries the following run-level provenance fields (added to support
reviewer reproducibility; pre-reg version stays v4 because these are
purely additive — no claim depends on their value):

1. `git_commit_sha` (str): full SHA of HEAD; `NOT_A_GIT_REPO` /
   `GIT_UNAVAILABLE` if not in a git work-tree or git is missing.
2. `git_commit_sha_short` (str): 12-char short SHA (same sentinel rules).
3. `git_status_clean` (bool): `true` iff `git status --porcelain` is empty.
   When `false`, the SHA alone is insufficient to reproduce — uncommitted
   changes are present.
4. `git_branch` (str): output of `git rev-parse --abbrev-ref HEAD`.
5. `encoder_model_file_sha256` (str): SHA256 of the MiniLM-L6-v2
   encoder's `state_dict()` (encoding-agnostic — same hash whether
   the on-disk file is `pytorch_model.bin` or `model.safetensors`).
6. `library_versions` (Dict[str, str]): installed versions of
   `anthropic`, `openai`, `sentence_transformers`, `torch`, `scipy`,
   `numpy`, `pytest`. Missing libraries are recorded as `NOT_INSTALLED`.
7. `python_version` (str): full `sys.version` string.
8. `platform_info` (Dict[str, str]): `system`, `release`, `machine`,
   `python_implementation`.

---

## §12. Stopping Rule and No-Peeking

- Single complete-sample run. No early stopping.
- No re-runs with different parameters after seeing results.
- If API errors cause >20% missing trials, document and proceed; do NOT add trials to compensate.
- If <20% missing, report as missing in the denominator; do not exclude.

---

## §13. The One Documented Stub

Following the typed-observer pattern from prior symbolic-substrate work (see `ci/audit/README.md`),
we document one explicit boundary: the audit observer cannot deterministically distinguish
between (α) genuine within-frame smooth-success exceptions and (β) frame-transition events
without human review of the offending packet. When SMOOTH_SUCCESS_EXCEPTION is emitted, the
audit observer outputs the diagnostic data and explicitly defers the (α)/(β) classification.
This is the registered boundary; it is not a hidden gap.

---

## §14. Sign-Off

Pre-registration locked at the timestamp recorded in git commit metadata for this file.
SHA256 of this file (computed AFTER final edit, BEFORE first API call):
`<sha256_of_this_document>`
(Recorded in every output JSON's `_meta.pre_reg_hash` field.)

Any deviation from this document during execution constitutes a protocol violation and
must be documented with explicit justification. Re-running the experiment with different
parameters is permitted only AFTER updating this pre-registration and re-locking
(yielding pre-reg v5).
