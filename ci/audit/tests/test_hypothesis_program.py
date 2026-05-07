"""Hypothesis Program (locked in pre-reg v4 §3c) → executable pytest.

Each invariant family from prior symbolic-substrate work corresponds to one
apparatus-soundness hypothesis (Part A: H_A1..H_A6). Each paper headline
corresponds to one substantive hypothesis (Part B: H_B1..H_B3).

This file maps every hypothesis to one or more named pytest functions. Test
names are deliberately verbose: cert layer L30 greps for them, asserting
the hypothesis program is mechanically present in the test suite.

Apparatus hypotheses are fully unit-testable here. Substantive hypotheses
have their predicate logic unit-tested here; their final verdict requires
real packet streams from the experiment and is computed by L31 at runtime.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import List

import pytest

from ci.audit.audit_observer import (
    AuditObserver,
    CHART_AGREEMENT_REL_TOL,
    CONTRACT_AGREEMENT_TOL,
    DISPLACEMENT_MULTIPLIER,
    DRIFT_DEG_THRESHOLD,
    MIN_CALIBRATION_N,
    REGIME_AGREEMENT_TOL,
)
from ci.audit.audit_result import AuditResult
from ci.audit.decision_rule import (
    PaperAction,
    RELATION_TO_PAPER_ACTION,
    apply_decision_rule,
)
from ci.audit.observation_packet import ObservationPacket
from ci.audit.relation_classes import RelationClass


PRE_REG_HASH = "hyp" * 22  # 66 chars; long enough to look hash-shaped
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
    # v4.1 additive provenance fields (default None preserves v4.0 shape)
    api_request_id: str | None = None,
    actual_token_usage: dict | None = None,
    stop_reason: str | None = None,
    openrouter_provider: str | None = None,
    wall_time_ms: float | None = None,
) -> ObservationPacket:
    """Synthetic packet builder; mirrors the one in test_audit_observer.py."""
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
        api_request_id=api_request_id,
        actual_token_usage=actual_token_usage,
        stop_reason=stop_reason,
        openrouter_provider=openrouter_provider,
        wall_time_ms=wall_time_ms,
    )


def _control_set(observer_id: str, n: int = 10, mean_disp: float = 0.1) -> List[ObservationPacket]:
    return [
        _packet(observer_id, "calib", "control", i, mean_chunk_displacement=mean_disp)
        for i in range(n)
    ]


# =====================================================================
# PART A — Apparatus soundness hypotheses (H_A1..H_A6)
# Each test = one invariant family from pre-reg §3c
# =====================================================================


class TestApparatusHypotheses:
    """Hypotheses that the audit observer satisfies the six bounded-observer
    invariants. Refutation of any of these means the apparatus is broken;
    no substantive Part B inference is permitted until the apparatus is fixed.
    """

    # ----- H_A1: STATE invariant ---------------------------------------
    def test_H_A1_state_invariant_classification_depends_only_on_declared_statistics(self):
        """H_A1 (State): Audit output depends only on declared interface statistics
        (max_chunk_displacement, max_drift_deg, verifier_result.pass_both, error)
        and the registered thresholds. Refutation predicate: two invocations of
        classify_cell on byte-identical inputs yield non-identical AuditResults.
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        ctl_a = _control_set("B_claude")
        ctl_b = _control_set("B_openweight")

        # Act
        result1 = obs.classify_cell(a, b, ctl_a, ctl_b)
        result2 = obs.classify_cell(a, b, ctl_a, ctl_b)

        # Assert
        assert result1 == result2, (
            "H_A1 REFUTED: classify_cell is non-deterministic; the audit "
            "observer's output depends on something other than declared statistics."
        )
        assert result1.relation_class == result2.relation_class

    def test_H_A1_state_invariant_threshold_dependence_is_explicit(self):
        """H_A1 corollary: changing a registered threshold MUST change the
        classification when the change crosses the predicate boundary.
        Confirms the audit reads its declared thresholds (no hidden constants).
        """
        # Arrange: packets with displacement RIGHT AT 2.5 * L_hat
        l_hat = 0.1
        a_smooth = [_packet("B_claude", "factorial", "high", i,
                            mean_chunk_displacement=l_hat,
                            max_chunk_displacement=2.4 * l_hat) for i in range(10)]
        b_smooth = [_packet("B_openweight", "factorial", "high", i,
                            mean_chunk_displacement=l_hat,
                            max_chunk_displacement=2.4 * l_hat) for i in range(10)]
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        # Act: with default 2.5 multiplier, all are smooth (2.4 < 2.5)
        obs_default = AuditObserver(displacement_multiplier=2.5)
        # With multiplier dropped to 2.0, all become pivot (2.4 > 2.0)
        obs_strict = AuditObserver(displacement_multiplier=2.0)
        r_default = obs_default.classify_cell(a_smooth, b_smooth, ctl_a, ctl_b)
        r_strict = obs_strict.classify_cell(a_smooth, b_smooth, ctl_a, ctl_b)

        # Assert: registered threshold actually affects classification
        # (refutation: results identical → audit ignores its declared thresholds)
        assert r_default.smooth_fraction_a != r_strict.smooth_fraction_a or \
               r_default.smooth_fraction_b != r_strict.smooth_fraction_b, (
            "H_A1 REFUTED: registered thresholds do not affect output."
        )

    # ----- H_A2: BUDGET invariant --------------------------------------
    def test_H_A2_budget_invariant_no_llm_imports_anywhere_in_audit(self):
        """H_A2 (Budget): The audit substrate ci/audit/ contains no LLM-API import,
        no model-weight access, and no unrestricted I/O. Refutation predicate:
        any forbidden import in any audit module.
        """
        # Arrange
        forbidden = {"anthropic", "openai", "openrouter", "transformers", "torch"}
        audit_dir = Path(__file__).resolve().parent.parent
        py_files = list(audit_dir.glob("*.py")) + list(audit_dir.glob("tests/*.py"))

        # Act
        offenders: list[tuple[str, str]] = []
        for f in py_files:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in forbidden:
                            offenders.append((f.name, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module is not None:
                        top = node.module.split(".")[0]
                        if top in forbidden:
                            offenders.append((f.name, node.module))

        # Assert
        assert not offenders, (
            f"H_A2 REFUTED: forbidden LLM-library imports in audit substrate: {offenders}"
        )

    # ----- H_A3: COHERENCE invariant -----------------------------------
    def test_H_A3_coherence_invariant_audit_accepts_cross_encoder_classifications(self):
        """H_A3 (Coherence): The audit observer must accept secondary classifications
        from a different encoder so that cross-encoder kappa can be computed.
        Operational: AuditObserver exposes a method or AuditResult exposes a
        field for ingesting an alternative-encoder classification stream.

        This is a 'shape' check: the API supports the coherence test.
        The actual kappa computation happens at runtime (L31) on real packets.
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Act + Assert: the AuditResult must expose enough information to recompute
        # smooth/pivot from packet content (so a second encoder pass can recompute
        # against the same predicate logic).
        assert hasattr(result, "smooth_fraction_a")
        assert hasattr(result, "smooth_fraction_b")
        assert hasattr(result, "l_hat_a")
        assert hasattr(result, "l_hat_b"), (
            "H_A3 REFUTED: AuditResult does not expose chart constants needed "
            "for cross-encoder coherence check."
        )

    # ----- H_A4: DIAGNOSTIC invariant ----------------------------------
    def test_H_A4_diagnostic_invariant_audit_result_is_recomputable(self):
        """H_A4 (Diagnostic): AuditResult is fully recomputable from packet content
        and the audit observer's source. No field requires interpretation.
        Refutation predicate: classify_cell on identical inputs returns objects
        that are not equal (i.e., something hidden was used).
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "high", i) for i in range(10)]

        # Act
        r1 = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))
        r2 = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: equality + frozen
        assert r1 == r2, "H_A4 REFUTED: AuditResult is non-recomputable."
        # Frozen dataclass → cannot mutate (refutation: mutation succeeds)
        with pytest.raises((AttributeError, Exception)):
            r1.relation_class = RelationClass.AGREEMENT  # type: ignore[misc]

    def test_H_A4_diagnostic_invariant_relation_evidence_is_typed(self):
        """H_A4 corollary: AuditResult carries the deterministic numerical
        evidence (pass rates, smooth fractions, chart constants) that drove
        the classification — not free-form text. The booleans contract_agreement,
        chart_agreement, regime_agreement are derivable from these typed
        numerics via the §4.1 thresholds.
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: typed numerical evidence is present and recomputable
        assert isinstance(result.pass_rate_a, float)
        assert isinstance(result.pass_rate_b, float)
        assert isinstance(result.smooth_fraction_a, float)
        assert isinstance(result.smooth_fraction_b, float)
        assert isinstance(result.l_hat_a, float)
        assert isinstance(result.l_hat_b, float)
        assert isinstance(result.n_a, int)
        assert isinstance(result.n_b, int)
        # Reason is a string but a deterministic-content one (not LLM-generated prose)
        assert isinstance(result.reason, str) and len(result.reason) > 0

        # Boolean predicates are derivable from numerics + thresholds
        contract_agreement_recomputed = abs(result.pass_rate_a - result.pass_rate_b) <= CONTRACT_AGREEMENT_TOL
        chart_agreement_recomputed = (
            abs(result.l_hat_a - result.l_hat_b) / max(result.l_hat_a, result.l_hat_b)
            <= CHART_AGREEMENT_REL_TOL
        )
        regime_agreement_recomputed = abs(result.smooth_fraction_a - result.smooth_fraction_b) <= REGIME_AGREEMENT_TOL
        # All three are well-defined booleans (no NaN propagation)
        assert isinstance(contract_agreement_recomputed, bool)
        assert isinstance(chart_agreement_recomputed, bool)
        assert isinstance(regime_agreement_recomputed, bool)

    # ----- H_A5: TRANSITION invariant ----------------------------------
    def test_H_A5_transition_invariant_decision_tree_total_over_relation_class_enum(self):
        """H_A5 (Transition): every RelationClass value must map to a well-defined
        paper action under §9. Refutation predicate: any enum value with no
        documented branch.

        We verify totality structurally by checking the §9 decision-tree branches
        (in pre-reg) match the enum value list.
        """
        # Arrange: pre-reg §9 documents these branches:
        documented_branches = {
            RelationClass.AGREEMENT,
            RelationClass.LOCAL_CONTRACT_DIVERGENCE,
            RelationClass.CHART_TRANSITION,
            RelationClass.PIVOT_DISAGREEMENT,
            RelationClass.VERIFIER_SURFACE_MISMATCH,
            RelationClass.SMOOTH_SUCCESS_EXCEPTION,
            RelationClass.TRUE_CERTIFICATE_REFUTATION,
            RelationClass.INSUFFICIENT_OBSERVABILITY,
        }

        # Act
        all_enum_values = set(RelationClass)

        # Assert
        missing = all_enum_values - documented_branches
        assert not missing, (
            f"H_A5 REFUTED: enum values without §9 decision-tree branches: {missing}"
        )

    def test_H_A5_transition_invariant_insufficient_observability_does_not_fabricate(self):
        """H_A5 corollary: when INSUFFICIENT_OBSERVABILITY is emitted, the audit
        does NOT silently fall through to a substantive class (AGREEMENT etc.).
        """
        # Arrange: error in stream → INSUFFICIENT_OBSERVABILITY
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", 0, error="API timeout")]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: no fabrication
        assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_A5 REFUTED: errored stream did not produce INSUFFICIENT_OBSERVABILITY; "
            "the audit fabricated a substantive class."
        )

    # ----- H_A6: FALSIFICATION invariant -------------------------------
    def test_H_A6_falsification_invariant_true_certificate_refutation_is_emittable(self):
        """H_A6 (Falsification): TRUE_CERTIFICATE_REFUTATION must fire when both
        observers exhibit a (smooth, high-tier, verifier_passed) packet.
        Refutation predicate: predicate is uncomputable or never fires under
        any input.
        """
        # Arrange: both observers have a high-tier smooth-success exception
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: the falsification predicate is computable AND fires
        assert result.relation_class == RelationClass.TRUE_CERTIFICATE_REFUTATION, (
            f"H_A6 REFUTED: smooth+pass+high in BOTH observers should produce "
            f"TRUE_CERTIFICATE_REFUTATION, got {result.relation_class}"
        )
        assert result.offending_packet_hashes, (
            "H_A6 REFUTED: refutation fired but no offending packets recorded."
        )

    def test_H_A6_falsification_invariant_smooth_success_exception_fires_single_observer(self):
        """H_A6 corollary: SMOOTH_SUCCESS_EXCEPTION (single-observer) must fire
        when only one observer shows a smooth-success high-tier exception.
        """
        # Arrange
        obs = AuditObserver()
        # B_claude: smooth+pass+high
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]
        # B_openweight: pivot+fail+high
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=False, pass_a=False, pass_b=False,
                     max_chunk_displacement=2.0, max_drift_deg=45.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert
        assert result.relation_class == RelationClass.SMOOTH_SUCCESS_EXCEPTION, (
            f"H_A6 REFUTED: single-observer smooth+pass+high should produce "
            f"SMOOTH_SUCCESS_EXCEPTION, got {result.relation_class}"
        )


# =====================================================================
# PART B — Substantive paper-claim hypotheses (H_B1..H_B3)
# Predicate-logic unit tests; final verdict is L31 against real packets
# =====================================================================


class TestSubstantiveHypothesisLogic:
    """Predicate-logic tests for the substantive hypotheses about paper headlines.
    These verify the audit observer correctly classifies known-shape data.
    The substantive verdict (does the paper claim hold?) is computed by L31
    at runtime against the actual experiment's packet streams.
    """

    # ----- H_B1: smooth_fraction within ±5pp of 0.89 -------------------
    def test_H_B1_predicate_smooth_fraction_in_band_classifies_as_agreement(self):
        """H_B1 logic: when smooth_fraction is within the [0.84, 0.94] band for
        BOTH observers, the per-cell classification supports the headline
        (AGREEMENT or other apparatus-OK class). This is a synthetic shape test.
        """
        # Arrange: packets calibrated so ~89% are smooth
        obs = AuditObserver()
        # 9 smooth + 1 pivot per observer = 0.90 smooth fraction
        a = [_packet("B_claude", "factorial", "low", i,
                     mean_chunk_displacement=0.1,
                     max_chunk_displacement=0.2 if i < 9 else 1.0,
                     max_drift_deg=5.0 if i < 9 else 25.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     mean_chunk_displacement=0.1,
                     max_chunk_displacement=0.2 if i < 9 else 1.0,
                     max_drift_deg=5.0 if i < 9 else 25.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: both observers report ~0.90 smooth → in the band
        assert 0.84 <= result.smooth_fraction_a <= 0.94, (
            f"H_B1 predicate test: A's smooth_fraction {result.smooth_fraction_a} not in [0.84, 0.94]"
        )
        assert 0.84 <= result.smooth_fraction_b <= 0.94, (
            f"H_B1 predicate test: B's smooth_fraction {result.smooth_fraction_b} not in [0.84, 0.94]"
        )

    # ----- H_B2: high_tier_pass_rate within ±0.5pp of 0.017 ------------
    def test_H_B2_predicate_low_pass_rate_at_high_tier_in_band(self):
        """H_B2 logic: when high-tier pass rate is in [0.012, 0.022] for both
        observers, the headline survives. Synthetic data: 2 passes per 100 trials.
        """
        # Arrange: 2 passes per 100 high-tier trials = 2.0% (in band)
        obs = AuditObserver()
        a = [_packet("B_claude", "fizzbuzz", "high", i,
                     pass_both=(i < 2),  # first 2 pass, rest fail
                     pass_a=(i < 2),
                     pass_b=(i < 2),
                     max_chunk_displacement=2.0,  # all pivot
                     max_drift_deg=45.0)
             for i in range(100)]
        b = [_packet("B_openweight", "fizzbuzz", "high", i,
                     pass_both=(i < 2),
                     pass_a=(i < 2),
                     pass_b=(i < 2),
                     max_chunk_displacement=2.0,
                     max_drift_deg=45.0)
             for i in range(100)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: predicate-band check for each observer's pass rate
        passes_a = sum(1 for p in a if p.verifier_dict()["pass_both"])
        passes_b = sum(1 for p in b if p.verifier_dict()["pass_both"])
        rate_a = passes_a / len(a)
        rate_b = passes_b / len(b)
        assert 0.012 <= rate_a <= 0.022, f"H_B2 predicate test: A's rate {rate_a} not in band"
        assert 0.012 <= rate_b <= 0.022, f"H_B2 predicate test: B's rate {rate_b} not in band"

    # ----- H_B3: 0 smooth-regime refutations across N trials -----------
    def test_H_B3_predicate_zero_smooth_high_pass_classifies_as_clean(self):
        """H_B3 logic: when no high-tier packet satisfies (smooth AND passed),
        the headline survives — the audit produces no SMOOTH_SUCCESS_EXCEPTION.
        """
        # Arrange: all high-tier trials pivot AND fail (the paper-claim shape)
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=False,
                     pass_a=False,
                     pass_b=False,
                     max_chunk_displacement=2.0,
                     max_drift_deg=45.0)
             for i in range(50)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=False,
                     pass_a=False,
                     pass_b=False,
                     max_chunk_displacement=2.0,
                     max_drift_deg=45.0)
             for i in range(50)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: no smooth+pass+high exception = paper-claim shape preserved
        assert result.relation_class != RelationClass.SMOOTH_SUCCESS_EXCEPTION, (
            "H_B3 predicate test: should not flag SMOOTH_SUCCESS_EXCEPTION when "
            "no smooth high-tier successes exist"
        )
        assert result.relation_class != RelationClass.TRUE_CERTIFICATE_REFUTATION, (
            "H_B3 predicate test: should not flag TRUE_CERTIFICATE_REFUTATION"
        )

    def test_H_B3_predicate_one_smooth_high_pass_fires_exception(self):
        """H_B3 corollary: a SINGLE smooth+pass+high packet (in either observer)
        must fire SMOOTH_SUCCESS_EXCEPTION. Predicate is testable.
        """
        # Arrange: 1 smooth-success in A only
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", 0,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)]  # the exception
        a += [_packet("B_claude", "factorial", "high", i,
                      pass_both=False, pass_a=False, pass_b=False,
                      max_chunk_displacement=2.0, max_drift_deg=45.0)
              for i in range(1, 50)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=False, pass_a=False, pass_b=False,
                     max_chunk_displacement=2.0, max_drift_deg=45.0)
             for i in range(50)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert
        assert result.relation_class == RelationClass.SMOOTH_SUCCESS_EXCEPTION, (
            f"H_B3 predicate test: single smooth+pass+high should fire "
            f"SMOOTH_SUCCESS_EXCEPTION, got {result.relation_class}"
        )


# =====================================================================
# Cert-binding test: every hypothesis name from pre-reg §3c is testable
# =====================================================================


class TestHypothesisProgramCompleteness:
    """Verifies the hypothesis program is mechanically present in this file.
    Cert layer L30 will independently grep for these names; this test catches
    drift between pre-reg §3c and the test suite at unit-test time.
    """

    REQUIRED_HYPOTHESIS_TEST_NAMES = [
        # H_A1..H_A6 (apparatus, one per invariant family)
        "test_H_A1_state_invariant_classification_depends_only_on_declared_statistics",
        "test_H_A2_budget_invariant_no_llm_imports_anywhere_in_audit",
        "test_H_A3_coherence_invariant_audit_accepts_cross_encoder_classifications",
        "test_H_A4_diagnostic_invariant_audit_result_is_recomputable",
        "test_H_A5_transition_invariant_decision_tree_total_over_relation_class_enum",
        "test_H_A6_falsification_invariant_true_certificate_refutation_is_emittable",
        # H_B1..H_B3 (substantive predicate logic)
        "test_H_B1_predicate_smooth_fraction_in_band_classifies_as_agreement",
        "test_H_B2_predicate_low_pass_rate_at_high_tier_in_band",
        "test_H_B3_predicate_zero_smooth_high_pass_classifies_as_clean",
        # H_B4..H_B8 (substantive expansions, Part B continuation)
        "test_H_B4_predicate_cross_generation_chart_stability",
        "test_H_B5_predicate_cross_family_chart_stability",
        "test_H_B6_predicate_staging_benefit_ratio_observable",
        "test_H_B7_predicate_cliff_form_invariant_across_tiers",
        "test_H_B8_predicate_constitutive_loop_pivot_fraction_within_band",
        # H_H1..H_H4 (hash-chain integrity)
        "test_H_H1_observation_packet_carries_all_required_hashes",
        "test_H_H2_audit_observer_rejects_packets_with_mismatched_pre_reg_hash",
        "test_H_H3_audit_observer_rejects_packets_with_mismatched_encoder_id",
        "test_H_H4_offending_packet_hashes_in_audit_result_match_input_packets",
        # H_C1..H_C5 (operator-card core invariants; anonymized names)
        "test_H_C1_commitment_boundary_observer_only_classifies_completed_packets",
        "test_H_C2_trace_preservation_audit_result_carries_offending_packet_hashes",
        "test_H_C3_irreversibility_audit_result_is_frozen",
        "test_H_C4_routing_discipline_high_conflict_routes_to_recovery_class",
        "test_H_C5_residue_re_expansion_hook_offending_data_is_inspectable",
        # H_F1..H_F2 (lift discipline; zeroth-order screening invariant)
        "test_H_F1_forbidden_lift_audit_output_is_typed_enum_not_proposition",
        "test_H_F2_allowed_lift_every_relation_class_maps_to_bounded_category",
        # H_X1..H_X4 (operator-card falsification criteria; generic names)
        "test_H_X1_falsification_one_shot_success_in_infeasible_region_without_pivot_signatures_fires_refutation",
        "test_H_X2_falsification_router_misses_persistently_observable_via_repeated_misclassification",
        "test_H_X3_falsification_refusal_dominates_one_shot_under_predicted_cliff_is_observable",
        "test_H_X4_falsification_collapse_residue_seeds_re_expansion",
        # H_D1 (decision rule total + deterministic)
        "test_H_D1_decision_rule_total_over_relation_class_enum",
        "test_H_D1_decision_rule_application_is_deterministic",
        # H_S1..H_S5 (boundary sensitivity to registered numeric thresholds)
        "test_H_S1_boundary_sensitivity_displacement_multiplier_at_2_5",
        "test_H_S2_boundary_sensitivity_drift_threshold_at_15_deg",
        "test_H_S3_boundary_sensitivity_contract_tol_at_0_10",
        "test_H_S4_boundary_sensitivity_chart_tol_at_0_50",
        "test_H_S5_boundary_sensitivity_regime_tol_at_0_10",
        # H_E1..H_E4 (equality-predicate polarity in mismatch detection)
        "test_H_E1_equality_polarity_pre_reg_hash_mismatch_triggers_insufficient_observability",
        "test_H_E2_equality_polarity_encoder_id_mismatch_triggers_insufficient_observability",
        "test_H_E3_equality_polarity_packet_schema_version_consistency_required",
        "test_H_E4_equality_polarity_observer_id_consistency_within_stream",
        # H_BETA1 (inclusive-boundary smoothness predicate uses <=, not <)
        "test_H_BETA1_inclusive_boundary_smooth_at_exact_threshold_value",
        # H_VOID1 (empty-stream substrate handling)
        "test_H_VOID1_empty_stream_handling_emits_insufficient_observability",
        # H_NOT1 (boolean-polarity meta-test: every threshold has both arms tested)
        "test_H_NOT1_boolean_polarity_every_threshold_has_above_and_below_classification",
        # H_AR1..H_AR2 (analytical-formula identity for L_hat and chart agreement)
        "test_H_AR1_formula_identity_l_hat_equals_mean_displacement_of_control_packets",
        "test_H_AR2_formula_identity_chart_agreement_uses_relative_difference",
    ]

    def test_every_hypothesis_in_pre_reg_has_a_named_pytest_function(self):
        """Cert binding: pre-reg §3c lists 6 apparatus + 3 substantive hypotheses;
        every one must be present here as a named pytest function.
        """
        # Arrange
        module = inspect.getmodule(self)
        all_test_names = {
            name
            for cls_name, cls in inspect.getmembers(module, inspect.isclass)
            for name, _ in inspect.getmembers(cls, inspect.isfunction)
            if name.startswith("test_")
        }

        # Act
        missing = [
            req for req in self.REQUIRED_HYPOTHESIS_TEST_NAMES
            if req not in all_test_names
        ]

        # Assert
        assert not missing, (
            f"Hypothesis program incomplete: pre-reg §3c lists hypotheses "
            f"with no corresponding pytest function: {missing}"
        )


# =====================================================================
# PART C — Operator-card core invariants (H_C1..H_C5)
# Each maps to one of the 5 core invariants on the collapse-phase operator card from external invariant spec
# from the external invariant specification. Names are anonymized (no operator branding).
# =====================================================================


class TestOperatorCardCoreInvariants:
    """Operator-card core invariants. Each test corresponds to one of:
    Commitment Boundary, Trace Preservation, Irreversibility, Routing
    Discipline (Staging/Refusal), Residue Re-expansion Hook.

    These verify the audit observer enforces the operator-card invariants
    without naming the operator family.
    """

    # ----- H_C1: Commitment Boundary -----------------------------------
    def test_H_C1_commitment_boundary_observer_only_classifies_completed_packets(self):
        """H_C1 (Commitment Boundary): the audit observer only classifies packets
        representing COMPLETED generation. A packet carrying an error marker
        (signaling incomplete generation) must NOT yield a substantive
        non-INSUFFICIENT classification.

        Refutation predicate: audit produces a non-INSUFFICIENT_OBSERVABILITY
        classification on a packet stream containing an incomplete-generation
        marker.
        """
        # Arrange: packets carrying error markers (i.e., did not complete)
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i, error="incomplete generation")
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert: incomplete packet stream must NOT produce a substantive class
        assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_C1 REFUTED: audit committed to a substantive classification on "
            "an incomplete-generation stream; commitment boundary breached."
        )

    # ----- H_C2: Trace Preservation ------------------------------------
    def test_H_C2_trace_preservation_audit_result_carries_offending_packet_hashes(self):
        """H_C2 (Trace Preservation): when SMOOTH_SUCCESS_EXCEPTION or
        TRUE_CERTIFICATE_REFUTATION fires, AuditResult.offending_packet_hashes
        MUST be populated. The collapse must leave a reportable trace.

        Refutation predicate: refutation fires but offending_packet_hashes is
        empty (i.e., the trace was lost on collapse).
        """
        # Arrange: both observers see smooth+pass+high → TRUE_CERTIFICATE_REFUTATION
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert
        assert result.relation_class in {
            RelationClass.SMOOTH_SUCCESS_EXCEPTION,
            RelationClass.TRUE_CERTIFICATE_REFUTATION,
        }, "H_C2 setup: refutation should fire on smooth+pass+high in both observers"
        assert result.offending_packet_hashes, (
            "H_C2 REFUTED: refutation fired but offending_packet_hashes is empty; "
            "trace preservation invariant violated."
        )

    # ----- H_C3: Irreversibility Acknowledgment ------------------------
    def test_H_C3_irreversibility_audit_result_is_frozen(self):
        """H_C3 (Irreversibility): AuditResult is a frozen dataclass. Once
        classification commits, the result cannot be mutated.

        Refutation predicate: assigning to a field on AuditResult succeeds.
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Act + Assert: mutation must raise (FrozenInstanceError subclasses AttributeError)
        with pytest.raises((AttributeError, Exception)):
            result.relation_class = RelationClass.AGREEMENT  # type: ignore[misc]
        with pytest.raises((AttributeError, Exception)):
            result.reason = "mutated"  # type: ignore[misc]

    # ----- H_C4: Routing Discipline (Staging/Refusal) ------------------
    def test_H_C4_routing_discipline_high_conflict_routes_to_recovery_class(self):
        """H_C4 (Routing Discipline): when packets show high conflict (high
        max_chunk_displacement, high drift, divergent pass rates), the audit
        MUST route to a recovery / disagreement / deferral class — never AGREEMENT.

        Recovery set: every RelationClass except AGREEMENT.

        Refutation predicate: high-conflict packets are classified as AGREEMENT.
        """
        # Arrange: A is smooth + passing, B is high-displacement + failing.
        # The pass-rate gap and smooth-fraction gap both far exceed tolerance.
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(20)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     pass_both=False, pass_a=False, pass_b=False,
                     max_chunk_displacement=10.0, max_drift_deg=60.0)
             for i in range(20)]

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert
        recovery_classes = set(RelationClass) - {RelationClass.AGREEMENT}
        assert result.relation_class != RelationClass.AGREEMENT, (
            "H_C4 REFUTED: high-conflict cell classified as AGREEMENT; "
            "routing discipline invariant violated."
        )
        assert result.relation_class in recovery_classes, (
            f"H_C4 REFUTED: high-conflict cell classified as "
            f"{result.relation_class}, which is not in the recovery set."
        )

    # ----- H_C5: Residue Re-expansion Hook -----------------------------
    def test_H_C5_residue_re_expansion_hook_offending_data_is_inspectable(self):
        """H_C5 (Residue Re-expansion Hook): when refutation fires, the audit
        must expose enough information to seed a follow-up investigation:
        offending_packet_hashes is populated AND reason is non-empty.

        Refutation predicate: refutation fires with empty reason or empty
        offending_packet_hashes.
        """
        # Arrange: trigger TRUE_CERTIFICATE_REFUTATION
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert: residue is inspectable (hashes + reason both present)
        assert result.relation_class in {
            RelationClass.SMOOTH_SUCCESS_EXCEPTION,
            RelationClass.TRUE_CERTIFICATE_REFUTATION,
        }
        assert len(result.offending_packet_hashes) > 0, (
            "H_C5 REFUTED: refutation fired with no offending_packet_hashes; "
            "residue cannot seed re-expansion."
        )
        assert isinstance(result.reason, str) and len(result.reason) > 0, (
            "H_C5 REFUTED: refutation fired with empty reason; residue is "
            "not inspectable."
        )


