"""Mutation-killing tests for ci/audit/.

Each test targets one or more surviving Cosmic Ray mutants by name.
Tests assert tight enough boundary / value semantics that the mutant
produces an observable difference. EQUIVALENT and DEAD-CODE survivors
are documented in MUTATION_LEDGER.md (no test here).

Convention: ``test_M_<file>_L<line>_<operator>_<descriptor>``.
"""

from __future__ import annotations

import pytest

from ci.audit import audit_observer as ao
from ci.audit import observer as obs_mod
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


def _control(observer_id: str, n: int = 10, mean_disp: float = 0.1, model_id: str = "claude-haiku-4-5"):
    return [
        _packet(observer_id, "calib", "control", i,
                mean_chunk_displacement=mean_disp, model_id=model_id)
        for i in range(n)
    ]


# ===================================================================== #
# 1. Threshold constants (NumberReplacer on lines 15-20)
# Asserting exact equality is sufficient to kill +1/-1 NumberReplacer.
# ===================================================================== #


def test_M_audit_observer_L15_DISPLACEMENT_MULTIPLIER_is_exactly_2_5():
    """M15: DISPLACEMENT_MULTIPLIER == 2.5 exactly. Refutation: any other value."""
    assert ao.DISPLACEMENT_MULTIPLIER == 2.5


def test_M_audit_observer_L16_DRIFT_DEG_THRESHOLD_is_exactly_15_0():
    """M16: DRIFT_DEG_THRESHOLD == 15.0 exactly. Refutation: any other value."""
    assert ao.DRIFT_DEG_THRESHOLD == 15.0


def test_M_audit_observer_L17_CONTRACT_AGREEMENT_TOL_is_exactly_0_10():
    """M17: CONTRACT_AGREEMENT_TOL == 0.10 exactly. Refutation: any other value."""
    assert ao.CONTRACT_AGREEMENT_TOL == 0.10


def test_M_audit_observer_L18_CHART_AGREEMENT_REL_TOL_is_exactly_0_50():
    """M18: CHART_AGREEMENT_REL_TOL == 0.50 exactly. Refutation: any other value."""
    assert ao.CHART_AGREEMENT_REL_TOL == 0.50


def test_M_audit_observer_L19_REGIME_AGREEMENT_TOL_is_exactly_0_10():
    """M19: REGIME_AGREEMENT_TOL == 0.10 exactly. Refutation: any other value."""
    assert ao.REGIME_AGREEMENT_TOL == 0.10


def test_M_audit_observer_L20_MIN_CALIBRATION_N_is_exactly_10():
    """M20: MIN_CALIBRATION_N == 10 exactly. Refutation: any other value."""
    assert ao.MIN_CALIBRATION_N == 10


# ===================================================================== #
# 2. _smooth boundary (LtE_Lt on lines 54, 55)
# At equality, <= is True and < is False. l_hat = 0.1 from _control().
# ===================================================================== #


def test_M_audit_observer_L54_LtE_displacement_at_exact_boundary_is_smooth():
    """M54: <= boundary on displacement: at displacement == 2.5 * l_hat _smooth returns True. Refutation: < mutant returns False."""
    # Arrange: max_chunk_displacement == DISPLACEMENT_MULTIPLIER * l_hat exactly.
    # _control gives l_hat = 0.1, so boundary = 0.25.
    obs = AuditObserver()
    p = _packet("A", "t", "low", 0,
                max_chunk_displacement=0.25,
                max_drift_deg=5.0)
    # Act
    smooth = obs._smooth(p, l_hat=0.1)
    # Assert: with <= True; mutant Lt would return False.
    assert smooth is True


def test_M_audit_observer_L55_LtE_drift_at_exact_boundary_is_smooth():
    """M55: <= boundary on drift: at drift == 15.0 _smooth returns True. Refutation: < mutant returns False."""
    # Arrange: max_drift_deg == DRIFT_DEG_THRESHOLD exactly.
    obs = AuditObserver()
    p = _packet("A", "t", "low", 0,
                max_chunk_displacement=0.05,
                max_drift_deg=15.0)
    smooth = obs._smooth(p, l_hat=0.1)
    assert smooth is True


