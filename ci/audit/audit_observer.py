"""AuditObserver: deterministic intermediary between black-box LLM observers.

Implements the predicate logic and RelationClass assignment rules from
pre-registration v4 §4.1 and §4.2 EXACTLY. No LLM imports. Hash-pinned
thresholds. Same input always yields the same output.
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .audit_result import AuditResult
from .control_router import (
    partition_controls_by_model,
    select_controls_for_cell_pair,
)
from .observation_packet import ObservationPacket
from .relation_classes import RelationClass

# Hash-pinned thresholds (locked in pre-reg v4 §1 / §4.1).
DISPLACEMENT_MULTIPLIER: float = 2.5
DRIFT_DEG_THRESHOLD: float = 15.0
CONTRACT_AGREEMENT_TOL: float = 0.10
CHART_AGREEMENT_REL_TOL: float = 0.50
REGIME_AGREEMENT_TOL: float = 0.10
MIN_CALIBRATION_N: int = 10


class AuditObserver:
    """Deterministic audit observer.

    Consumes typed ObservationPackets from two independent black-box
    observers and emits one RelationClass per (task, tier) cell.
    """

    def __init__(
        self,
        displacement_multiplier: float = DISPLACEMENT_MULTIPLIER,
        drift_deg_threshold: float = DRIFT_DEG_THRESHOLD,
        contract_agreement_tol: float = CONTRACT_AGREEMENT_TOL,
        chart_agreement_rel_tol: float = CHART_AGREEMENT_REL_TOL,
        regime_agreement_tol: float = REGIME_AGREEMENT_TOL,
        min_calibration_n: int = MIN_CALIBRATION_N,
    ) -> None:
        self.displacement_multiplier = float(displacement_multiplier)
        self.drift_deg_threshold = float(drift_deg_threshold)
        self.contract_agreement_tol = float(contract_agreement_tol)
        self.chart_agreement_rel_tol = float(chart_agreement_rel_tol)
        self.regime_agreement_tol = float(regime_agreement_tol)
        self.min_calibration_n = int(min_calibration_n)

    # ------------------------------------------------------------------
    # §4.1 packet-level predicates
    # ------------------------------------------------------------------
    def _passed(self, p: ObservationPacket) -> bool:
        # Cosmic-Ray: ReplaceFalseWithTrue on the .get() default is EQUIVALENT.
        # ObservationPacket.verifier_dict() always populates pass_both (enforced
        # by the dataclass __post_init__), so the default never fires.
        return bool(p.verifier_dict().get("pass_both", False))

    def _smooth(self, p: ObservationPacket, l_hat: float) -> bool:
        return (
            p.max_chunk_displacement <= self.displacement_multiplier * l_hat
            and p.max_drift_deg <= self.drift_deg_threshold
        )

    def classify_packet(
        self, p: ObservationPacket, l_hat: float
    ) -> Dict[str, bool]:
        """Return {smooth, passed, smooth_success_exception} for one packet."""
        smooth = self._smooth(p, l_hat)
        passed = self._passed(p)
        return {
            "smooth": smooth,
            "passed": passed,
            # Cosmic-Ray: Eq_Is and Eq_LtE on `p.tier == "high"` are
            # EQUIVALENT under the closed tier set {"control", "low", "high"}.
            # Eq_Is: "high" is an interned string literal; tests pass tier
            # as a literal, so `is` and `==` agree.
            # Eq_LtE: alphabetically "control" < "high" < "low", so
            # `tier <= "high"` matches {"control", "high"}; "control" is
            # filtered upstream and never reaches this predicate, so the
            # observable set collapses to {"high"} — same as `==`.
            "smooth_success_exception": (
                smooth and passed and p.tier == "high"
            ),
        }

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------
    def _l_hat(self, control_packets: Sequence[ObservationPacket]) -> float:
        if not control_packets:
            return float("nan")
        return float(
            sum(p.mean_chunk_displacement for p in control_packets)
            / len(control_packets)
        )

    def _pass_rate(self, packets: Sequence[ObservationPacket]) -> float:
        if not packets:
            return 0.0
        return float(
            sum(1 for p in packets if self._passed(p)) / len(packets)
        )

    def _smooth_fraction(
        self, packets: Sequence[ObservationPacket], l_hat: float
    ) -> float:
        if not packets:
            return 0.0
        return float(
            sum(1 for p in packets if self._smooth(p, l_hat)) / len(packets)
        )

    # ------------------------------------------------------------------
    # §4.1 stream-level predicates
    # ------------------------------------------------------------------
    def _contract_agreement(self, pa: float, pb: float) -> bool:
        return abs(pa - pb) <= self.contract_agreement_tol

    def _chart_agreement(self, la: float, lb: float) -> bool:
        denom = max(la, lb)
        # Cosmic-Ray: Eq_LtE on `denom == 0.0` is EQUIVALENT — denom is
        # max(la, lb) and L_hat values are non-negative by construction
        # (mean of non-negative chunk displacements), so denom <= 0 ↔
        # denom == 0.
        if denom == 0.0:
            # Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `la == lb` are all
            # EQUIVALENT inside this branch — the branch is reached only
            # when both la and lb are 0.0, so all comparators agree.
            return la == lb
        return abs(la - lb) / denom <= self.chart_agreement_rel_tol

    def _regime_agreement(self, sa: float, sb: float) -> bool:
        return abs(sa - sb) <= self.regime_agreement_tol

    # ------------------------------------------------------------------
    # Insufficient-observability checks (§4.2 trailing list)
    # ------------------------------------------------------------------
    def _insufficient_reason(
        self,
        packets_a: Sequence[ObservationPacket],
        packets_b: Sequence[ObservationPacket],
        control_a: Sequence[ObservationPacket],
        control_b: Sequence[ObservationPacket],
    ) -> Optional[str]:
        for p in list(packets_a) + list(packets_b):
            if p.error is not None:
                return "observer error in stream"
        encoders_a = {p.encoder_id for p in packets_a}
        encoders_b = {p.encoder_id for p in packets_b}
        if encoders_a and encoders_b and encoders_a != encoders_b:
            return "encoder mismatch between observers"
        hashes = {
            p.pre_reg_hash for p in list(packets_a) + list(packets_b)
        }
        if len(hashes) > 1:
            return "pre_reg_hash mismatch in input packets"
        if len(control_a) < self.min_calibration_n:
            return (
                f"L_hat calibration sample too small for observer A "
                f"(n={len(control_a)} < {self.min_calibration_n})"
            )
        if len(control_b) < self.min_calibration_n:
            return (
                f"L_hat calibration sample too small for observer B "
                f"(n={len(control_b)} < {self.min_calibration_n})"
            )
        return None

    # ------------------------------------------------------------------
    # §4.2 cell-level classification
    # ------------------------------------------------------------------
    def classify_cell(
        self,
        packets_a: Sequence[ObservationPacket],
        packets_b: Sequence[ObservationPacket],
        control_a: Optional[Sequence[ObservationPacket]] = None,
        control_b: Optional[Sequence[ObservationPacket]] = None,
    ) -> AuditResult:
        """Classify a single (task, tier) cell into one RelationClass."""
        control_a = list(control_a) if control_a is not None else []
        control_b = list(control_b) if control_b is not None else []
        packets_a = list(packets_a)
        packets_b = list(packets_b)

        observer_pair = (
            packets_a[0].observer_id if packets_a else "A",
            packets_b[0].observer_id if packets_b else "B",
        )
        task = packets_a[0].task_id if packets_a else (
            packets_b[0].task_id if packets_b else "?"
        )
        tier = packets_a[0].tier if packets_a else (
            packets_b[0].tier if packets_b else "?"
        )

        insufficient = self._insufficient_reason(
            packets_a, packets_b, control_a, control_b
        )
        if insufficient is not None:
            return AuditResult(
                observer_pair=observer_pair,
                task=task,
                tier=tier,
                relation_class=RelationClass.INSUFFICIENT_OBSERVABILITY,
                pass_rate_a=self._pass_rate(packets_a),
                pass_rate_b=self._pass_rate(packets_b),
                smooth_fraction_a=0.0,
                smooth_fraction_b=0.0,
                l_hat_a=float("nan"),
                l_hat_b=float("nan"),
                n_a=len(packets_a),
                n_b=len(packets_b),
                reason=insufficient,
                offending_packet_hashes=tuple(),
            )

        l_hat_a = self._l_hat(control_a)
        l_hat_b = self._l_hat(control_b)
        pa = self._pass_rate(packets_a)
        pb = self._pass_rate(packets_b)
        sa = self._smooth_fraction(packets_a, l_hat_a)
        sb = self._smooth_fraction(packets_b, l_hat_b)

        # §4.2 second branch: high-tier smooth-success exceptions.
        # Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `tier == "high"` are
        # EQUIVALENT — Eq_LtE collapses to == under the closed tier set
        # (see classify_packet); Eq_GtE would also enter for tier="low",
        # but the inner classify_packet still filters via == "high", so
        # `exc_a` and `exc_b` are empty and the branch returns to the
        # ladder unchanged.
        if tier == "high":
            exc_a = [
                p for p in packets_a
                if self.classify_packet(p, l_hat_a)["smooth_success_exception"]
            ]
            exc_b = [
                p for p in packets_b
                if self.classify_packet(p, l_hat_b)["smooth_success_exception"]
            ]
            if exc_a or exc_b:
                tasks_a = {p.task_id for p in exc_a}
                tasks_b = {p.task_id for p in exc_b}
                overlap = tasks_a & tasks_b
                offending = tuple(
                    sorted(p.output_hash for p in (exc_a + exc_b))
                )
                if overlap:
                    return AuditResult(
                        observer_pair=observer_pair,
                        task=task,
                        tier=tier,
                        relation_class=(
                            RelationClass.TRUE_CERTIFICATE_REFUTATION
                        ),
                        pass_rate_a=pa,
                        pass_rate_b=pb,
                        smooth_fraction_a=sa,
                        smooth_fraction_b=sb,
                        l_hat_a=l_hat_a,
                        l_hat_b=l_hat_b,
                        n_a=len(packets_a),
                        n_b=len(packets_b),
                        reason=(
                            "smooth-success exception observed in BOTH "
                            "observers on overlapping task"
                        ),
                        offending_packet_hashes=offending,
                    )
                return AuditResult(
                    observer_pair=observer_pair,
                    task=task,
                    tier=tier,
                    relation_class=RelationClass.SMOOTH_SUCCESS_EXCEPTION,
                    pass_rate_a=pa,
                    pass_rate_b=pb,
                    smooth_fraction_a=sa,
                    smooth_fraction_b=sb,
                    l_hat_a=l_hat_a,
                    l_hat_b=l_hat_b,
                    n_a=len(packets_a),
                    n_b=len(packets_b),
                    reason=(
                        "smooth-success exception in single observer; "
                        "deferring (alpha)/(beta) classification to "
                        "human review per pre-reg v4 §13"
                    ),
                    offending_packet_hashes=offending,
                )

        # §4.2 contract / chart / regime ladder.
        if not self._contract_agreement(pa, pb):
            if self._chart_agreement(l_hat_a, l_hat_b):
                rc = RelationClass.LOCAL_CONTRACT_DIVERGENCE
                reason = (
                    "pass-rate gap exceeds 0.10 with charts in agreement"
                )
            else:
                rc = RelationClass.VERIFIER_SURFACE_MISMATCH
                reason = (
                    "pass-rate gap exceeds 0.10 AND charts disagree"
                )
        elif not self._chart_agreement(l_hat_a, l_hat_b):
            rc = RelationClass.CHART_TRANSITION
            reason = "L_hat values diverge by more than 50% relative"
        elif not self._regime_agreement(sa, sb):
            rc = RelationClass.PIVOT_DISAGREEMENT
            reason = "smooth-fraction gap exceeds 0.10"
        else:
            rc = RelationClass.AGREEMENT
            reason = "all three predicates satisfied"

        return AuditResult(
            observer_pair=observer_pair,
            task=task,
            tier=tier,
            relation_class=rc,
            pass_rate_a=pa,
            pass_rate_b=pb,
            smooth_fraction_a=sa,
            smooth_fraction_b=sb,
            l_hat_a=l_hat_a,
            l_hat_b=l_hat_b,
            n_a=len(packets_a),
            n_b=len(packets_b),
            reason=reason,
            offending_packet_hashes=tuple(),
        )

    # ------------------------------------------------------------------
    # Stream-level driver
    # ------------------------------------------------------------------
    def audit_streams(
        self,
        stream_a: Iterable[ObservationPacket],
        stream_b: Iterable[ObservationPacket],
    ) -> List[AuditResult]:
        """Group streams by (task, tier) and return one AuditResult per cell."""
        a_list = list(stream_a)
        b_list = list(stream_b)

        # Per-(observer, model) calibration sets per pre-reg §6 — built by
        # the control_router module so the routing logic has its own
        # mutation-test session and stays disentangled from §4.1/§4.2.
        control_a_by_model = partition_controls_by_model(a_list)
        control_b_by_model = partition_controls_by_model(b_list)

        cells: Dict[Tuple[str, str], Tuple[
            List[ObservationPacket], List[ObservationPacket]
        ]] = {}
        # Cosmic-Ray: Eq_Is and Eq_LtE on the two `tier == "control"`
        # filters below are EQUIVALENT under the closed tier set
        # (see partition_controls_by_model in control_router.py for the
        # same justification: only "control" sorts <= "control"
        # alphabetically among the three valid tiers).
        for p in a_list:
            if p.tier == "control":
                continue
            key = (p.task_id, p.tier)
            cells.setdefault(key, ([], []))[0].append(p)
        for p in b_list:
            if p.tier == "control":
                continue
            key = (p.task_id, p.tier)
            cells.setdefault(key, ([], []))[1].append(p)

        results: List[AuditResult] = []
        for key in sorted(cells.keys()):
            packets_a, packets_b = cells[key]
            controls_a, controls_b = select_controls_for_cell_pair(
                control_a_by_model, control_b_by_model,
                packets_a, packets_b,
            )
            results.append(
                self.classify_cell(
                    packets_a,
                    packets_b,
                    control_a=controls_a,
                    control_b=controls_b,
                )
            )
        return results