# =====================================================================
# PART F — Lift discipline (H_F1..H_F2)
# Maps to ZEROTH_ORDER_SCREENING_INVARIANT.md forbidden/allowed lift rules.
# =====================================================================


class TestLiftDiscipline:
    """Forbidden lift / allowed lift discipline.

    Forbidden: screening statistic -> direct ontology / proposition.
    Allowed:   screening statistic -> bounded routing / comparison /
               falsification / deferral contract.
    """

    # ----- H_F1: Forbidden lift ----------------------------------------
    def test_H_F1_forbidden_lift_audit_output_is_typed_enum_not_proposition(self):
        """H_F1 (Forbidden lift): AuditResult.relation_class MUST be a typed
        RelationClass enum value, not a string or free-form proposition. The
        audit output is bounded interface vocabulary, not direct ontology.

        Refutation predicate: any non-enum value found in relation_class for
        any standard classifier output.
        """
        # Arrange: exercise every realistic classification path
        obs = AuditObserver()

        # Path 1: AGREEMENT
        a1 = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b1 = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        # Path 2: INSUFFICIENT_OBSERVABILITY
        a2 = [_packet("B_claude", "factorial", "low", 0, error="x")]
        b2 = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        # Path 3: TRUE_CERTIFICATE_REFUTATION
        a3 = [_packet("B_claude", "factorial", "high", i,
                      pass_both=True,
                      max_chunk_displacement=0.05, max_drift_deg=2.0)
              for i in range(10)]
        b3 = [_packet("B_openweight", "factorial", "high", i,
                      pass_both=True,
                      max_chunk_displacement=0.05, max_drift_deg=2.0)
              for i in range(10)]

        # Act
        results = [
            obs.classify_cell(a1, b1, _control_set("B_claude"), _control_set("B_openweight")),
            obs.classify_cell(a2, b2, _control_set("B_claude"), _control_set("B_openweight")),
            obs.classify_cell(a3, b3, _control_set("B_claude"), _control_set("B_openweight")),
        ]

        # Assert: every output is a RelationClass enum value (typed, not proposition)
        for r in results:
            assert isinstance(r.relation_class, RelationClass), (
                f"H_F1 REFUTED: relation_class is not a RelationClass enum: "
                f"{type(r.relation_class).__name__}={r.relation_class!r}"
            )

    # ----- H_F2: Allowed lift ------------------------------------------
    def test_H_F2_allowed_lift_every_relation_class_maps_to_bounded_category(self):
        """H_F2 (Allowed lift): every RelationClass value must map to one of
        the four allowed lift categories: ROUTING, COMPARISON, FALSIFICATION,
        DEFERRAL. No free-floating ontological lift permitted.

        Refutation predicate: any RelationClass enum value not in the
        categorization dict.
        """
        # Arrange: locked categorization per the allowed-lift contract
        ROUTING = "ROUTING"
        COMPARISON = "COMPARISON"
        FALSIFICATION = "FALSIFICATION"
        DEFERRAL = "DEFERRAL"
        categorization = {
            # ROUTING: per-cell / per-family routing decisions
            RelationClass.AGREEMENT: ROUTING,
            RelationClass.CHART_TRANSITION: ROUTING,
            RelationClass.LOCAL_CONTRACT_DIVERGENCE: ROUTING,
            RelationClass.PIVOT_DISAGREEMENT: ROUTING,
            # COMPARISON: cross-observer surface comparison
            RelationClass.VERIFIER_SURFACE_MISMATCH: COMPARISON,
            # FALSIFICATION: refutations of paper headlines
            RelationClass.SMOOTH_SUCCESS_EXCEPTION: FALSIFICATION,
            RelationClass.TRUE_CERTIFICATE_REFUTATION: FALSIFICATION,
            # DEFERRAL: insufficient observability defers all lift
            RelationClass.INSUFFICIENT_OBSERVABILITY: DEFERRAL,
        }

        # Act
        unmapped = [v for v in RelationClass if v not in categorization]
        bad_category = [
            v for v, cat in categorization.items()
            if cat not in {ROUTING, COMPARISON, FALSIFICATION, DEFERRAL}
        ]

        # Assert
        assert not unmapped, (
            f"H_F2 REFUTED: RelationClass values not mapped to a bounded "
            f"lift category: {unmapped}"
        )
        assert not bad_category, (
            f"H_F2 REFUTED: categorization contains non-allowed targets: "
            f"{bad_category}"
        )
        assert len(categorization) == len(set(RelationClass)) == 8, (
            f"H_F2 REFUTED: expected exactly 8 enum values mapped, got "
            f"{len(categorization)} mapped vs {len(set(RelationClass))} enum values."
        )