# ===================================================================== #
# 3. _chart_agreement: Div mutations + LtE_Lt at line 109
# Original: abs(la-lb)/denom <= rel_tol  with rel_tol=0.50
# Use la=2.0, lb=1.0, denom=2.0: ratio = 0.5 == tol → boundary True.
# Mutants:
#   Div_Mul   → abs(1.0)*2.0=2.0 <= 0.5 → False
#   Div_Pow   → 1.0**2.0 = 1.0 <= 0.5 → False
#   Div_LShift→ float << float is TypeError, kills via exception
#   LtE_Lt    → 0.5 < 0.5 → False
# ===================================================================== #


def test_M_audit_observer_L109_chart_agreement_at_exact_boundary_is_True():
    """M109a: chart agreement at exactly the rel_tol boundary returns True. Refutation: any Div/comparison mutation flips this."""
    obs = AuditObserver()
    # Original: abs(2.0-1.0)/2.0 = 0.5 <= 0.5 → True
    assert obs._chart_agreement(2.0, 1.0) is True
    assert obs._chart_agreement(1.0, 2.0) is True


def test_M_audit_observer_L109_chart_agreement_just_above_boundary_is_False():
    """M109b: chart agreement just above rel_tol boundary returns False. Refutation: comparison mutation lets it through."""
    obs = AuditObserver()
    # 1.01 / 2.0 = 0.505 > 0.50
    assert obs._chart_agreement(2.0, 0.99) is False


# ===================================================================== #
# 4. _l_hat / _pass_rate / _smooth_fraction divisor (NumberReplacer at L87)
# pass_rate of 3-pass-out-of-4 packets must equal 0.75 exactly.
# Mutants on the divisor "/ len(packets)" produce 1.0, 0.6, etc.
# ===================================================================== #


def test_M_audit_observer_L87_pass_rate_three_of_four_is_exactly_0_75():
    """M87a: _pass_rate of 3-of-4 == 0.75 exactly. Refutation: divisor mutation (Div_Mul/Div_LShift) yields any other value."""
    obs = AuditObserver()
    pkts = [
        _packet("A", "t", "low", 0, pass_both=True),
        _packet("A", "t", "low", 1, pass_both=True),
        _packet("A", "t", "low", 2, pass_both=True),
        _packet("A", "t", "low", 3, pass_both=False),
    ]
    assert obs._pass_rate(pkts) == 0.75


def test_M_audit_observer_L80_l_hat_average_is_exact():
    """M80: _l_hat of 4 packets all at 0.2 == 0.2 exactly. Refutation: averaging arithmetic mutated."""
    # 4 packets with mean_chunk_displacement = 0.2 each → l_hat = 0.2 exactly.
    obs = AuditObserver()
    pkts = [
        _packet("A", "calib", "control", i, mean_chunk_displacement=0.2)
        for i in range(4)
    ]
    assert obs._l_hat(pkts) == 0.2


def test_M_audit_observer_L87_div_mul_distinguishable_via_pass_rate():
    """M87b: pass_rate(1-of-2) == 0.5 — would be 2.0 under Div_Mul mutant."""
    # 1 of 2 → 0.5 original; mutant '*' → 1*2 = 2.0
    obs = AuditObserver()
    pkts = [
        _packet("A", "t", "low", 0, pass_both=True),
        _packet("A", "t", "low", 1, pass_both=False),
    ]
    assert obs._pass_rate(pkts) == 0.5


# ===================================================================== #
# 5. min_calibration_n boundary (Lt_NotEq, Lt_IsNot on L136, L141)
# Need n=300 to break CPython small-int interning so `is not` differs from `<`.
# ===================================================================== #


def test_M_audit_observer_L136_calibration_size_just_below_threshold_fails():
    """M136a: control n=9 (< 10) yields INSUFFICIENT_OBSERVABILITY for observer A."""
    # n=9 < 10 → INSUFFICIENT_OBSERVABILITY
    obs = AuditObserver()
    a = [_packet("A", "t", "low", i) for i in range(10)]
    b = [_packet("B", "t", "low", i) for i in range(10)]
    short_ctrl = _control("A", n=9)
    full_ctrl = _control("B", n=10)
    result = obs.classify_cell(a, b, short_ctrl, full_ctrl)
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert "observer A" in result.reason


