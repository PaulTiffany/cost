# Audit Observer Substrate (`ci/audit/`)

Deterministic intermediary between two black-box generative observers.
Implements the schema, predicate logic, and `RelationClass` assignment
rules locked in
`supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md`.

## Typed-Observer Pattern

This substrate follows the typed-observer pattern from prior
symbolic-substrate work: each observer is a typed object that carries
explicit resolution and an append-only drift history, and exposes a
deterministic resolution-adjustment callable. The audit layer itself is
also an observer in this sense — it consumes typed `ObservationPacket`
objects and emits typed `AuditResult` objects, one per
`(observer_pair, task, tier)` cell.

The pattern matters here for one reason: the audit observer must be
mechanically distinguishable from the black-box generative observers
it audits. It cannot itself be a language model. The substrate is
verified pure (`tests/test_purity.py`) by AST-parsing every `.py` file
in `ci/audit/` and asserting that none of the names `anthropic`,
`openai`, `openrouter`, `transformers`, or `torch` appear in any
`import` or `from ... import` statement.

## Public Interface

```python
from ci.audit import (
    AuditObserver,         # the deterministic intermediary
    AuditResult,           # one frozen dataclass per cell
    Observer,              # typed observer with resolution + drift history
    ObservationPacket,     # frozen dataclass, schema locked in pre-reg §3
    RelationClass,         # 8-value enum locked in pre-reg §4
)
```

`AuditObserver.classify_cell(packets_a, packets_b, control_a, control_b)`
returns one `AuditResult`. `AuditObserver.audit_streams(stream_a, stream_b)`
groups two streams of packets by `(task, tier)` and returns one
`AuditResult` per cell.

## The One Documented Stub

Per pre-reg v4 §13: when `SMOOTH_SUCCESS_EXCEPTION` is emitted, the
audit observer **cannot deterministically distinguish** between

- (alpha) a genuine within-frame smooth-success exception, and
- (beta) a frame-transition event that happens to land inside the
  smooth band.

The substrate emits the offending packet's `output_hash` in the
`AuditResult.offending_packet_hashes` field and explicitly defers the
(alpha)/(beta) classification to human review with the full chunk
trace, drift series, and verifier output. This is the registered
boundary; it is not a hidden gap.

## Determinism

The substrate has no randomness, no clock reads, and no I/O.
`classify_cell` and `audit_streams` are pure functions of their
inputs: invoking them twice on the same input produces byte-identical
output, and the `AuditResult` dataclass is frozen. Thresholds are
pinned in `audit_observer.py` as module-level constants.