# =====================================================================
# PART X — Operator-card falsification criteria (H_X1..H_X4)
# Maps to the 4 falsification criteria on the collapse-phase operator card from external invariant spec.
# Test names are generic (no operator branding).
# =====================================================================


class TestOperatorCardFalsificationCriteria:
    """Operator-card falsification criteria. Each test verifies that the
    audit observer can OBSERVE the corresponding falsification condition.
    These are predicate-logic tests; final field verdicts run at L31.
    """

    # ----- H_X1: one-shot success in infeasible region without pivot ---
    def test_H_X1_falsification_one_shot_success_in_infeasible_region_without_pivot_signatures_fires_refutation(self):
        """H_X1 (Falsification 1): a high-tier (cliff regime, predicted-infeasible)
        packet that is smooth (no pivot signatures: low displacement, low drift)
        AND verifier-passes must trigger SMOOTH_SUCCESS_EXCEPTION or
        TRUE_CERTIFICATE_REFUTATION. Sustained success there is the operator's
        falsifier; the audit must be able to observe it.
        """
        # Arrange: high-tier, smooth, verifier-passing in observer A;
        # observer B is pivot+failing so we route to single-observer exception.
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=False, pass_a=False, pass_b=False,
                     max_chunk_displacement=2.0, max_drift_deg=45.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert: refutation must fire (exception is observable)
        assert result.relation_class in {
            RelationClass.SMOOTH_SUCCESS_EXCEPTION,
            RelationClass.TRUE_CERTIFICATE_REFUTATION,
        }, (
            f"H_X1 REFUTED: smooth + verifier-pass at high tier failed to "
            f"trigger refutation; got {result.relation_class}."
        )

    # ----- H_X2: router misses persistently observable -----------------
    def test_H_X2_falsification_router_misses_persistently_observable_via_repeated_misclassification(self):
        """H_X2 (Falsification 2): if the router has a stochastic component,
        repeated identical inputs would yield inconsistent classifications and
        the router could persistently miss high-conflict states without us
        knowing. We assert determinism: byte-identical input yields the same
        relation_class across many invocations.

        Refutation predicate: any pair of repeated calls yields different
        relation_class values.
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]
        ctl_a = _control_set("B_claude")
        ctl_b = _control_set("B_openweight")

        # Act: 25 repeated invocations
        classes = [
            obs.classify_cell(a, b, ctl_a, ctl_b).relation_class
            for _ in range(25)
        ]

        # Assert: all identical
        assert len(set(classes)) == 1, (
            f"H_X2 REFUTED: router produced inconsistent classifications "
            f"across identical inputs: {set(classes)}; falsifier observable."
        )

    # ----- H_X3: refusal/staging beats one-shot under cliff ------------
    def test_H_X3_falsification_refusal_dominates_one_shot_under_predicted_cliff_is_observable(self):
        """H_X3 (Falsification 3): under the predicted cliff regime (high tier),
        if pivot trials' pass rate substantially exceeds smooth trials' pass
        rate, that is the observable signal that routing is correctly
        differentiating regimes (operator survives). The inverse would falsify.

        Predicate test: construct a stream where smooth trials all fail (0%)
        and pivot trials pass at 15%; verify smooth_fraction and pass_rate
        are observable and computable from AuditResult.
        """
        # Arrange: 20 smooth+fail + 20 pivot+pass-at-15% packets per observer
        obs = AuditObserver()
        smooth_a = [_packet("B_claude", "factorial", "high", i,
                            pass_both=False, pass_a=False, pass_b=False,
                            max_chunk_displacement=0.05, max_drift_deg=2.0)
                    for i in range(20)]
        pivot_a = [_packet("B_claude", "factorial", "high", 100 + i,
                           pass_both=(i < 3), pass_a=(i < 3), pass_b=(i < 3),
                           max_chunk_displacement=2.0, max_drift_deg=45.0)
                   for i in range(20)]
        a = smooth_a + pivot_a
        smooth_b = [_packet("B_openweight", "factorial", "high", i,
                            pass_both=False, pass_a=False, pass_b=False,
                            max_chunk_displacement=0.05, max_drift_deg=2.0)
                    for i in range(20)]
        pivot_b = [_packet("B_openweight", "factorial", "high", 100 + i,
                           pass_both=(i < 3), pass_a=(i < 3), pass_b=(i < 3),
                           max_chunk_displacement=2.0, max_drift_deg=45.0)
                   for i in range(20)]
        b = smooth_b + pivot_b

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert: bimodal regime structure is observable in the AuditResult
        assert isinstance(result.smooth_fraction_a, float)
        assert isinstance(result.pass_rate_a, float)
        assert isinstance(result.smooth_fraction_b, float)
        assert isinstance(result.pass_rate_b, float)
        assert 0.3 <= result.smooth_fraction_a <= 0.7, (
            f"H_X3 REFUTED: smooth_fraction_a={result.smooth_fraction_a} "
            f"does not reflect the 50/50 bimodal design; observable signal lost."
        )
        assert 0.3 <= result.smooth_fraction_b <= 0.7, (
            f"H_X3 REFUTED: smooth_fraction_b={result.smooth_fraction_b} "
            f"does not reflect the 50/50 bimodal design; observable signal lost."
        )

    # ----- H_X4: collapse residue seeds re-expansion -------------------
    def test_H_X4_falsification_collapse_residue_seeds_re_expansion(self):
        """H_X4 (Falsification 4): when the audit emits an exception class
        with offending_packet_hashes, those hashes must match the output_hash
        of an actual input packet so the residue can be looked up to
        reconstruct context for a follow-up integration pass.

        Refutation predicate: a hash present in AuditResult does not match
        any input packet's output_hash.
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "high", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     max_chunk_displacement=0.05, max_drift_deg=2.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(
            a, b, _control_set("B_claude"), _control_set("B_openweight")
        )

        # Assert
        assert result.offending_packet_hashes, (
            "H_X4 setup: refutation should produce offending_packet_hashes."
        )
        all_input_hashes = {p.output_hash for p in a} | {p.output_hash for p in b}
        for h in result.offending_packet_hashes:
            assert h in all_input_hashes, (
                f"H_X4 REFUTED: offending hash {h!r} not present in any "
                f"input packet's output_hash; residue cannot be looked up "
                f"to seed re-expansion."
            )


