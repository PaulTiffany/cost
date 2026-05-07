"""AuditResult dataclass: one per (observer_pair, task, tier) cell."""

from dataclasses import dataclass, field
from typing import Tuple

from .relation_classes import RelationClass


@dataclass(frozen=True)
class AuditResult:
    """Deterministic per-cell audit outcome.

    Emitted by AuditObserver.classify_cell. One AuditResult per
    (observer_pair, task, tier) cell in the experiment design.
    """

    observer_pair: Tuple[str, str]
    task: str
    tier: str
    relation_class: RelationClass
    pass_rate_a: float
    pass_rate_b: float
    smooth_fraction_a: float
    smooth_fraction_b: float
    l_hat_a: float
    l_hat_b: float
    n_a: int
    n_b: int
    reason: str
    offending_packet_hashes: Tuple[str, ...] = field(default_factory=tuple)
