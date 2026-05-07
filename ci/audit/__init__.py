"""Deterministic audit observer substrate.

Public interface for the typed-observer audit layer described in
``supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md``. No LLM imports.
"""

from .audit_observer import (
    AuditObserver,
    CHART_AGREEMENT_REL_TOL,
    CONTRACT_AGREEMENT_TOL,
    DISPLACEMENT_MULTIPLIER,
    DRIFT_DEG_THRESHOLD,
    MIN_CALIBRATION_N,
    REGIME_AGREEMENT_TOL,
)
from .audit_result import AuditResult
from .observation_packet import ObservationPacket
from .observer import Observer
from .relation_classes import RelationClass

__all__ = [
    "AuditObserver",
    "AuditResult",
    "CHART_AGREEMENT_REL_TOL",
    "CONTRACT_AGREEMENT_TOL",
    "DISPLACEMENT_MULTIPLIER",
    "DRIFT_DEG_THRESHOLD",
    "MIN_CALIBRATION_N",
    "ObservationPacket",
    "Observer",
    "REGIME_AGREEMENT_TOL",
    "RelationClass",
]
