"""AAA tests for ObservationPacket schema (pre-reg v4 §3, v4.1 additive)."""

import dataclasses
import json

import pytest

from ci.audit.observation_packet import PACKET_SCHEMA_VERSION, ObservationPacket


def _make_packet(**overrides):
    base = dict(
        packet_schema_version=PACKET_SCHEMA_VERSION,
        observer_id="B_claude",
        model_id="claude-haiku-4-5",
        api_model_snapshot="claude-haiku-4-5-2026-01-15",
        task_id="factorial",
        tier="control",
        trial_idx=0,
        prompt_text="Write factorial.",
        prompt_hash="0" * 64,
        output_text="def factorial(n): ...",
        output_hash="1" * 64,
        chunk_trace=tuple(),
        n_chunks=0,
        max_chunk_displacement=0.0,
        mean_chunk_displacement=0.0,
        max_drift_deg=0.0,
        verifier_result=(("pass_a", True), ("pass_b", True), ("pass_both", True)),
        verifier_hash="2" * 64,
        timestamp_utc="2026-05-04T00:00:00Z",
        encoder_id="sentence-transformers/all-MiniLM-L6-v2",
        encoder_package_version="3.0.1",
        pre_reg_hash="3" * 64,
        manifest_hash="4" * 64,
        error=None,
    )
    base.update(overrides)
    return ObservationPacket(**base)


def test_packet_is_frozen():
    # Arrange
    packet = _make_packet()

    # Act / Assert
    with pytest.raises(dataclasses.FrozenInstanceError):
        packet.tier = "high"  # type: ignore[misc]


def test_packet_has_complete_pre_reg_schema():
    # Arrange
    expected_fields = {
        "packet_schema_version",
        "observer_id",
        "model_id",
        "api_model_snapshot",
        "task_id",
        "tier",
        "trial_idx",
        "prompt_text",
        "prompt_hash",
        "output_text",
        "output_hash",
        "chunk_trace",
        "n_chunks",
        "max_chunk_displacement",
        "mean_chunk_displacement",
        "max_drift_deg",
        "verifier_result",
        "verifier_hash",
        "timestamp_utc",
        "encoder_id",
        "encoder_package_version",
        "pre_reg_hash",
        "manifest_hash",
        "error",
        # v4.1 additive provenance fields
        "api_request_id",
        "actual_token_usage",
        "stop_reason",
        "openrouter_provider",
        "wall_time_ms",
    }

    # Act
    actual_fields = {f.name for f in dataclasses.fields(ObservationPacket)}

    # Assert
    assert actual_fields == expected_fields


def test_packet_hash_is_deterministic():
    # Arrange
    p1 = _make_packet()
    p2 = _make_packet()

    # Act
    h1 = hash(p1)
    h2 = hash(p2)

    # Assert
    assert h1 == h2
    assert p1 == p2


def test_verifier_dict_helper_unpacks_tuple():
    # Arrange
    packet = _make_packet(
        verifier_result=(
            ("pass_a", False),
            ("pass_b", True),
            ("pass_both", False),
        )
    )

    # Act
    d = packet.verifier_dict()

    # Assert
    assert d == {"pass_a": False, "pass_b": True, "pass_both": False}


def test_chunk_trace_list_helper():
    # Arrange
    packet = _make_packet(
        chunk_trace=(
            (("chunk_idx", 0), ("displacement", 0.1)),
            (("chunk_idx", 1), ("displacement", 0.2)),
        ),
    )

    # Act
    chunks = packet.chunk_trace_list()

    # Assert
    assert chunks == [
        {"chunk_idx": 0, "displacement": 0.1},
        {"chunk_idx": 1, "displacement": 0.2},
    ]


# ----------------------------------------------------------------------- #
# v4.1 additive provenance fields
# ----------------------------------------------------------------------- #
def test_observation_packet_v41_optional_fields_default_none():
    """v4.1 additive fields: when omitted, all default to None and the
    packet still constructs cleanly with the v4.0 field set."""
    # Arrange / Act: build a packet without supplying any v4.1 field
    packet = _make_packet()

    # Assert: every v4.1 field defaults to None
    assert packet.api_request_id is None
    assert packet.actual_token_usage is None
    assert packet.stop_reason is None
    assert packet.openrouter_provider is None
    assert packet.wall_time_ms is None


def test_observation_packet_v41_optional_fields_round_trip():
    """v4.1 additive fields: set, serialize to JSON, parse back, and
    reconstruct an equal-valued packet."""
    # Arrange
    usage = {"input_tokens": 41, "output_tokens": 73}
    packet = _make_packet(
        api_request_id="msg_01ABCDEF",
        actual_token_usage=usage,
        stop_reason="end_turn",
        openrouter_provider=None,
        wall_time_ms=1234.5,
    )

    # Act: dataclasses.asdict() expands the dict, then JSON round-trip
    payload = json.dumps(dataclasses.asdict(packet))
    parsed = json.loads(payload)

    # Assert: provenance fields survive the round trip with identical values
    assert parsed["api_request_id"] == "msg_01ABCDEF"
    assert parsed["actual_token_usage"] == usage
    assert parsed["stop_reason"] == "end_turn"
    assert parsed["openrouter_provider"] is None
    assert parsed["wall_time_ms"] == 1234.5
    # And the reconstructed dataclass equals the original
    # (chunk_trace / verifier_result are tuples in the dataclass, so
    # rebuild from JSON list-shape with the same tuple coercion the
    # storage layer performs in §10b.)
    parsed["chunk_trace"] = tuple(
        tuple(tuple(kv) for kv in c) for c in parsed["chunk_trace"]
    )
    parsed["verifier_result"] = tuple(tuple(kv) for kv in parsed["verifier_result"])
    rebuilt = ObservationPacket(**parsed)
    assert rebuilt.api_request_id == packet.api_request_id
    assert rebuilt.actual_token_usage == packet.actual_token_usage
    assert rebuilt.stop_reason == packet.stop_reason
    assert rebuilt.wall_time_ms == packet.wall_time_ms


def test_observation_packet_v41_v40_packets_still_loadable():
    """Backward compatibility: a JSON dict in the v4.0 shape (no v4.1
    fields present) must still construct a valid packet."""
    # Arrange: build the v4.0 field set explicitly, omitting all v4.1 keys
    v40_shape = dict(
        packet_schema_version="v4.0",
        observer_id="B_claude",
        model_id="claude-haiku-4-5",
        api_model_snapshot="claude-haiku-4-5-2026-01-15",
        task_id="factorial",
        tier="control",
        trial_idx=0,
        prompt_text="Write factorial.",
        prompt_hash="0" * 64,
        output_text="def factorial(n): ...",
        output_hash="1" * 64,
        chunk_trace=tuple(),
        n_chunks=0,
        max_chunk_displacement=0.0,
        mean_chunk_displacement=0.0,
        max_drift_deg=0.0,
        verifier_result=(("pass_a", True), ("pass_b", True), ("pass_both", True)),
        verifier_hash="2" * 64,
        timestamp_utc="2026-05-04T00:00:00Z",
        encoder_id="sentence-transformers/all-MiniLM-L6-v2",
        encoder_package_version="3.0.1",
        pre_reg_hash="3" * 64,
        manifest_hash="4" * 64,
        error=None,
    )

    # Act
    packet = ObservationPacket(**v40_shape)

    # Assert: construction succeeds and v4.1 fields are present as None
    assert packet.packet_schema_version == "v4.0"
    assert packet.api_request_id is None
    assert packet.actual_token_usage is None
    assert packet.stop_reason is None
    assert packet.openrouter_provider is None
    assert packet.wall_time_ms is None