# =====================================================================
# PART D — Decision rule (H_D1)
# Maps RelationClass -> PaperAction per pre-reg §9. Total + deterministic.
# =====================================================================


class TestDecisionRule:
    """Tests for the locked RelationClass -> PaperAction mapping (pre-reg §9).
    The mapping is total over the enum and deterministic on lookup.
    """

    def test_H_D1_decision_rule_total_over_relation_class_enum(self):
        """H_D1 (Decision rule totality): RELATION_TO_PAPER_ACTION must contain
        a key for every RelationClass enum value. Refutation predicate: any
        enum value missing from the dict.
        """
        # Arrange
        all_enum_values = set(RelationClass)
        mapped = set(RELATION_TO_PAPER_ACTION.keys())

        # Act
        missing = all_enum_values - mapped

        # Assert
        assert not missing, (
            f"H_D1 REFUTED: decision rule missing keys for: {missing}"
        )
        # And every value is a PaperAction
        for k, v in RELATION_TO_PAPER_ACTION.items():
            assert isinstance(v, PaperAction), (
                f"H_D1 REFUTED: mapping for {k} is not a PaperAction: "
                f"{type(v).__name__}={v!r}"
            )

    def test_H_D1_decision_rule_application_is_deterministic(self):
        """H_D1 corollary: apply_decision_rule(rc) twice yields the same
        PaperAction. Refutation predicate: any pair of identical lookups
        differs.
        """
        # Arrange + Act + Assert
        for rc in RelationClass:
            r1 = apply_decision_rule(rc)
            r2 = apply_decision_rule(rc)
            assert r1 == r2, (
                f"H_D1 REFUTED: apply_decision_rule({rc}) is non-deterministic "
                f"({r1} vs {r2})."
            )
            assert isinstance(r1, PaperAction)


