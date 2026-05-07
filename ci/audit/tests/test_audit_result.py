"""AAA tests for AuditResult dataclass."""

import dataclasses

import pytest

from ci.audit.audit_result import AuditResult
from ci.audit.relation_classes import RelationClass


def _make_result(**overrides):
    base = dict(
        observer_pair=("B_claude", "B_openweight"),
        task="factorial",
        tier="high",
        relation_class=RelationClass.AGREEMENT,
        pass_rate_a=0.5,
        pass_rate_b=0.5,
        smooth_fraction_a=0.9,
        smooth_fraction_b=0.9,
        l_hat_a=0.1,
        l_hat_b=0.1,
        n_a=10,
        n_b=10,
        reason="all three predicates satisfied",
    )
    base.update(overrides)
    return AuditResult(**base)


def test_audit_result_is_frozen():
    # Arrange
    r = _make_result()

    # Act / Assert
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.tier = "low"  # type: ignore[misc]


def test_audit_result_field_types():
    # Arrange
    r = _make_result(offending_packet_hashes=("abc", "def"))

    # Act / Assert
    assert isinstance(r.observer_pair, tuple)
    assert len(r.observer_pair) == 2
    assert isinstance(r.task, str)
    assert isinstance(r.tier, str)
    assert isinstance(r.relation_class, RelationClass)
    assert isinstance(r.pass_rate_a, float)
    assert isinstance(r.pass_rate_b, float)
    assert isinstance(r.smooth_fraction_a, float)
    assert isinstance(r.smooth_fraction_b, float)
    assert isinstance(r.l_hat_a, float)
    assert isinstance(r.l_hat_b, float)
    assert isinstance(r.n_a, int)
    assert isinstance(r.n_b, int)
    assert isinstance(r.reason, str)
    assert isinstance(r.offending_packet_hashes, tuple)


def test_audit_result_default_offending_hashes_is_empty_tuple():
    # Arrange / Act
    r = _make_result()

    # Assert
    assert r.offending_packet_hashes == tuple()
