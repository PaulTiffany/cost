"""Dedicated tests for ci/audit/control_router.py.

These tests cover the per-(observer, model) calibration routing in
isolation: partition behavior, per-cell selection, empty-input handling,
and the per-model L_hat differentiation property end-to-end.

Run alongside the audit substrate via the standard suite, AND mutated
in isolation by ``cosmic_ray_control.toml``.
"""

from __future__ import annotations

import pytest

from ci.audit import control_router as cr
from ci.audit.audit_observer import AuditObserver
from ci.audit.observation_packet import ObservationPacket
from ci.audit.relation_classes import RelationClass


PRE_REG_HASH = "deadbeef" * 8
ENCODER = "sentence-transformers/all-MiniLM-L6-v2"


def _packet(
    observer_id: str, task_id: str, tier: str, trial_idx: int,
    *,
    pass_both: bool = True,
    mean_chunk_displacement: float = 0.05,
    max_chunk_displacement: float = 0.05,
    max_drift_deg: float = 5.0,
    model_id: str = "claude-haiku-4-5",
) -> ObservationPacket:
    """Test fixture: build a minimal ObservationPacket with sensible defaults."""
    return ObservationPacket(
        packet_schema_version="v4.1",
        observer_id=observer_id,
        model_id=model_id,
        api_model_snapshot=f"{model_id}-snap",
        task_id=task_id,
        tier=tier,
        trial_idx=trial_idx,
        prompt_text=f"prompt-{task_id}",
        prompt_hash=f"p{task_id}{trial_idx}".ljust(64, "0"),
        output_text=f"output-{task_id}-{trial_idx}",
        output_hash=f"o{observer_id}{task_id}{trial_idx}".ljust(64, "0"),
        chunk_trace=tuple(),
        n_chunks=1,
        max_chunk_displacement=max_chunk_displacement,
        mean_chunk_displacement=mean_chunk_displacement,
        max_drift_deg=max_drift_deg,
        verifier_result=(
            ("pass_a", pass_both), ("pass_b", pass_both),
            ("pass_both", pass_both), ("msg_a", ""), ("msg_b", ""),
        ),
        verifier_hash="v" * 64,
        timestamp_utc="2026-05-04T00:00:00Z",
        encoder_id=ENCODER,
        encoder_package_version="3.0.1",
        pre_reg_hash=PRE_REG_HASH,
        manifest_hash="m" * 64,
        error=None,
    )


def _ctrl(observer_id: str, n: int, mean_disp: float, model_id: str):
    """Test fixture: build a list of n control-tier packets."""
    return [
        _packet(observer_id, "calib", "control", i,
                mean_chunk_displacement=mean_disp, model_id=model_id)
        for i in range(n)
    ]


# ===================================================================== #
# 1. partition_controls_by_model
# ===================================================================== #


def test_CR_partition_empty_stream_returns_empty_dict():
    """Empty input → empty dict. Refutation: any non-dict or non-empty result."""
    assert cr.partition_controls_by_model([]) == {}


def test_CR_partition_no_control_tier_returns_empty_dict():
    """Stream with only non-control tiers yields no calibration entries."""
    s = [_packet("A", "t", "low", i) for i in range(5)]
    assert cr.partition_controls_by_model(s) == {}


def test_CR_partition_single_model_groups_under_one_key():
    """All control packets share a model_id → single dict entry with all packets."""
    s = _ctrl("A", n=5, mean_disp=0.1, model_id="m_only")
    out = cr.partition_controls_by_model(s)
    assert set(out.keys()) == {"m_only"}
    assert len(out["m_only"]) == 5


def test_CR_partition_two_models_yield_two_entries():
    """Two model_ids in controls → two dict entries; counts match per model."""
    s = (
        _ctrl("A", n=3, mean_disp=0.1, model_id="m_x")
        + _ctrl("A", n=7, mean_disp=0.2, model_id="m_y")
    )
    out = cr.partition_controls_by_model(s)
    assert set(out.keys()) == {"m_x", "m_y"}
    assert len(out["m_x"]) == 3
    assert len(out["m_y"]) == 7


def test_CR_partition_drops_non_control_packets():
    """Mixed stream: only ``tier == "control"`` packets reach the partition."""
    s = (
        _ctrl("A", n=2, mean_disp=0.1, model_id="m_x")
        + [_packet("A", "t", "low", i, model_id="m_x") for i in range(5)]
    )
    out = cr.partition_controls_by_model(s)
    assert len(out["m_x"]) == 2  # not 7


# ===================================================================== #
# 2. select_controls_for_cell
# ===================================================================== #


def test_CR_select_empty_cell_returns_empty_list():
    """Cell with no packets → empty calibration; no model_id to look up."""
    assert cr.select_controls_for_cell({"m_x": [_packet("A", "t", "control", 0)]}, []) == []


def test_CR_select_model_absent_returns_empty_list():
    """Cell's model_id not in dict → empty list (flows to INSUFFICIENT)."""
    by_model = {"m_known": _ctrl("A", n=10, mean_disp=0.1, model_id="m_known")}
    cell = [_packet("A", "t", "low", 0, model_id="m_unknown")]
    assert cr.select_controls_for_cell(by_model, cell) == []