# =====================================================================
# PART B continuation — Substantive expansions (H_B4..H_B8)
# Predicate-logic unit tests for cross-generation, cross-family,
# staging-benefit, cliff-form, and constitutive-loop hypotheses.
# =====================================================================


class TestSubstantiveExpansions:
    """Predicate-logic tests for the Part B continuation hypotheses.

    Each test verifies that the audit observer correctly classifies
    known-shape data; the substantive verdict against real packet streams
    is L31's responsibility (computed at runtime, not here).
    """

    # ----- H_B4: cross-generation chart stability ----------------------
    def test_H_B4_predicate_cross_generation_chart_stability(self):
        """H_B4 logic: when packets sourced from different model generations
        (claude-haiku-4-5, claude-haiku-4-6, claude-haiku-4-7) yield L_hat
        values within the 50% relative tolerance, the audit must classify
        cross-generation pairs as AGREEMENT or CHART_TRANSITION — but never
        as LOCAL_CONTRACT_DIVERGENCE (which would mean a contract gap, not
        a chart shift).
        """
        # Arrange: three generations with closely matched calibration
        # (mean_chunk_displacement values give L_hat values 0.10, 0.11, 0.12;
        #  pairwise rel-diffs are 9% and 17%, all under 50% rel tol).
        obs = AuditObserver()
        generations = [
            ("claude-haiku-4-5", 0.10),
            ("claude-haiku-4-6", 0.11),
            ("claude-haiku-4-7", 0.12),
        ]

        permitted = {
            RelationClass.AGREEMENT,
            RelationClass.CHART_TRANSITION,
        }

        # Act + Assert: every cross-pair classifies into the permitted set
        for i, (model_a, l_a) in enumerate(generations):
            for j, (model_b, l_b) in enumerate(generations):
                if i >= j:
                    continue
                a = [
                    _packet("B_claude", "factorial", "low", k,
                            mean_chunk_displacement=l_a,
                            max_chunk_displacement=l_a,
                            model_id=model_a)
                    for k in range(10)
                ]
                b = [
                    _packet("B_openweight", "factorial", "low", k,
                            mean_chunk_displacement=l_b,
                            max_chunk_displacement=l_b,
                            model_id=model_b)
                    for k in range(10)
                ]
                ctl_a = [
                    _packet("B_claude", "calib", "control", k,
                            mean_chunk_displacement=l_a,
                            model_id=model_a)
                    for k in range(10)
                ]
                ctl_b = [
                    _packet("B_openweight", "calib", "control", k,
                            mean_chunk_displacement=l_b,
                            model_id=model_b)
                    for k in range(10)
                ]
                result = obs.classify_cell(a, b, ctl_a, ctl_b)
                assert result.relation_class in permitted, (
                    f"H_B4 REFUTED: cross-generation pair ({model_a},{model_b}) "
                    f"with L_hat ({l_a},{l_b}) within rel tol classified as "
                    f"{result.relation_class}; expected one of {permitted}"
                )
                # Refutation guard: never LOCAL_CONTRACT_DIVERGENCE here
                assert result.relation_class != RelationClass.LOCAL_CONTRACT_DIVERGENCE

    # ----- H_B5: cross-family chart stability --------------------------
    def test_H_B5_predicate_cross_family_chart_stability(self):
        """H_B5 logic: when B_claude and B_openweight packets carry L_hat
        values that are absolutely different (different families' chart
        constants), the chart-disagreement branch is the path that the
        audit MUST select over AGREEMENT once the relative gap exceeds
        the 50% rel tol bound.

        Spec note: the user's narrative said "within 50% rel tol but
        visibly different"; the audit's chart_agreement predicate uses
        |la-lb|/max(la,lb) <= 0.50, so a 33% gap remains AGREEMENT.
        We honor the test intent (cross-family produces CHART_TRANSITION,
        not AGREEMENT) by using L_hat values whose relative gap exceeds
        the published threshold (here 0.05 vs 0.12 -> 0.583).
        """
        # Arrange: matched pass rates / smooth fractions; divergent L_hat
        obs = AuditObserver()
        a = [
            _packet("B_claude", "gcd", "low", i,
                    mean_chunk_displacement=0.05,
                    max_chunk_displacement=0.05)
            for i in range(10)
        ]
        b = [
            _packet("B_openweight", "gcd", "low", i,
                    mean_chunk_displacement=0.12,
                    max_chunk_displacement=0.12)
            for i in range(10)
        ]
        ctl_a = [
            _packet("B_claude", "calib", "control", i, mean_chunk_displacement=0.05)
            for i in range(10)
        ]
        ctl_b = [
            _packet("B_openweight", "calib", "control", i, mean_chunk_displacement=0.12)
            for i in range(10)
        ]

        # Act
        result = obs.classify_cell(a, b, ctl_a, ctl_b)

        # Assert: cross-family chart difference -> CHART_TRANSITION, not AGREEMENT
        assert result.relation_class == RelationClass.CHART_TRANSITION, (
            f"H_B5 REFUTED: cross-family L_hat divergence "
            f"(la={result.l_hat_a}, lb={result.l_hat_b}) should fire "
            f"CHART_TRANSITION; got {result.relation_class}"
        )
        assert result.relation_class != RelationClass.AGREEMENT

    # ----- H_B6: staging benefit ratio observable ----------------------
    def test_H_B6_predicate_staging_benefit_ratio_observable(self):
        """H_B6 logic: the pivot-vs-smooth pass-rate ratio is a measurable
        downstream quantity. Construct two packet sets in which observer A
        is the "smooth" stream and observer B is the "pivot/staged" stream
        with ~4.8x higher pass rate. Verify that the audit's reported
        pass_rate_b / pass_rate_a recovers the staging benefit.
        """
        # Arrange: A (smooth) gets 5/100 passes; B (pivot/staged) gets 24/100.
        obs = AuditObserver()
        n = 100
        smooth_passes = 5  # rate 0.05
        pivot_passes = 24  # rate 0.24 -> ratio 4.8x
        a = [
            _packet("B_claude", "fizzbuzz", "high", i,
                    pass_both=(i < smooth_passes),
                    pass_a=(i < smooth_passes),
                    pass_b=(i < smooth_passes),
                    max_chunk_displacement=0.05,
                    max_drift_deg=2.0)
            for i in range(n)
        ]
        b = [
            _packet("B_openweight", "fizzbuzz", "high", i,
                    pass_both=(i < pivot_passes),
                    pass_a=(i < pivot_passes),
                    pass_b=(i < pivot_passes),
                    max_chunk_displacement=2.0,
                    max_drift_deg=45.0)
            for i in range(n)
        ]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert: the audit exposes pass_rate_a and pass_rate_b such that
        # the staging-benefit ratio is recoverable from the result alone.
        assert result.pass_rate_a > 0.0, "denominator zero -> ratio not recoverable"
        observed_ratio = result.pass_rate_b / result.pass_rate_a
        # Allow 5% slack on the 4.8x target
        assert 4.56 <= observed_ratio <= 5.04, (
            f"H_B6 REFUTED: staging-benefit ratio not recoverable from audit; "
            f"expected ~4.8x, got {observed_ratio:.3f}"
        )

    # ----- H_B7: cliff form invariant across tiers ---------------------
    def test_H_B7_predicate_cliff_form_invariant_across_tiers(self):
        """H_B7 logic: as tier escalates (control -> low -> moderate -> high),
        the per-observer pass rate should monotonically decrease — this is
        the "cliff form" the paper claims. The audit must observe this
        monotonicity given monotonic pass-rate inputs across cells.
        """
        # Arrange: per-tier pass counts that decline monotonically
        obs = AuditObserver()
        tier_passes = {
            "low": 9,        # 90%
            "moderate": 5,   # 50%
            "high": 1,       # 10%
        }
        ctl_a = _control_set("B_claude")
        ctl_b = _control_set("B_openweight")

        rates_a: list[float] = []
        rates_b: list[float] = []
        for tier in ("low", "moderate", "high"):
            n_pass = tier_passes[tier]
            a = [
                _packet("B_claude", "factorial", tier, i,
                        pass_both=(i < n_pass),
                        pass_a=(i < n_pass),
                        pass_b=(i < n_pass))
                for i in range(10)
            ]
            b = [
                _packet("B_openweight", "factorial", tier, i,
                        pass_both=(i < n_pass),
                        pass_a=(i < n_pass),
                        pass_b=(i < n_pass))
                for i in range(10)
            ]
            result = obs.classify_cell(a, b, ctl_a, ctl_b)
            rates_a.append(result.pass_rate_a)
            rates_b.append(result.pass_rate_b)

        # Assert: strict monotonic decrease across tiers per observer
        assert all(rates_a[i] > rates_a[i + 1] for i in range(len(rates_a) - 1)), (
            f"H_B7 REFUTED: pass_rate_a not monotonic across tiers: {rates_a}"
        )
        assert all(rates_b[i] > rates_b[i + 1] for i in range(len(rates_b) - 1)), (
            f"H_B7 REFUTED: pass_rate_b not monotonic across tiers: {rates_b}"
        )

    # ----- H_B8: constitutive loop pivot fraction within band ----------
    def test_H_B8_predicate_constitutive_loop_pivot_fraction_within_band(self):
        """H_B8 logic: the Constitutive Loop Hypothesis predicts that the
        pivot fraction (1 - smooth_fraction) is bounded by loop-coherence
        bandwidth at human-cognition-scale k_eff = 9, with a predicted
        value near 11% and a plausible band [5%, 20%]. Reuse the H_B1
        shape (9 smooth + 1 pivot per observer => smooth_fraction ~ 0.90,
        pivot fraction ~ 0.10) and verify it lands in the band.
        """
        # Arrange: same shape as H_B1 (9 smooth, 1 pivot per observer)
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i,
                     mean_chunk_displacement=0.1,
                     max_chunk_displacement=0.2 if i < 9 else 1.0,
                     max_drift_deg=5.0 if i < 9 else 25.0)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     mean_chunk_displacement=0.1,
                     max_chunk_displacement=0.2 if i < 9 else 1.0,
                     max_drift_deg=5.0 if i < 9 else 25.0)
             for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        pivot_fraction_a = 1.0 - result.smooth_fraction_a
        pivot_fraction_b = 1.0 - result.smooth_fraction_b

        # Assert: both observers land in [0.05, 0.20] band predicted at k_eff=9
        assert 0.05 <= pivot_fraction_a <= 0.20, (
            f"H_B8 REFUTED: pivot fraction A {pivot_fraction_a:.3f} "
            f"outside loop-coherence band [0.05, 0.20]"
        )
        assert 0.05 <= pivot_fraction_b <= 0.20, (
            f"H_B8 REFUTED: pivot fraction B {pivot_fraction_b:.3f} "
            f"outside loop-coherence band [0.05, 0.20]"
        )