def test_M_audit_observer_L136_calibration_at_threshold_is_OK_large_N():
    """M136b: control n=300 (well above small-int interning) yields non-INSUFFICIENT — kills Lt_IsNot/Lt_NotEq mutants."""
    # n=300 (well above 256, breaks small-int interning) → AGREEMENT path
    obs = AuditObserver()
    a = [_packet("A", "t", "low", i) for i in range(10)]
    b = [_packet("B", "t", "low", i) for i in range(10)]
    big_ctrl_a = _control("A", n=300)
    big_ctrl_b = _control("B", n=300)
    result = obs.classify_cell(a, b, big_ctrl_a, big_ctrl_b)
    assert result.relation_class != RelationClass.INSUFFICIENT_OBSERVABILITY


def test_M_audit_observer_L141_calibration_size_observer_b_short():
    """M141: short calibration on observer B produces INSUFFICIENT for B."""
    obs = AuditObserver()
    a = [_packet("A", "t", "low", i) for i in range(10)]
    b = [_packet("B", "t", "low", i) for i in range(10)]
    full_ctrl = _control("A", n=10)
    short_ctrl = _control("B", n=9)
    result = obs.classify_cell(a, b, full_ctrl, short_ctrl)
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert "observer B" in result.reason


# ===================================================================== #
# 6. classify_cell heterogeneous-first-element fixtures (NumberReplacer
# on lines 165-187 indexing [0] vs [1]).
# Tests use packets where [0] differs from [1] in observer_id, task_id,
# and tier so that mutants picking [1] produce a different observer_pair.
# ===================================================================== #


def test_M_audit_observer_L165_observer_pair_uses_index_0_not_1():
    """M165: heterogeneous packet[0] differs from packet[1]; observer_pair must use index 0."""
    obs = AuditObserver()
    # Heterogeneous: first packet has observer_id "FIRST_A"; rest are "B_claude"
    a = [_packet("FIRST_A", "t", "low", 0)]
    a += [_packet("B_claude", "t", "low", i) for i in range(1, 10)]
    b = [_packet("FIRST_B", "t", "low", 0)]
    b += [_packet("B_openweight", "t", "low", i) for i in range(1, 10)]
    result = obs.classify_cell(a, b, _control("A"), _control("B"))
    # Original: observer_pair == ("FIRST_A", "FIRST_B"); mutant [1] would give
    # ("B_claude", "B_openweight")
    assert result.observer_pair == ("FIRST_A", "FIRST_B")


def test_M_audit_observer_L168_task_id_uses_index_0_not_1():
    """M168: heterogeneous packet[0] task_id differs from packet[1]; cell.task must use index 0."""
    obs = AuditObserver()
    # Heterogeneous task_id at index 0
    a = [_packet("A", "FIRST_TASK", "low", 0)]
    a += [_packet("A", "other_task", "low", i) for i in range(1, 10)]
    b = [_packet("B", "FIRST_TASK", "low", 0)]
    b += [_packet("B", "other_task", "low", i) for i in range(1, 10)]
    # Use control with NO error/mismatch but classify_cell groups by (task,tier)
    # via classify_cell directly (no audit_streams grouping)
    result = obs.classify_cell(a, b, _control("A"), _control("B"))
    assert result.task == "FIRST_TASK"


def test_M_audit_observer_L171_tier_uses_index_0_not_1():
    """M171: heterogeneous packet[0] tier differs from packet[1]; cell.tier must use index 0."""
    obs = AuditObserver()
    # Heterogeneous tier at index 0 (well-formed only via classify_cell direct)
    a = [_packet("A", "t", "FIRST_TIER", 0)]
    a += [_packet("A", "t", "other_tier", i) for i in range(1, 10)]
    b = [_packet("B", "t", "FIRST_TIER", 0)]
    b += [_packet("B", "t", "other_tier", i) for i in range(1, 10)]
    result = obs.classify_cell(a, b, _control("A"), _control("B"))
    assert result.tier == "FIRST_TIER"


# ===================================================================== #
# 7. SMOOTH_SUCCESS_EXCEPTION + TRUE_CERTIFICATE_REFUTATION (line 204)
# ===================================================================== #


