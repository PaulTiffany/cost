"""Typed Observer class.

Mirrors the typed-observer pattern from prior symbolic-substrate work:
each observer carries an explicit resolution and a drift history, and
exposes a deterministic resolution-adjustment callable. Pure Python +
numpy. No LLM or web imports.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np


def _default_adjust(current: float, drift: float) -> float:
    """Default resolution-adjustment policy.

    Multiplicative damping: increase resolution (smaller value) when
    drift exceeds 1.0, otherwise relax slowly. Deterministic.
    """
    if drift > 1.0:
        return float(current * 0.5)
    return float(current * 1.05)


@dataclass
class Observer:
    """Typed observer with explicit resolution and drift history."""

    name: str
    resolution: float = 1.0
    drift_history: List[float] = field(default_factory=list)
    adjust_resolution: Callable[[float, float], float] = field(
        default=_default_adjust
    )

    def record_drift(self, drift: float) -> None:
        """Append a drift measurement to history (no resolution change)."""
        self.drift_history.append(float(drift))

    def step(self, drift: float) -> float:
        """Record drift, adjust resolution, return new resolution."""
        self.record_drift(drift)
        self.resolution = float(self.adjust_resolution(self.resolution, drift))
        return self.resolution

    def mean_drift(self) -> Optional[float]:
        """Return mean of drift_history or None if empty."""
        if not self.drift_history:
            return None
        return float(np.mean(self.drift_history))