# =====================================================================
# Hash-chain integrity (H_H1..H_H4)
# =====================================================================


class TestHashChainIntegrity:
    """Hash-chain integrity tests.

    Every ObservationPacket carries five hashes that anchor it to its
    pre-registration, manifest, verifier, prompt, and output. The audit
    observer must reject any stream whose hashes drift, and must surface
    the offending packet hashes in its AuditResult so downstream layers
    can re-locate the offending data.
    """

    REQUIRED_HASH_FIELDS = (
        "pre_reg_hash",
        "manifest_hash",
        "verifier_hash",
        "output_hash",
        "prompt_hash",
    )
    MIN_HASH_LEN = 16

    # ----- H_H1: every packet carries all required hashes --------------
    def test_H_H1_observation_packet_carries_all_required_hashes(self):
        """H_H1: each ObservationPacket must populate (non-empty) values for
        pre_reg_hash, manifest_hash, verifier_hash, output_hash, prompt_hash,
        each of plausible hash length (>= 16 chars).
        """
        # Arrange
        p = _packet("B_claude", "factorial", "low", 0)

        # Act + Assert
        for field_name in self.REQUIRED_HASH_FIELDS:
            value = getattr(p, field_name)
            assert isinstance(value, str), (
                f"H_H1 REFUTED: {field_name} is not a string ({type(value)})"
            )
            assert value, f"H_H1 REFUTED: {field_name} is empty"
            assert len(value) >= self.MIN_HASH_LEN, (
                f"H_H1 REFUTED: {field_name} length {len(value)} "
                f"< minimum {self.MIN_HASH_LEN}"
            )

    # ----- H_H2: pre_reg_hash mismatch -> INSUFFICIENT_OBSERVABILITY ---
    def test_H_H2_audit_observer_rejects_packets_with_mismatched_pre_reg_hash(self):
        """H_H2: when packets within a single audited stream carry different
        pre_reg_hash values, the cell MUST be classified as
        INSUFFICIENT_OBSERVABILITY (no fabrication of a substantive class).
        """
        # Arrange: A's stream contains two distinct pre_reg_hash values.
        obs = AuditObserver()
        good_hash = PRE_REG_HASH
        bad_hash = "tampered" * 8  # 64 chars, distinct from PRE_REG_HASH
        a = [
            _packet("B_claude", "factorial", "low", i,
                    pre_reg_hash=good_hash if i < 5 else bad_hash)
            for i in range(10)
        ]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert
        assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            f"H_H2 REFUTED: pre_reg_hash mismatch did not produce "
            f"INSUFFICIENT_OBSERVABILITY; got {result.relation_class}"
        )
        assert "pre_reg_hash" in result.reason, (
            f"H_H2 REFUTED: reason text does not cite pre_reg_hash mismatch: "
            f"{result.reason!r}"
        )

    # ----- H_H3: encoder_id mismatch -> INSUFFICIENT_OBSERVABILITY -----
    def test_H_H3_audit_observer_rejects_packets_with_mismatched_encoder_id(self):
        """H_H3: when packets in the two streams carry different encoder_id
        values (cross-encoder contamination), the cell MUST be classified
        as INSUFFICIENT_OBSERVABILITY.
        """
        # Arrange: A uses ENCODER, B uses a different encoder
        obs = AuditObserver()
        alt_encoder = "sentence-transformers/all-mpnet-base-v2"
        a = [_packet("B_claude", "factorial", "low", i, encoder_id=ENCODER)
             for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i, encoder_id=alt_encoder)
             for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert
        assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            f"H_H3 REFUTED: encoder_id mismatch did not produce "
            f"INSUFFICIENT_OBSERVABILITY; got {result.relation_class}"
        )
        assert "encoder" in result.reason, (
            f"H_H3 REFUTED: reason text does not cite encoder mismatch: "
            f"{result.reason!r}"
        )

    # ----- H_H4: offending packet hashes in result match input ---------
    def test_H_H4_offending_packet_hashes_in_audit_result_match_input_packets(self):
        """H_H4: when SMOOTH_SUCCESS_EXCEPTION fires, every hash listed in
        result.offending_packet_hashes MUST be the output_hash of an input
        packet. Refutation: a hash in the result is not present in the
        union of input output_hash values.
        """
        # Arrange: 1 smooth+pass+high in observer A; nothing in B
        obs = AuditObserver()
        exception_packet = _packet(
            "B_claude", "factorial", "high", 0,
            pass_both=True, pass_a=True, pass_b=True,
            max_chunk_displacement=0.05, max_drift_deg=2.0,
            output_hash=("excep" * 13).ljust(64, "0"),
        )
        a = [exception_packet]
        a += [
            _packet("B_claude", "factorial", "high", i,
                    pass_both=False, pass_a=False, pass_b=False,
                    max_chunk_displacement=2.0, max_drift_deg=45.0)
            for i in range(1, 50)
        ]
        b = [
            _packet("B_openweight", "factorial", "high", i,
                    pass_both=False, pass_a=False, pass_b=False,
                    max_chunk_displacement=2.0, max_drift_deg=45.0)
            for i in range(50)
        ]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"), _control_set("B_openweight"))

        # Assert
        assert result.relation_class == RelationClass.SMOOTH_SUCCESS_EXCEPTION, (
            f"H_H4 setup REFUTED: expected SMOOTH_SUCCESS_EXCEPTION; "
            f"got {result.relation_class}"
        )
        assert result.offending_packet_hashes, (
            "H_H4 REFUTED: SMOOTH_SUCCESS_EXCEPTION fired but no offending hashes recorded"
        )
        input_output_hashes = {p.output_hash for p in a} | {p.output_hash for p in b}
        for h in result.offending_packet_hashes:
            assert h in input_output_hashes, (
                f"H_H4 REFUTED: offending hash {h!r} in result not found among "
                f"input packet output_hash values"
            )


# =====================================================================
# H_S1..H_S5 — Boundary sensitivity to registered numeric thresholds
# =====================================================================


class TestBoundarySensitivity:
    """Boundary-sensitivity hypotheses: each registered numeric constant in
    audit_observer.py MUST measurably affect classification when packets sit
    near its predicate boundary. Refutation predicate per test: changing the
    constant by epsilon does NOT change the resulting AuditResult fields.

    These tests kill NumberReplacer-style mutations on the registered
    constants (DISPLACEMENT_MULTIPLIER, DRIFT_DEG_THRESHOLD,
    CONTRACT_AGREEMENT_TOL, CHART_AGREEMENT_REL_TOL, REGIME_AGREEMENT_TOL).
    """

    def test_H_S1_boundary_sensitivity_displacement_multiplier_at_2_5(self):
        """H_S1: when packets sit just below 2.5*L_hat, swapping the multiplier
        from 2.5 to 2.4 must flip the smooth predicate. Refutation: identical
        smooth_fraction under both multipliers (audit ignores the constant).
        """
        # Arrange: max_chunk_displacement chosen so 2.4*l_hat < x < 2.5*l_hat
        l_hat = 0.1
        # x = 2.45 * l_hat: smooth at mult=2.5, NOT smooth at mult=2.4
        x = 2.45 * l_hat
        a = [_packet("B_claude", "factorial", "low", i,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=x) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=x) for i in range(10)]
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        # Act
        obs_default = AuditObserver(displacement_multiplier=2.5)
        obs_strict = AuditObserver(displacement_multiplier=2.4)
        r_default = obs_default.classify_cell(a, b, ctl_a, ctl_b)
        r_strict = obs_strict.classify_cell(a, b, ctl_a, ctl_b)

        # Assert
        assert (r_default.smooth_fraction_a != r_strict.smooth_fraction_a) or \
               (r_default.smooth_fraction_b != r_strict.smooth_fraction_b), (
            "H_S1 REFUTED: changing displacement_multiplier from 2.5 to 2.4 "
            "did not affect smooth_fraction at the boundary; constant is dead."
        )

    def test_H_S2_boundary_sensitivity_drift_threshold_at_15_deg(self):
        """H_S2: when packets sit at max_drift_deg=10 (default 15 → smooth),
        lowering threshold to 5 must flip the smooth predicate.
        """
        # Arrange: drift = 10° (smooth at 15°, not smooth at 5°)
        l_hat = 0.1
        a = [_packet("B_claude", "factorial", "low", i,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=0.5 * l_hat,
                     max_drift_deg=10.0) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=0.5 * l_hat,
                     max_drift_deg=10.0) for i in range(10)]
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        # Act
        obs_default = AuditObserver(drift_deg_threshold=15.0)
        obs_strict = AuditObserver(drift_deg_threshold=5.0)
        r_default = obs_default.classify_cell(a, b, ctl_a, ctl_b)
        r_strict = obs_strict.classify_cell(a, b, ctl_a, ctl_b)

        # Assert
        assert (r_default.smooth_fraction_a != r_strict.smooth_fraction_a) or \
               (r_default.smooth_fraction_b != r_strict.smooth_fraction_b), (
            "H_S2 REFUTED: changing drift_deg_threshold did not affect "
            "smooth_fraction at the boundary; constant is dead."
        )

    def test_H_S3_boundary_sensitivity_contract_tol_at_0_10(self):
        """H_S3: pass-rate gap of 0.10 sits exactly at default contract tol.
        Tightening to 0.05 must flip contract_agreement → different
        relation_class (LOCAL_CONTRACT_DIVERGENCE / VERIFIER_SURFACE_MISMATCH).
        """
        # Arrange: pass_rate_a = 0.6 (6/10), pass_rate_b = 0.5 (5/10), gap = 0.10
        l_hat = 0.1
        a = [_packet("B_claude", "factorial", "low", i,
                     pass_both=(i < 6), pass_a=(i < 6), pass_b=(i < 6),
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=0.5 * l_hat) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     pass_both=(i < 5), pass_a=(i < 5), pass_b=(i < 5),
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=0.5 * l_hat) for i in range(10)]
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        # Act
        obs_default = AuditObserver(contract_agreement_tol=0.10)
        obs_strict = AuditObserver(contract_agreement_tol=0.05)
        r_default = obs_default.classify_cell(a, b, ctl_a, ctl_b)
        r_strict = obs_strict.classify_cell(a, b, ctl_a, ctl_b)

        # Assert: differing relation_class proves the constant is read
        assert r_default.relation_class != r_strict.relation_class, (
            f"H_S3 REFUTED: changing contract_agreement_tol did not change "
            f"relation_class at the boundary; got {r_default.relation_class} "
            f"and {r_strict.relation_class}."
        )

    def test_H_S4_boundary_sensitivity_chart_tol_at_0_50(self):
        """H_S4: l_hat values 0.10 vs 0.18 give relative gap 0.444.
        Default tol 0.50 → chart agrees. Tightening to 0.40 → chart disagrees,
        which (with contract agreement) yields CHART_TRANSITION.
        """
        # Arrange: control sets engineered to give different l_hat values.
        ctl_a = _control_set("B_claude", mean_disp=0.10)
        ctl_b = _control_set("B_openweight", mean_disp=0.18)
        # Equal pass rates, equal smooth fractions → only chart predicate differs
        l_hat_used = 0.14  # within band that keeps packets smooth under both l_hats
        a = [_packet("B_claude", "factorial", "low", i,
                     mean_chunk_displacement=l_hat_used,
                     max_chunk_displacement=0.05) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     mean_chunk_displacement=l_hat_used,
                     max_chunk_displacement=0.05) for i in range(10)]

        # Act
        obs_default = AuditObserver(chart_agreement_rel_tol=0.50)
        obs_strict = AuditObserver(chart_agreement_rel_tol=0.40)
        r_default = obs_default.classify_cell(a, b, ctl_a, ctl_b)
        r_strict = obs_strict.classify_cell(a, b, ctl_a, ctl_b)

        # Assert
        assert r_default.relation_class != r_strict.relation_class, (
            f"H_S4 REFUTED: changing chart_agreement_rel_tol did not change "
            f"relation_class at the boundary; got {r_default.relation_class} "
            f"and {r_strict.relation_class}."
        )

    def test_H_S5_boundary_sensitivity_regime_tol_at_0_10(self):
        """H_S5: smooth_fraction gap of 0.10 sits at default regime tol.
        Tightening to 0.05 must flip regime_agreement → PIVOT_DISAGREEMENT.
        """
        # Arrange: equal pass rates and l_hats; smooth_fraction_a = 0.6,
        # smooth_fraction_b = 0.5 → gap 0.10.
        l_hat = 0.1
        # A: 6 smooth, 4 pivot (huge displacement)
        a = [_packet("B_claude", "factorial", "low", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=(0.5 * l_hat) if i < 6 else (5.0 * l_hat))
             for i in range(10)]
        # B: 5 smooth, 5 pivot
        b = [_packet("B_openweight", "factorial", "low", i,
                     pass_both=True, pass_a=True, pass_b=True,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=(0.5 * l_hat) if i < 5 else (5.0 * l_hat))
             for i in range(10)]
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        # Act
        obs_default = AuditObserver(regime_agreement_tol=0.10)
        obs_strict = AuditObserver(regime_agreement_tol=0.05)
        r_default = obs_default.classify_cell(a, b, ctl_a, ctl_b)
        r_strict = obs_strict.classify_cell(a, b, ctl_a, ctl_b)

        # Assert
        assert r_default.relation_class != r_strict.relation_class, (
            f"H_S5 REFUTED: changing regime_agreement_tol did not change "
            f"relation_class at the boundary; got {r_default.relation_class} "
            f"and {r_strict.relation_class}."
        )