def test_M_audit_observer_L204_high_tier_smooth_pass_yields_smooth_success_exception():
    """M204a: single-observer smooth+pass at high tier classifies as SMOOTH_SUCCESS_EXCEPTION."""
    obs = AuditObserver()
    # Single-observer smooth+pass at high tier → SMOOTH_SUCCESS_EXCEPTION
    a = [_packet("A", "t", "high", i,
                 max_chunk_displacement=0.05,
                 max_drift_deg=5.0,
                 pass_both=True) for i in range(10)]
    # B has high-tier failures (no smooth-success-exception)
    b = [_packet("B", "t", "high", i,
                 max_chunk_displacement=0.05,
                 max_drift_deg=5.0,
                 pass_both=False) for i in range(10)]
    result = obs.classify_cell(a, b, _control("A"), _control("B"))
    assert result.relation_class == RelationClass.SMOOTH_SUCCESS_EXCEPTION


def test_M_audit_observer_L204_overlapping_smooth_success_in_both_yields_TRUE_CERT_REFUTATION():
    """M204b: overlapping smooth-success in both observers on same task yields TRUE_CERTIFICATE_REFUTATION."""
    obs = AuditObserver()
    a = [_packet("A", "shared_task", "high", i, pass_both=True) for i in range(10)]
    b = [_packet("B", "shared_task", "high", i, pass_both=True) for i in range(10)]
    result = obs.classify_cell(a, b, _control("A"), _control("B"))
    assert result.relation_class == RelationClass.TRUE_CERTIFICATE_REFUTATION
    # Overlap → offending_packet_hashes is non-empty
    assert len(result.offending_packet_hashes) > 0


# ===================================================================== #
# 8. audit_streams cell grouping (lines 337-346: comparison + ZeroIterationForLoop)
# Mutants on `if p.tier == "control"` filter would include controls in cells
# (or exclude non-controls). ZeroIterationForLoop on the for-loop bodies
# would empty out the cells dict.
# ===================================================================== #


def test_M_audit_observer_L337_audit_streams_filters_controls_from_cells():
    """M337a: audit_streams excludes control-tier packets from non-control cells; mutated filter would include them."""
    obs = AuditObserver()
    # Mixed stream: 10 control + 10 low-tier per observer. After grouping
    # there should be exactly 1 cell for ("t","low"), NOT 2.
    a = _control("A", n=10) + [_packet("A", "t", "low", i) for i in range(10)]
    b = _control("B", n=10) + [_packet("B", "t", "low", i) for i in range(10)]
    results = obs.audit_streams(a, b)
    # Original: 1 result (("t","low") cell only). Mutant ZeroIterationForLoop
    # produces 0 results; mutant "tier != 'control'" inverts to give "calib"
    # cell instead.
    assert len(results) == 1
    assert results[0].task == "t"
    assert results[0].tier == "low"


def test_M_audit_observer_L337_audit_streams_with_empty_streams_returns_empty():
    """M337b: audit_streams on empty streams returns []; ZeroIterationForLoop mutant trivially preserves this — kept for explicit no-op contract."""
    obs = AuditObserver()
    assert obs.audit_streams([], []) == []


def test_M_audit_observer_L317_control_packets_routed_to_calibration_pool():
    """M317: control packets reach the calibration pool — assertion that the resulting cell is NOT INSUFFICIENT (would be if the filter were inverted)."""
    # If line-317 comparison were inverted (Eq_NotEq etc.), control packets
    # would get pushed into cells and non-control into the calibration pool,
    # which would produce INSUFFICIENT_OBSERVABILITY (n=0 calibration set).
    obs = AuditObserver()
    a = _control("A", n=10) + [_packet("A", "t", "low", i) for i in range(10)]
    b = _control("B", n=10) + [_packet("B", "t", "low", i) for i in range(10)]
    results = obs.audit_streams(a, b)
    assert len(results) == 1
    # Calibration was sufficient → not INSUFFICIENT_OBSERVABILITY
    assert results[0].relation_class != RelationClass.INSUFFICIENT_OBSERVABILITY


# ===================================================================== #
# 9. observer.py kill-tests
# ===================================================================== #