def test_CR_select_returns_correct_per_model_set():
    """Cell's model_id matches a key → returns those packets, not the other model's."""
    x_pkts = _ctrl("A", n=3, mean_disp=0.05, model_id="m_x")
    y_pkts = _ctrl("A", n=7, mean_disp=0.50, model_id="m_y")
    by_model = {"m_x": x_pkts, "m_y": y_pkts}
    cell_y = [_packet("A", "t", "low", 0, model_id="m_y")]
    out = cr.select_controls_for_cell(by_model, cell_y)
    assert len(out) == 7
    assert all(p.mean_chunk_displacement == 0.50 for p in out)


def test_CR_select_uses_first_packet_not_second_for_model_lookup():
    """Heterogeneous cell: packet[0].model_id differs from packet[1].model_id;
    select_controls_for_cell must use [0]'s model. Refutation: NumberReplacer
    mutating cell_packets[0] → cell_packets[1] would route to the other model.
    """
    by_model = {
        "m_first": _ctrl("A", n=3, mean_disp=0.05, model_id="m_first"),
        "m_second": _ctrl("A", n=99, mean_disp=0.50, model_id="m_second"),
    }
    cell = [
        _packet("A", "t", "low", 0, model_id="m_first"),
        _packet("A", "t", "low", 1, model_id="m_second"),
    ]
    out = cr.select_controls_for_cell(by_model, cell)
    assert len(out) == 3  # m_first set, not m_second's 99


def test_CR_select_returns_independent_list_copy():
    """Mutating the returned list must not corrupt the dict's stored value."""
    x_pkts = _ctrl("A", n=3, mean_disp=0.05, model_id="m_x")
    by_model = {"m_x": x_pkts}
    cell = [_packet("A", "t", "low", 0, model_id="m_x")]
    out = cr.select_controls_for_cell(by_model, cell)
    out.clear()
    assert len(by_model["m_x"]) == 3


# ===================================================================== #
# 3. select_controls_for_cell_pair
# ===================================================================== #


def test_CR_select_pair_routes_each_observer_to_its_own_model():
    """Pair selector returns (controls_a, controls_b) — each from its own dict."""
    x_a = _ctrl("A", n=3, mean_disp=0.1, model_id="m_x")
    y_b = _ctrl("B", n=4, mean_disp=0.2, model_id="m_y")
    by_a = {"m_x": x_a}
    by_b = {"m_y": y_b}
    cell_a = [_packet("A", "t", "low", 0, model_id="m_x")]
    cell_b = [_packet("B", "t", "low", 0, model_id="m_y")]
    out_a, out_b = cr.select_controls_for_cell_pair(by_a, by_b, cell_a, cell_b)
    assert len(out_a) == 3
    assert len(out_b) == 4


def test_CR_select_pair_handles_one_empty_cell():
    """Asymmetric: A-only cell → A gets controls, B gets empty."""
    by_a = {"m_x": _ctrl("A", n=10, mean_disp=0.1, model_id="m_x")}
    by_b = {"m_x": _ctrl("B", n=10, mean_disp=0.1, model_id="m_x")}
    cell_a = [_packet("A", "t", "low", 0, model_id="m_x")]
    cell_b = []
    out_a, out_b = cr.select_controls_for_cell_pair(by_a, by_b, cell_a, cell_b)
    assert len(out_a) == 10
    assert out_b == []


# ===================================================================== #
# 4. End-to-end via audit_streams: per-model L_hat differentiation
# ===================================================================== #


def test_CR_audit_streams_per_model_l_hat_differentiation():
    """Two models with very different mean displacements produce
    distinct per-model L_hat in their cells. Refutation: identical
    L_hat for both (proves routing collapsed to a pooled set).
    """
    obs = AuditObserver()
    a_low = (
        _ctrl("A", n=10, mean_disp=0.05, model_id="m_low")
        + _ctrl("A", n=10, mean_disp=0.50, model_id="m_high")
        + [_packet("A", "t", "low", i, model_id="m_low") for i in range(10)]
    )
    b_low = (
        _ctrl("B", n=10, mean_disp=0.05, model_id="m_low")
        + _ctrl("B", n=10, mean_disp=0.50, model_id="m_high")
        + [_packet("B", "t", "low", i, model_id="m_low") for i in range(10)]
    )
    res_low = obs.audit_streams(a_low, b_low)

    a_high = (
        _ctrl("A", n=10, mean_disp=0.05, model_id="m_low")
        + _ctrl("A", n=10, mean_disp=0.50, model_id="m_high")
        + [_packet("A", "t", "low", i, model_id="m_high") for i in range(10)]
    )
    b_high = (
        _ctrl("B", n=10, mean_disp=0.05, model_id="m_low")
        + _ctrl("B", n=10, mean_disp=0.50, model_id="m_high")
        + [_packet("B", "t", "low", i, model_id="m_high") for i in range(10)]
    )
    res_high = obs.audit_streams(a_high, b_high)

    assert res_low[0].l_hat_a == pytest.approx(0.05)
    assert res_high[0].l_hat_a == pytest.approx(0.50)
    assert res_low[0].l_hat_a != res_high[0].l_hat_a


def test_CR_audit_streams_unknown_model_yields_insufficient():
    """Cell whose model has no controls → INSUFFICIENT_OBSERVABILITY."""
    obs = AuditObserver()
    a = (
        _ctrl("A", n=10, mean_disp=0.1, model_id="m_known")
        + [_packet("A", "t", "low", i, model_id="m_unknown") for i in range(10)]
    )
    b = (
        _ctrl("B", n=10, mean_disp=0.1, model_id="m_known")
        + [_packet("B", "t", "low", i, model_id="m_unknown") for i in range(10)]
    )
    results = obs.audit_streams(a, b)
    assert len(results) == 1
    assert results[0].relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