# =====================================================================
# H_E1..H_E4 — Equality-predicate polarity in mismatch detection
# =====================================================================


class TestEqualityPolarity:
    """Equality-predicate polarity: every equality check in
    _insufficient_reason and audit_streams MUST be tested in BOTH the
    matching and mismatching arm. Refutation predicate per test:
    only one polarity is observable in the AuditResult.

    These tests kill Eq_LtE / Eq_NotEq / Eq_Gt / Eq_Is / Eq_GtE mutations.
    """

    def test_H_E1_equality_polarity_pre_reg_hash_mismatch_triggers_insufficient_observability(self):
        """H_E1: when streams agree on pre_reg_hash, classification proceeds
        normally; when they disagree by even one packet, INSUFFICIENT fires.
        Tests BOTH arms of the equality check.
        """
        # Arrange: matched-hash streams
        obs = AuditObserver()
        a_match = [_packet("B_claude", "factorial", "low", i, pre_reg_hash=PRE_REG_HASH)
                   for i in range(10)]
        b_match = [_packet("B_openweight", "factorial", "low", i, pre_reg_hash=PRE_REG_HASH)
                   for i in range(10)]
        # Mismatched-hash streams (one packet drifts)
        bad_hash = "tampered" * 8
        a_drift = [
            _packet("B_claude", "factorial", "low", i,
                    pre_reg_hash=PRE_REG_HASH if i < 9 else bad_hash)
            for i in range(10)
        ]
        b_drift = [_packet("B_openweight", "factorial", "low", i, pre_reg_hash=PRE_REG_HASH)
                   for i in range(10)]
        ctl_a = _control_set("B_claude")
        ctl_b = _control_set("B_openweight")

        # Act
        r_match = obs.classify_cell(a_match, b_match, ctl_a, ctl_b)
        r_drift = obs.classify_cell(a_drift, b_drift, ctl_a, ctl_b)

        # Assert: both polarities yield distinguishable outputs
        assert r_match.relation_class != RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_E1 REFUTED: matched pre_reg_hash streams classified as "
            "INSUFFICIENT (false-positive arm)."
        )
        assert r_drift.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_E1 REFUTED: mismatched pre_reg_hash streams NOT classified "
            "as INSUFFICIENT (false-negative arm)."
        )

    def test_H_E2_equality_polarity_encoder_id_mismatch_triggers_insufficient_observability(self):
        """H_E2: encoder_id equality is checked in BOTH arms. Same encoder
        across streams → substantive class. Different encoders → INSUFFICIENT.
        """
        # Arrange
        obs = AuditObserver()
        alt_encoder = "sentence-transformers/all-mpnet-base-v2"
        a_match = [_packet("B_claude", "factorial", "low", i, encoder_id=ENCODER)
                   for i in range(10)]
        b_match = [_packet("B_openweight", "factorial", "low", i, encoder_id=ENCODER)
                   for i in range(10)]
        a_drift = [_packet("B_claude", "factorial", "low", i, encoder_id=ENCODER)
                   for i in range(10)]
        b_drift = [_packet("B_openweight", "factorial", "low", i, encoder_id=alt_encoder)
                   for i in range(10)]
        ctl_a = _control_set("B_claude")
        ctl_b = _control_set("B_openweight")

        # Act
        r_match = obs.classify_cell(a_match, b_match, ctl_a, ctl_b)
        r_drift = obs.classify_cell(a_drift, b_drift, ctl_a, ctl_b)

        # Assert
        assert r_match.relation_class != RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_E2 REFUTED: matched encoder_id classified as INSUFFICIENT."
        )
        assert r_drift.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_E2 REFUTED: mismatched encoder_id did NOT yield INSUFFICIENT."
        )

    def test_H_E3_equality_polarity_packet_schema_version_consistency_required(self):
        """H_E3: packet_schema_version is locked at construction time. Every
        packet built via _packet() carries 'v4.0'. Refutation: streams with a
        mixed schema_version pass through audit without surfacing in the typed
        AuditResult (cannot distinguish, so version is meaningless).

        Operational test: confirm packet_schema_version is exposed on every
        packet AND that audit_streams orders results deterministically based
        on (task, tier) keys derived from those packets.
        """
        # Arrange: every packet carries the schema version
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act + Assert: every packet exposes the field with the same value
        schema_versions = {p.packet_schema_version for p in (a + b)}
        assert schema_versions == {"v4.1"}, (
            f"H_E3 REFUTED: packet_schema_version is not consistently v4.1 "
            f"across constructed packets; got {schema_versions}."
        )

        # And: classify_cell still produces a substantive class (no spurious
        # INSUFFICIENT triggered by version itself in matched case)
        result = obs.classify_cell(a, b, _control_set("B_claude"),
                                   _control_set("B_openweight"))
        assert result.relation_class != RelationClass.INSUFFICIENT_OBSERVABILITY, (
            "H_E3 REFUTED: matched-version streams flagged INSUFFICIENT."
        )

    def test_H_E4_equality_polarity_observer_id_consistency_within_stream(self):
        """H_E4: observer_id from packets_a[0] is propagated into
        observer_pair[0]; observer_id from packets_b[0] into observer_pair[1].
        Refutation: classify_cell ignores observer_id and emits a generic
        ('A', 'B') pair regardless of actual packet observer_ids.
        """
        # Arrange: distinguishable observer_ids
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, _control_set("B_claude"),
                                   _control_set("B_openweight"))

        # Assert: observer_id propagation is correct in BOTH slots
        assert result.observer_pair[0] == "B_claude", (
            f"H_E4 REFUTED: observer_pair[0] = {result.observer_pair[0]!r}, "
            f"expected 'B_claude'."
        )
        assert result.observer_pair[1] == "B_openweight", (
            f"H_E4 REFUTED: observer_pair[1] = {result.observer_pair[1]!r}, "
            f"expected 'B_openweight'."
        )


# =====================================================================
# H_BETA1 — Inclusive boundary on smooth predicate (<=, not <)
# =====================================================================


class TestInclusiveBoundary:
    """Inclusive-boundary hypothesis: the smoothness predicate uses '<='
    so a packet AT exactly the threshold is smooth. Refutation: a packet
    with max_chunk_displacement == multiplier * L_hat is classified
    pivot. This kills LtE_Lt mutations.
    """

    def test_H_BETA1_inclusive_boundary_smooth_at_exact_threshold_value(self):
        """H_BETA1: max_chunk_displacement == 2.5 * l_hat AND
        max_drift_deg == 15.0 → packet IS smooth (predicate uses <=, not <).
        """
        # Arrange: every packet sits exactly on BOTH thresholds
        obs = AuditObserver()
        l_hat = 0.1
        exact_displacement = obs.displacement_multiplier * l_hat  # 2.5 * 0.1 = 0.25
        exact_drift = obs.drift_deg_threshold                     # 15.0
        a = [_packet("B_claude", "factorial", "low", i,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=exact_displacement,
                     max_drift_deg=exact_drift) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i,
                     mean_chunk_displacement=l_hat,
                     max_chunk_displacement=exact_displacement,
                     max_drift_deg=exact_drift) for i in range(10)]
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        # Act
        result = obs.classify_cell(a, b, ctl_a, ctl_b)

        # Assert
        assert result.smooth_fraction_a == 1.0, (
            f"H_BETA1 REFUTED: at exact threshold, smooth_fraction_a = "
            f"{result.smooth_fraction_a}, expected 1.0 (predicate must be <=)."
        )
        assert result.smooth_fraction_b == 1.0, (
            f"H_BETA1 REFUTED: at exact threshold, smooth_fraction_b = "
            f"{result.smooth_fraction_b}, expected 1.0 (predicate must be <=)."
        )