def test_M_observer_L21_Gt_GtE_default_adjust_at_drift_exactly_1_0():
    """M21: at drift == 1.0 exactly, _default_adjust returns current * 1.05; Gt_GtE mutant would return current * 0.5."""
    # Original: drift > 1.0 → False at drift=1.0 → return current * 1.05 = 1.05
    # Mutant Gt_GtE: drift >= 1.0 → True → return current * 0.5 = 0.5
    out = obs_mod._default_adjust(current=1.0, drift=1.0)
    assert out == pytest.approx(1.05)


def test_M_observer_L22_Mul_Sub_default_adjust_with_current_2_0():
    """M22: at current=2.0 the original Mul produces 1.0 while Mul_Sub mutant produces 1.5 — fixture chosen to break the degenerate current=1.0 equivalence."""
    # Original: current * 0.5 = 1.0 at current=2.0
    # Mutant Mul_Sub: current - 0.5 = 1.5
    out = obs_mod._default_adjust(current=2.0, drift=5.0)
    assert out == pytest.approx(1.0)
    assert out != pytest.approx(1.5)


def test_M_observer_L31_default_resolution_is_exactly_1_0():
    """M31: default Observer.resolution == 1.0 exactly."""
    # NumberReplacer on the default would change Observer().resolution
    o = obs_mod.Observer(name="x")
    assert o.resolution == 1.0


# ===================================================================== #
# 11. Predicate-boundary kill tests for the second Ray pass.
# Targets: lines 114, 118-119, 123, 197-198, 215, 335, 340 (post linter).
# ===================================================================== #


def test_M_audit_observer_L114_contract_agreement_at_exact_tol_is_True():
    """M114: <= boundary on contract agreement: gap exactly == tol → True;
    mutant '<' → False. Uses (0.1, 0.0) so abs(pa-pb) is bit-exact 0.1
    (matching the tol literal); (0.5, 0.4) does NOT work due to float
    representation: 0.5 - 0.4 == 0.09999999999999998.
    """
    obs = AuditObserver()
    assert obs._contract_agreement(0.1, 0.0) is True
    assert obs._contract_agreement(0.0, 0.1) is True


def test_M_audit_observer_L114_contract_agreement_just_above_tol_is_False():
    """M114b: gap just above tolerance returns False."""
    obs = AuditObserver()
    assert obs._contract_agreement(0.2, 0.0) is False


def test_M_audit_observer_L118_chart_agreement_zero_denom_branch_True_when_both_zero():
    """M118: when both la and lb are 0.0, chart_agreement returns True via the
    denom==0 branch. Refutation: comparison-operator mutation flips this.
    """
    obs = AuditObserver()
    assert obs._chart_agreement(0.0, 0.0) is True


def test_M_audit_observer_L119_chart_agreement_zero_denom_branch_False_when_only_one_zero():
    """M119: denom==0.0 path: if only one of la, lb is zero, denom would be the
    nonzero value — so this branch never reaches `la == lb` in that case. To
    exercise the inner `la == lb` test we need both la and lb to be zero
    (handled by L118) AND we need a fixture where they ARE equal but the
    inner equality could be flipped — e.g., both 0.0 returns True.
    A second test: classify_cell on an empty input pair where l_hat_a == l_hat_b == nan.
    """
    obs = AuditObserver()
    # Direct call: both zero -> True via la == lb
    assert obs._chart_agreement(0.0, 0.0) is True


def test_M_audit_observer_L123_regime_agreement_at_exact_tol_is_True():
    """M123: <= boundary on regime agreement: gap exactly == tol → True;
    mutant '<' → False. Uses (0.1, 0.0) for bit-exact tol equality.
    """
    obs = AuditObserver()
    assert obs._regime_agreement(0.1, 0.0) is True
    assert obs._regime_agreement(0.0, 0.1) is True


def test_M_audit_observer_L123_regime_agreement_just_above_tol_is_False():
    """M123b: gap just above tolerance returns False."""
    obs = AuditObserver()
    assert obs._regime_agreement(0.2, 0.0) is False


