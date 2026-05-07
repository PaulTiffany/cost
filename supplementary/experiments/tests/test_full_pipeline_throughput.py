"""End-to-end throughput tests for the experiment pipeline.

These tests construct synthetic ObservationPackets directly and run them
through the audit observer + decision rule, verifying that the pipeline
produces well-shaped AuditResults and PaperActions WITHOUT issuing any
LLM API call. They lock in the JSONL serialisation contract too.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

# Make the audit substrate (under ci/audit/) importable as ``ci.audit.*``.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ci.audit.audit_observer import AuditObserver  # noqa: E402
from ci.audit.audit_result import AuditResult  # noqa: E402
from ci.audit.decision_rule import (  # noqa: E402
    PaperAction,
    apply_decision_rule,
)
from ci.audit.observation_packet import ObservationPacket  # noqa: E402
from ci.audit.relation_classes import RelationClass  # noqa: E402


# --------------------------------------------------------------------- #
# Packet builders (kept small; only fields relevant to the pipeline are
# parametrised — every other field gets a deterministic default).
# --------------------------------------------------------------------- #


def _make_packet(
    *,
    observer_id: str = "B_claude",
    model_id: str = "claude-haiku-4-5",
    task_id: str = "factorial",
    tier: str = "control",
    trial_idx: int = 0,
    pass_both: bool = True,
    max_chunk_displacement: float = 0.1,
    mean_chunk_displacement: float = 0.05,
    max_drift_deg: float = 1.0,
    error=None,
    output_text: str = "ok",
    output_hash: str = "deadbeef",
    pre_reg_hash: str = "PRE",
    encoder_id: str = "miniLM",
) -> ObservationPacket:
    """Build a frozen ObservationPacket with sensible defaults."""
    verifier_tuple = (
        ("pass_a", pass_both),
        ("pass_b", pass_both),
        ("pass_both", pass_both),
        ("msg_a", "ok"),
        ("msg_b", "ok"),
    )
    return ObservationPacket(
        packet_schema_version="v4.0",
        observer_id=observer_id,
        model_id=model_id,
        api_model_snapshot=model_id,
        task_id=task_id,
        tier=tier,
        trial_idx=trial_idx,
        prompt_text="prompt",
        prompt_hash="ph",
        output_text=output_text,
        output_hash=output_hash,
        chunk_trace=tuple(),
        n_chunks=0,
        max_chunk_displacement=max_chunk_displacement,
        mean_chunk_displacement=mean_chunk_displacement,
        max_drift_deg=max_drift_deg,
        verifier_result=verifier_tuple,
        verifier_hash="vh",
        timestamp_utc="2026-05-04T00:00:00+00:00",
        encoder_id=encoder_id,
        encoder_package_version="0.0.0",
        pre_reg_hash=pre_reg_hash,
        manifest_hash="MH",
        error=error,
    )


# --------------------------------------------------------------------- #
# T1 — synthetic packets through the audit observer
# --------------------------------------------------------------------- #


def test_throughput_T1_synthetic_packets_flow_through_audit_observer():
    """T1: 10 control packets per stream (calibration sample) plus matched
    test-tier packets produce one well-shaped AuditResult per (task, tier)
    cell. All required AuditResult fields are populated.
    Refutation: missing fields, wrong type, or empty result list.
    """
    # Arrange — 10 control packets each side + 5 low-tier packets each side.
    packets_a = [
        _make_packet(observer_id="B_claude", tier="control", trial_idx=i)
        for i in range(10)
    ] + [
        _make_packet(observer_id="B_claude", tier="low", trial_idx=i,
                     pass_both=True)
        for i in range(5)
    ]
    packets_b = [
        _make_packet(observer_id="B_openweight", tier="control", trial_idx=i)
        for i in range(10)
    ] + [
        _make_packet(observer_id="B_openweight", tier="low", trial_idx=i,
                     pass_both=True)
        for i in range(5)
    ]
    obs = AuditObserver()

    # Act
    results = obs.audit_streams(packets_a, packets_b)

    # Assert
    assert len(results) == 1, f"expected one (factorial, low) cell; got {len(results)}"
    r = results[0]
    assert isinstance(r, AuditResult)
    assert r.task == "factorial"
    assert r.tier == "low"
    assert isinstance(r.relation_class, RelationClass)
    assert r.n_a == 5 and r.n_b == 5
    assert 0.0 <= r.pass_rate_a <= 1.0
    assert 0.0 <= r.pass_rate_b <= 1.0
    # Fully-agreeing streams should land on AGREEMENT.
    assert r.relation_class == RelationClass.AGREEMENT


# --------------------------------------------------------------------- #
# T2 — decision rule applied to a synthetic AuditResult
# --------------------------------------------------------------------- #


def test_throughput_T2_decision_rule_applied_to_synthetic_audit_result():
    """T2: synthesise one AuditResult per RelationClass and verify
    ``apply_decision_rule`` returns a ``PaperAction`` for every one of
    them (mapping is total per pre-reg §9).
    Refutation: a KeyError or a non-PaperAction return value.
    """
    # Arrange
    actions = []

    # Act
    for rc in RelationClass:
        result = AuditResult(
            observer_pair=("A", "B"),
            task="t", tier="control",
            relation_class=rc,
            pass_rate_a=0.5, pass_rate_b=0.5,
            smooth_fraction_a=0.5, smooth_fraction_b=0.5,
            l_hat_a=0.1, l_hat_b=0.1,
            n_a=10, n_b=10,
            reason="synthetic",
        )
        action = apply_decision_rule(result.relation_class)
        actions.append(action)

    # Assert
    assert len(actions) == len(list(RelationClass))
    assert all(isinstance(a, PaperAction) for a in actions)


# --------------------------------------------------------------------- #
# T3 — JSONL round trip
# --------------------------------------------------------------------- #


def test_throughput_T3_jsonl_round_trip_packet_serialization():
    """T3: a packet's dict form serialises to JSON, parses back, and yields
    a structurally-identical dict. This locks the JSONL on-disk contract
    that channels rely on for line-interleaved appends.
    Refutation: any field is dropped, re-typed, or differs after round trip.
    """
    # Arrange
    p = _make_packet(
        output_text="def f(): return 1\n",
        output_hash="abc123",
    )
    original = asdict(p)

    # Act
    line = json.dumps(original, ensure_ascii=False)
    parsed = json.loads(line)

    # Assert
    assert set(parsed.keys()) == set(original.keys())
    # JSON converts tuples to lists; compare against the list-coerced original.
    def _coerce(obj):
        if isinstance(obj, tuple):
            return [_coerce(x) for x in obj]
        if isinstance(obj, list):
            return [_coerce(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _coerce(v) for k, v in obj.items()}
        return obj

    assert parsed == _coerce(original)


# --------------------------------------------------------------------- #
# T4 — full synthetic pipeline → PaperAction
# --------------------------------------------------------------------- #


def test_throughput_T4_full_pipeline_synthetic_packets_to_paper_action():
    """T4: end-to-end pipeline with no API calls:
        synthetic packets → audit_streams → AuditResults
                          → apply_decision_rule per cell
                          → aggregate stream-level paper action.
    The aggregate action is the most-severe (mandatory > footnote > ...
    by ordering of ``PRECEDENCE``); for an all-agreement stream it must
    be ADD_MEASUREMENT_TABLE.
    Refutation: any cell fails to map, or aggregate is wrong shape.
    """
    # Arrange — control + a small experimental tier across two tasks.
    packets_a = []
    packets_b = []
    for task in ("factorial", "fibonacci"):
        for i in range(10):
            packets_a.append(_make_packet(
                observer_id="B_claude", task_id=task, tier="control",
                trial_idx=i,
            ))
            packets_b.append(_make_packet(
                observer_id="B_openweight", task_id=task, tier="control",
                trial_idx=i,
            ))
        for i in range(5):
            packets_a.append(_make_packet(
                observer_id="B_claude", task_id=task, tier="low",
                trial_idx=i, pass_both=True,
            ))
            packets_b.append(_make_packet(
                observer_id="B_openweight", task_id=task, tier="low",
                trial_idx=i, pass_both=True,
            ))

    obs = AuditObserver()

    # Act
    results = obs.audit_streams(packets_a, packets_b)
    actions = [apply_decision_rule(r.relation_class) for r in results]

    # Aggregate via a deterministic precedence (most-severe wins).
    PRECEDENCE = [
        PaperAction.MANDATORY_REWRITE,
        PaperAction.INVESTIGATE_VERIFIER,
        PaperAction.SCOPE_TO_OPEN_WEIGHT,
        PaperAction.REFRAME_PER_FAMILY_L_HAT,
        PaperAction.FOOTNOTE_EXCEPTION,
        PaperAction.DEFER,
        PaperAction.ADD_MEASUREMENT_TABLE,
        PaperAction.KEEP_HEADLINE,
    ]
    aggregate = next(a for a in PRECEDENCE if a in actions)

    # Assert
    assert len(results) == 2  # one cell per task at the (low) experimental tier
    assert all(r.relation_class == RelationClass.AGREEMENT for r in results)
    assert aggregate == PaperAction.ADD_MEASUREMENT_TABLE
