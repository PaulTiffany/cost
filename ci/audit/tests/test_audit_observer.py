"""AAA tests covering EVERY branch of pre-reg v4 §4.2."""

from typing import List

import pytest

from ci.audit.audit_observer import AuditObserver
from ci.audit.observation_packet import ObservationPacket
from ci.audit.relation_classes import RelationClass


PRE_REG_HASH = "deadbeef" * 8
ENCODER = "sentence-transformers/all-MiniLM-L6-v2"


def _packet(
    observer_id: str,
    task_id: str,
    tier: str,
    trial_idx: int,
    *,
    pass_both: bool = True,
    pass_a: bool = True,
    pass_b: bool = True,
    max_chunk_displacement: float = 0.05,
    mean_chunk_displacement: float = 0.05,
    max_drift_deg: float = 5.0,
    error: str | None = None,
    encoder_id: str = ENCODER,
    pre_reg_hash: str = PRE_REG_HASH,
    output_hash: str | None = None,
    model_id: str = "claude-haiku-4-5",
) -> ObservationPacket:
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
        output_hash=output_hash or f"o{observer_id}{task_id}{trial_idx}".ljust(64, "0"),
        chunk_trace=tuple(),
        n_chunks=1,
        max_chunk_displacement=max_chunk_displacement,
        mean_chunk_displacement=mean_chunk_displacement,
        max_drift_deg=max_drift_deg,
        verifier_result=(
            ("pass_a", pass_a),
            ("pass_b", pass_b),
            ("pass_both", pass_both),
            ("msg_a", ""),
            ("msg_b", ""),
        ),
        verifier_hash="v" * 64,
        timestamp_utc="2026-05-04T00:00:00Z",
        encoder_id=encoder_id,
        encoder_package_version="3.0.1",
        pre_reg_hash=pre_reg_hash,
        manifest_hash="m" * 64,
        error=error,
    )


def _control_set(observer_id: str, n: int = 10) -> List[ObservationPacket]:
    return [
        _packet(observer_id, "calib", "control", i, mean_chunk_displacement=0.1)
        for i in range(n)
    ]


# --------------------------------------------------------------------- #
# 1. AGREEMENT
# --------------------------------------------------------------------- #
def test_agreement_branch():
    # Arrange: identical pass rates, identical L_hat, identical smooth fraction
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
    b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

    # Act
    result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

    # Assert
    assert result.relation_class == RelationClass.AGREEMENT
    assert result.reason == "all three predicates satisfied"


# --------------------------------------------------------------------- #
# 2. LOCAL_CONTRACT_DIVERGENCE: pass-rate gap > 0.10, charts agree
# --------------------------------------------------------------------- #
def test_local_contract_divergence_branch():
    # Arrange: A all pass, B all fail; control sets identical -> charts agree
    obs = AuditObserver()
    a = [_packet("B_claude", "fizzbuzz", "low", i, pass_both=True) for i in range(10)]
    b = [_packet("B_openweight", "fizzbuzz", "low", i, pass_both=False) for i in range(10)]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert
    assert result.relation_class == RelationClass.LOCAL_CONTRACT_DIVERGENCE
    assert "charts in agreement" in result.reason