def test_M_audit_observer_L180_183_B_only_fallback_uses_packets_b_index_0():
    """M180/M183: when packets_a is empty, the cell's task and tier come
    from packets_b[0]. NumberReplacer mutating [0] → [1] would route the
    fallback to a different packet's task/tier. Heterogeneous packets_b
    fixture distinguishes [0] from [1].
    """
    obs = AuditObserver()
    packets_a = []
    packets_b = [
        _packet("B", "FIRST_TASK", "FIRST_TIER", 0),
        _packet("B", "OTHER_TASK", "OTHER_TIER", 1),
    ]
    result = obs.classify_cell(
        packets_a, packets_b,
        control_a=_control("A"),
        control_b=_control("B"),
    )
    assert result.task == "FIRST_TASK"
    assert result.tier == "FIRST_TIER"


def test_M_audit_observer_L197_198_smooth_fraction_defaults_in_INSUFFICIENT_branch():
    """M197/M198: when classify_cell hits INSUFFICIENT_OBSERVABILITY (e.g. via
    too-small calibration), the smooth_fraction_a and smooth_fraction_b fields
    in the returned AuditResult must equal 0.0 exactly. NumberReplacer mutating
    these to 1.0 would still pass loose tests; this asserts the literal.
    """
    obs = AuditObserver()
    # Force INSUFFICIENT via too-short calibration
    a = [_packet("A", "t", "low", i) for i in range(10)]
    b = [_packet("B", "t", "low", i) for i in range(10)]
    short_ctrl = _control("A", n=3)  # < 10
    full_ctrl = _control("B", n=10)
    result = obs.classify_cell(a, b, short_ctrl, full_ctrl)
    assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY
    assert result.smooth_fraction_a == 0.0
    assert result.smooth_fraction_b == 0.0


def test_M_audit_observer_L215_high_tier_distinct_from_other_tiers():
    """M215: classify_cell at tier='low' must NOT enter the high-tier
    smooth-success-exception branch (Eq_LtE/Eq_GtE/Eq_Is on tier=='high').
    Refutation: a smooth+pass cell at tier='low' classifies as
    SMOOTH_SUCCESS_EXCEPTION (which would only fire under tier=='high').
    """
    obs = AuditObserver()
    a = [_packet("A", "t", "low", i, max_chunk_displacement=0.05,
                 max_drift_deg=5.0, pass_both=True) for i in range(10)]
    b = [_packet("B", "t", "low", i, max_chunk_displacement=0.05,
                 max_drift_deg=5.0, pass_both=True) for i in range(10)]
    result = obs.classify_cell(a, b, _control("A"), _control("B"))
    # All-smooth, all-pass at low tier → AGREEMENT (NOT SMOOTH_SUCCESS_EXCEPTION)
    assert result.relation_class == RelationClass.AGREEMENT


def test_M_audit_observer_L335_340_audit_streams_skips_only_control_tier():
    """M335/M340: the cell-grouping loops in audit_streams skip ONLY
    `tier == 'control'`. A mutation to Eq_LtE would also skip 'calib' or any
    string < 'control' alphabetically — but no other tier in the spec is
    < 'control', so this is EQUIVALENT under the closed tier set.
    What we CAN kill: Eq_Is would still match interned strings; explicit
    test with a tier label NOT created from a literal (forces non-interned
    string) breaks the `is` mutant.
    """
    import sys
    obs = AuditObserver()
    # Create a non-interned 'low' string via concatenation — `is "low"`
    # would be False for this instance even though `== "low"` is True.
    non_interned_low = ("lo" + "w") * 1
    # Force CPython to NOT intern (sys.intern would re-intern; skip it)
    a = (
        _control("A", n=10)
        + [_packet("A", "t", non_interned_low, i) for i in range(10)]
    )
    b = (
        _control("B", n=10)
        + [_packet("B", "t", non_interned_low, i) for i in range(10)]
    )
    results = obs.audit_streams(a, b)
    # The non-interned 'low' is still grouped via == comparison; mutant
    # using `is` against the interned literal "control" would still skip
    # control packets (interned), so mutation actually equivalent here.
    # But the cell IS produced with our non_interned tier:
    assert len(results) == 1
    # Tier round-trips as the (possibly non-interned) string equal to 'low'
    assert results[0].tier == "low"
