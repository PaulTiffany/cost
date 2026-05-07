"""Per-(observer, model) control-set routing for the audit pipeline.

Pre-reg v4 §6: L_hat is calibrated per (observer, model) pair. The audit
substrate's predicate ladder (audit_observer.py) consumes flat control
lists per cell; this module bridges the two by partitioning a stream's
control packets by model_id and then selecting the right calibration set
for each non-control cell.

Pure stdlib + ObservationPacket. No LLM, no IO, no audit-observer imports.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .observation_packet import ObservationPacket


def partition_controls_by_model(
    stream: Sequence[ObservationPacket],
) -> Dict[str, List[ObservationPacket]]:
    """Group ``tier == "control"`` packets by ``model_id``.

    Non-control packets are silently dropped — this function's contract is
    "give me the control set for each model in the stream". Empty input
    yields an empty dict; a stream with no control-tier packets yields
    an empty dict.
    """
    by_model: Dict[str, List[ObservationPacket]] = {}
    for p in stream:
        # Cosmic-Ray: Eq_Is and Eq_LtE on this comparison are EQUIVALENT
        # under the spec's closed tier set {"control", "low", "high"}.
        # Eq_Is: "control" is a compile-time interned literal and packets
        # carry tier as a literal → `is` agrees with `==`.
        # Eq_LtE: alphabetically "control" < "high" < "low", so `<= "control"`
        # selects only "control" — same set as `==`.
        if p.tier == "control":
            by_model.setdefault(p.model_id, []).append(p)
    return by_model


def select_controls_for_cell(
    by_model: Dict[str, List[ObservationPacket]],
    cell_packets: Sequence[ObservationPacket],
) -> List[ObservationPacket]:
    """Return the calibration set matching the cell's first packet's model.

    A cell with no packets gets no calibration (returns ``[]``); a cell
    whose model has no entry in ``by_model`` also gets ``[]``. The empty
    list flows through to the predicate ladder where the
    ``len(control) < min_calibration_n`` check converts it into
    ``INSUFFICIENT_OBSERVABILITY``.
    """
    if not cell_packets:
        return []
    model_id = cell_packets[0].model_id
    return list(by_model.get(model_id, []))


def select_controls_for_cell_pair(
    by_model_a: Dict[str, List[ObservationPacket]],
    by_model_b: Dict[str, List[ObservationPacket]],
    packets_a: Sequence[ObservationPacket],
    packets_b: Sequence[ObservationPacket],
) -> Tuple[List[ObservationPacket], List[ObservationPacket]]:
    """Convenience wrapper: select per-model controls for both observers."""
    return (
        select_controls_for_cell(by_model_a, packets_a),
        select_controls_for_cell(by_model_b, packets_b),
    )