# --------------------------------------------------------------------- #
# 3. CHART_TRANSITION: pass-rates close, charts diverge
# --------------------------------------------------------------------- #
def test_chart_transition_branch():
    # Arrange: matching pass rates and smooth fractions; very different L_hats
    obs = AuditObserver()
    a = [_packet("B_claude", "gcd", "low", i, max_chunk_displacement=0.05) for i in range(10)]
    b = [_packet("B_openweight", "gcd", "low", i, max_chunk_displacement=0.05) for i in range(10)]
    control_a = [
        _packet("B_claude", "calib", "control", i, mean_chunk_displacement=0.1)
        for i in range(10)
    ]
    # Make B's L_hat 10x larger -> chart_agreement fails
    control_b = [
        _packet("B_openweight", "calib", "control", i, mean_chunk_displacement=1.0)
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(a, b, control_a, control_b)

    # Assert
    assert result.relation_class == RelationClass.CHART_TRANSITION
    assert "L_hat" in result.reason


# --------------------------------------------------------------------- #
# 4. PIVOT_DISAGREEMENT: pass-rate close, chart close, smooth-fraction far
# --------------------------------------------------------------------- #
def test_pivot_disagreement_branch():
    # Arrange: A smooth (low displacement), B not smooth (high drift); same pass rate
    obs = AuditObserver()
    a = [
        _packet("B_claude", "is_palindrome", "low", i, max_drift_deg=5.0)
        for i in range(10)
    ]
    b = [
        _packet("B_openweight", "is_palindrome", "low", i, max_drift_deg=45.0)
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert
    assert result.relation_class == RelationClass.PIVOT_DISAGREEMENT
    assert "smooth-fraction" in result.reason


# --------------------------------------------------------------------- #
# 5. VERIFIER_SURFACE_MISMATCH: pass-rate gap > 0.10 AND charts disagree
# --------------------------------------------------------------------- #
def test_verifier_surface_mismatch_branch():
    # Arrange: A all pass, B all fail; ALSO L_hats diverge
    obs = AuditObserver()
    a = [_packet("B_claude", "reverse_words", "low", i, pass_both=True) for i in range(10)]
    b = [_packet("B_openweight", "reverse_words", "low", i, pass_both=False) for i in range(10)]
    control_a = [
        _packet("B_claude", "calib", "control", i, mean_chunk_displacement=0.1)
        for i in range(10)
    ]
    control_b = [
        _packet("B_openweight", "calib", "control", i, mean_chunk_displacement=2.0)
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(a, b, control_a, control_b)

    # Assert
    assert result.relation_class == RelationClass.VERIFIER_SURFACE_MISMATCH
    assert "charts disagree" in result.reason


# --------------------------------------------------------------------- #
# 6. SMOOTH_SUCCESS_EXCEPTION: single-observer at high tier
# --------------------------------------------------------------------- #
def test_smooth_success_exception_single_observer():
    # Arrange: A high-tier all smooth+pass on factorial; B high-tier on a DIFFERENT task
    # (so no overlap). Provide the offending packet.
    obs = AuditObserver()
    a = [
        _packet(
            "B_claude",
            "factorial",
            "high",
            i,
            pass_both=True,
            max_chunk_displacement=0.05,
            max_drift_deg=2.0,
        )
        for i in range(10)
    ]
    # B is in the SAME (task, tier) cell from the audit grouping perspective,
    # but its packets do NOT trigger smooth_success_exception (e.g. they fail).
    b = [
        _packet(
            "B_openweight",
            "factorial",
            "high",
            i,
            pass_both=False,
            max_chunk_displacement=0.05,
            max_drift_deg=2.0,
        )
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert
    assert result.relation_class == RelationClass.SMOOTH_SUCCESS_EXCEPTION
    assert len(result.offending_packet_hashes) == 10
    assert "human review" in result.reason


# --------------------------------------------------------------------- #
# 7. TRUE_CERTIFICATE_REFUTATION: BOTH observers exhibit the exception on
#    the overlapping task at high tier.
# --------------------------------------------------------------------- #
def test_true_certificate_refutation_both_observers():
    # Arrange: A and B both pass + smooth at high tier on factorial
    obs = AuditObserver()
    a = [
        _packet(
            "B_claude",
            "factorial",
            "high",
            i,
            pass_both=True,
            max_chunk_displacement=0.05,
            max_drift_deg=2.0,
        )
        for i in range(10)
    ]
    b = [
        _packet(
            "B_openweight",
            "factorial",
            "high",
            i,
            pass_both=True,
            max_chunk_displacement=0.05,
            max_drift_deg=2.0,
        )
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert
    assert result.relation_class == RelationClass.TRUE_CERTIFICATE_REFUTATION
    assert "BOTH observers" in result.reason
    assert len(result.offending_packet_hashes) == 20


# --------------------------------------------------------------------- #
# 8. INSUFFICIENT_OBSERVABILITY: error in stream
# --------------------------------------------------------------------- #
def test_insufficient_error_in_stream():
    # Arrange
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", 0, error="api 500")]
    b = [_packet("B_openweight", "factorial", "low", 0)]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert result.reason == "observer error in stream"


# 8b. encoder mismatch
def test_insufficient_encoder_mismatch():
    # Arrange
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", i, encoder_id="MiniLM") for i in range(10)]
    b = [
        _packet("B_openweight", "factorial", "low", i, encoder_id="mpnet")
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(
        a,
        b,
        [_packet("B_claude", "calib", "control", i, encoder_id="MiniLM", mean_chunk_displacement=0.1) for i in range(10)],
        [_packet("B_openweight", "calib", "control", i, encoder_id="mpnet", mean_chunk_displacement=0.1) for i in range(10)],
    )

    # Assert
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert "encoder mismatch" in result.reason


# 8c. pre_reg_hash mismatch
def test_insufficient_pre_reg_hash_mismatch():
    # Arrange
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
    b = [
        _packet("B_openweight", "factorial", "low", i, pre_reg_hash="ff" * 32)
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert "pre_reg_hash mismatch" in result.reason


# 8d. n<10 calibration set
def test_insufficient_calibration_n_too_small_for_a():
    # Arrange
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
    b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude", n=5), _control_set("B_openweight", n=10)
    )

    # Assert
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert "observer A" in result.reason


def test_insufficient_calibration_n_too_small_for_b():
    # Arrange
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
    b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude", n=10), _control_set("B_openweight", n=3)
    )

    # Assert
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert "observer B" in result.reason


# --------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------- #
def test_classify_cell_is_deterministic():
    # Arrange
    obs = AuditObserver()
    a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
    b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
    ca = _control_set("B_claude")
    cb = _control_set("B_openweight")

    # Act
    r1 = obs.classify_cell(a, b, ca, cb)
    r2 = obs.classify_cell(a, b, ca, cb)

    # Assert
    assert r1 == r2


# --------------------------------------------------------------------- #
# audit_streams driver: groups by (task, tier) and produces one cell each
# --------------------------------------------------------------------- #
def test_audit_streams_groups_cells():
    # Arrange
    obs = AuditObserver()
    stream_a = (
        _control_set("B_claude")
        + [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        + [_packet("B_claude", "fibonacci", "moderate", i) for i in range(10)]
    )
    stream_b = (
        _control_set("B_openweight")
        + [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        + [_packet("B_openweight", "fibonacci", "moderate", i) for i in range(10)]
    )

    # Act
    results = obs.audit_streams(stream_a, stream_b)

    # Assert
    assert len(results) == 2
    keys = {(r.task, r.tier) for r in results}
    assert keys == {("factorial", "low"), ("fibonacci", "moderate")}
    for r in results:
        assert r.relation_class == RelationClass.AGREEMENT


# --------------------------------------------------------------------- #
# classify_packet helper coverage
# --------------------------------------------------------------------- #
def test_high_tier_with_no_smooth_success_exceptions_falls_through():
    # Arrange: high tier but every packet fails the verifier (no exception)
    obs = AuditObserver()
    a = [
        _packet(
            "B_claude",
            "factorial",
            "high",
            i,
            pass_both=False,
            max_chunk_displacement=0.05,
            max_drift_deg=2.0,
        )
        for i in range(10)
    ]
    b = [
        _packet(
            "B_openweight",
            "factorial",
            "high",
            i,
            pass_both=False,
            max_chunk_displacement=0.05,
            max_drift_deg=2.0,
        )
        for i in range(10)
    ]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert: exception path NOT taken; falls through to AGREEMENT ladder
    assert result.relation_class == RelationClass.AGREEMENT


def test_classify_packet_returns_three_keys():
    # Arrange
    obs = AuditObserver()
    p = _packet(
        "B_claude",
        "factorial",
        "high",
        0,
        pass_both=True,
        max_chunk_displacement=0.05,
        max_drift_deg=2.0,
    )

    # Act
    out = obs.classify_packet(p, l_hat=0.1)

    # Assert
    assert set(out.keys()) == {"smooth", "passed", "smooth_success_exception"}
    assert out["smooth"] is True
    assert out["passed"] is True
    assert out["smooth_success_exception"] is True


def test_classify_packet_non_high_tier_never_exception():
    # Arrange
    obs = AuditObserver()
    p = _packet("B_claude", "factorial", "low", 0, pass_both=True)

    # Act
    out = obs.classify_packet(p, l_hat=1.0)

    # Assert
    assert out["smooth_success_exception"] is False


# --------------------------------------------------------------------- #
# Edge cases for internal predicates
# --------------------------------------------------------------------- #
def test_chart_agreement_zero_l_hats_treated_as_equal():
    # Arrange
    obs = AuditObserver()

    # Act / Assert
    assert obs._chart_agreement(0.0, 0.0) is True


def test_pass_rate_empty_packets_returns_zero():
    # Arrange
    obs = AuditObserver()

    # Act / Assert
    assert obs._pass_rate([]) == 0.0
    assert obs._smooth_fraction([], 0.1) == 0.0


def test_l_hat_empty_returns_nan():
    # Arrange
    import math
    obs = AuditObserver()

    # Act
    val = obs._l_hat([])

    # Assert
    assert math.isnan(val)


def test_classify_cell_only_b_packets_fills_observer_and_task():
    # Arrange: A empty, B has packets; controls satisfy n>=10 via stream a list pad
    obs = AuditObserver()
    a: list = []
    b = [_packet("B_openweight", "fibonacci", "low", i) for i in range(10)]

    # Act
    result = obs.classify_cell(
        a, b, _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert: pass_rate_a is 0, smooth_fraction_a is 0; pass-rate gap > 0.10 with charts agreeing
    assert result.observer_pair[1] == "B_openweight"
    assert result.task == "fibonacci"
    assert result.tier == "low"
    assert result.relation_class == RelationClass.LOCAL_CONTRACT_DIVERGENCE


def test_classify_cell_both_streams_empty_uses_question_mark_defaults():
    # Arrange
    obs = AuditObserver()

    # Act
    result = obs.classify_cell(
        [], [], _control_set("B_claude"), _control_set("B_openweight")
    )

    # Assert: with no packets we expect AGREEMENT (all rates equal 0, charts equal)
    assert result.task == "?"
    assert result.tier == "?"
    assert result.relation_class == RelationClass.AGREEMENT


def test_audit_observer_constructor_overrides():
    # Arrange / Act
    obs = AuditObserver(
        displacement_multiplier=3.0,
        drift_deg_threshold=20.0,
        contract_agreement_tol=0.2,
        chart_agreement_rel_tol=0.7,
        regime_agreement_tol=0.2,
        min_calibration_n=5,
    )

    # Assert
    assert obs.displacement_multiplier == 3.0
    assert obs.drift_deg_threshold == 20.0
    assert obs.contract_agreement_tol == 0.2
    assert obs.chart_agreement_rel_tol == 0.7
    assert obs.regime_agreement_tol == 0.2
    assert obs.min_calibration_n == 5