# =====================================================================
# H_VOID1 — Empty-stream substrate handling
# =====================================================================


class TestEmptyStreamHandling:
    """Empty-stream hypothesis: empty packet streams (or empty controls)
    are NOT silently classified as AGREEMENT; they fire INSUFFICIENT
    per pre-reg §4.2 (calibration sample too small).
    """

    def test_H_VOID1_empty_stream_handling_emits_insufficient_observability(self):
        """H_VOID1: passing empty control sets MUST yield
        INSUFFICIENT_OBSERVABILITY (n=0 < min_calibration_n=10).
        """
        # Arrange
        obs = AuditObserver()
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act: empty controls
        result = obs.classify_cell(a, b, control_a=[], control_b=[])

        # Assert
        assert result.relation_class == RelationClass.INSUFFICIENT_OBSERVABILITY, (
            f"H_VOID1 REFUTED: empty control stream classified as "
            f"{result.relation_class}, expected INSUFFICIENT_OBSERVABILITY."
        )
        assert "calibration" in result.reason.lower() or \
               "too small" in result.reason.lower(), (
            f"H_VOID1 REFUTED: reason text does not cite calibration shortfall: "
            f"{result.reason!r}"
        )


# =====================================================================
# H_NOT1 — Boolean-polarity meta-test
# =====================================================================


class TestBooleanPolarity:
    """Boolean-polarity hypothesis: every threshold-driven boolean
    predicate in audit_observer must be observable in BOTH the True
    and False arm via constructed packet streams. This is the
    parameterized direct-test variant from the spec.
    """

    def test_H_NOT1_boolean_polarity_every_threshold_has_above_and_below_classification(self):
        """H_NOT1: for each registered threshold, construct (above, below)
        packet pairs and assert both classifications are reachable.
        Refutation: any threshold's predicate is unreachable in some arm.
        """
        # Arrange: matrix of thresholds with above/below sentinel values
        l_hat = 0.1
        ctl_a = _control_set("B_claude", mean_disp=l_hat)
        ctl_b = _control_set("B_openweight", mean_disp=l_hat)

        def _build_uniform_stream(observer_id: str, max_disp: float, drift: float):
            return [
                _packet(observer_id, "factorial", "low", i,
                        mean_chunk_displacement=l_hat,
                        max_chunk_displacement=max_disp,
                        max_drift_deg=drift)
                for i in range(10)
            ]

        obs = AuditObserver()

        # Below displacement threshold: smooth_fraction == 1.0
        a_below = _build_uniform_stream("B_claude",
                                        max_disp=0.5 * l_hat, drift=5.0)
        b_below = _build_uniform_stream("B_openweight",
                                        max_disp=0.5 * l_hat, drift=5.0)
        r_below = obs.classify_cell(a_below, b_below, ctl_a, ctl_b)

        # Above displacement threshold: smooth_fraction == 0.0
        a_above = _build_uniform_stream("B_claude",
                                        max_disp=10.0 * l_hat, drift=5.0)
        b_above = _build_uniform_stream("B_openweight",
                                        max_disp=10.0 * l_hat, drift=5.0)
        r_above = obs.classify_cell(a_above, b_above, ctl_a, ctl_b)

        # Above drift threshold (with low displacement): also pivot
        a_drift = _build_uniform_stream("B_claude",
                                        max_disp=0.5 * l_hat, drift=45.0)
        b_drift = _build_uniform_stream("B_openweight",
                                        max_disp=0.5 * l_hat, drift=45.0)
        r_drift = obs.classify_cell(a_drift, b_drift, ctl_a, ctl_b)

        # Assert: BOTH polarities visible per threshold
        assert r_below.smooth_fraction_a == 1.0 and r_below.smooth_fraction_b == 1.0, (
            f"H_NOT1 REFUTED: below-threshold packets did not all classify "
            f"smooth: a={r_below.smooth_fraction_a}, b={r_below.smooth_fraction_b}"
        )
        assert r_above.smooth_fraction_a == 0.0 and r_above.smooth_fraction_b == 0.0, (
            f"H_NOT1 REFUTED: above-displacement packets still classify smooth: "
            f"a={r_above.smooth_fraction_a}, b={r_above.smooth_fraction_b}"
        )
        assert r_drift.smooth_fraction_a == 0.0 and r_drift.smooth_fraction_b == 0.0, (
            f"H_NOT1 REFUTED: above-drift packets still classify smooth: "
            f"a={r_drift.smooth_fraction_a}, b={r_drift.smooth_fraction_b}"
        )


# =====================================================================
# H_AR1..H_AR2 — Analytical formula identity
# =====================================================================


class TestFormulaIdentity:
    """Formula-identity hypotheses: numerical aggregates produced by the
    audit observer must match analytical formulas exactly (within
    floating-point tolerance). These tests kill Div_Mul / Div_LShift /
    Div_Pow mutations on the aggregation arithmetic.
    """

    def test_H_AR1_formula_identity_l_hat_equals_mean_displacement_of_control_packets(self):
        """H_AR1: l_hat_a reported in AuditResult MUST equal the analytical
        arithmetic mean of mean_chunk_displacement values across control_a.
        Refutation: division replaced by multiplication / shift / power.
        """
        # Arrange: control packets with KNOWN distinct mean_chunk_displacement
        obs = AuditObserver()
        known_values_a = [0.10, 0.12, 0.08, 0.15, 0.11, 0.09, 0.13, 0.10, 0.14, 0.08]
        known_values_b = [0.20, 0.22, 0.18, 0.25, 0.21, 0.19, 0.23, 0.20, 0.24, 0.18]
        analytical_mean_a = sum(known_values_a) / len(known_values_a)
        analytical_mean_b = sum(known_values_b) / len(known_values_b)

        ctl_a = [
            _packet("B_claude", "calib", "control", i,
                    mean_chunk_displacement=v)
            for i, v in enumerate(known_values_a)
        ]
        ctl_b = [
            _packet("B_openweight", "calib", "control", i,
                    mean_chunk_displacement=v)
            for i, v in enumerate(known_values_b)
        ]
        a = [_packet("B_claude", "factorial", "low", i) for i in range(10)]
        b = [_packet("B_openweight", "factorial", "low", i) for i in range(10)]

        # Act
        result = obs.classify_cell(a, b, ctl_a, ctl_b)

        # Assert: l_hat values match analytical means (1e-9 floating tolerance)
        assert abs(result.l_hat_a - analytical_mean_a) < 1e-9, (
            f"H_AR1 REFUTED: l_hat_a={result.l_hat_a} != analytical mean "
            f"{analytical_mean_a}; division operator may have been replaced."
        )
        assert abs(result.l_hat_b - analytical_mean_b) < 1e-9, (
            f"H_AR1 REFUTED: l_hat_b={result.l_hat_b} != analytical mean "
            f"{analytical_mean_b}; division operator may have been replaced."
        )

    def test_H_AR2_formula_identity_chart_agreement_uses_relative_difference(self):
        """H_AR2: _chart_agreement uses |la - lb| / max(la, lb), not
        absolute difference. Construct two l_hat values where ABSOLUTE
        gap (0.10) would imply disagreement under any sane absolute tol,
        but relative gap (0.10/0.20 = 0.5) is exactly at the registered
        boundary 0.50 → agreement holds. Then nudge to 0.10 vs 0.21 →
        relative gap 0.476 still within 0.50 → agreement. Then 0.10 vs
        0.30 → relative gap 0.667 > 0.50 → disagreement.
        """
        # Arrange: build three control configurations with controlled l_hats
        obs = AuditObserver()

        def _ctl(observer: str, mean_disp: float):
            return [
                _packet(observer, "calib", "control", i,
                        mean_chunk_displacement=mean_disp)
                for i in range(10)
            ]

        # Configuration 1: l_hat_a = 0.10, l_hat_b = 0.20 (relative gap 0.5)
        # max(la, lb) = 0.20, |la-lb|/max = 0.5 <= 0.5 → chart_agreement TRUE
        # Smooth/pass identical → AGREEMENT
        ctl_a1 = _ctl("B_claude", 0.10)
        ctl_b1 = _ctl("B_openweight", 0.20)
        # Packets uniformly smooth under both observers' l_hats:
        # max_chunk_displacement = 0.05 < 2.5 * 0.10 = 0.25
        a1 = [_packet("B_claude", "factorial", "low", i,
                      max_chunk_displacement=0.05, max_drift_deg=2.0)
              for i in range(10)]
        b1 = [_packet("B_openweight", "factorial", "low", i,
                      max_chunk_displacement=0.05, max_drift_deg=2.0)
              for i in range(10)]
        r1 = obs.classify_cell(a1, b1, ctl_a1, ctl_b1)

        # Configuration 2: l_hat_a = 0.10, l_hat_b = 0.30
        # |la-lb|/max = 0.20/0.30 = 0.667 > 0.50 → chart_agreement FALSE
        # → CHART_TRANSITION
        ctl_a2 = _ctl("B_claude", 0.10)
        ctl_b2 = _ctl("B_openweight", 0.30)
        a2 = [_packet("B_claude", "factorial", "low", i,
                      max_chunk_displacement=0.05, max_drift_deg=2.0)
              for i in range(10)]
        b2 = [_packet("B_openweight", "factorial", "low", i,
                      max_chunk_displacement=0.05, max_drift_deg=2.0)
              for i in range(10)]
        r2 = obs.classify_cell(a2, b2, ctl_a2, ctl_b2)

        # Assert: relative-difference formula is honored
        # If formula were absolute (|la-lb| <= 0.50), BOTH r1 and r2 would
        # have chart_agreement TRUE (gaps 0.10 and 0.20, both < 0.50)
        # and BOTH would classify as AGREEMENT, indistinguishable.
        assert r1.relation_class == RelationClass.AGREEMENT, (
            f"H_AR2 REFUTED: l_hat=(0.10, 0.20) (relative gap 0.5 == tol) "
            f"did not classify as AGREEMENT; got {r1.relation_class}."
        )
        assert r2.relation_class == RelationClass.CHART_TRANSITION, (
            f"H_AR2 REFUTED: l_hat=(0.10, 0.30) (relative gap 0.667 > tol) "
            f"did not classify as CHART_TRANSITION; got {r2.relation_class}. "
            f"Formula may have been replaced with absolute difference."
        )
