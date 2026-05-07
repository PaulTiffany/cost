"""ObservationPacket schema, frozen, exact match to pre-reg v4 §3.

Schema v4.1 (additive expansion of v4.0): adds five optional provenance
fields (api_request_id, actual_token_usage, stop_reason,
openrouter_provider, wall_time_ms). All default to None for backward
compatibility; v4.0 packets remain valid construction inputs.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


PACKET_SCHEMA_VERSION = "v4.1"


@dataclass(frozen=True)
class ObservationPacket:
    """One typed observation per (model, task, tier, trial) cell.

    Schema is locked at pre-reg v4 §3, additively expanded to v4.1 with
    five optional provenance fields. v4.0 field names, types, and order
    are preserved; the new fields are appended with `= None` defaults so
    existing v4.0 packet JSON loads cleanly.
    """

    packet_schema_version: str
    observer_id: str
    model_id: str
    api_model_snapshot: str
    task_id: str
    tier: str
    trial_idx: int
    prompt_text: str
    prompt_hash: str
    output_text: str
    output_hash: str
    chunk_trace: tuple
    n_chunks: int
    max_chunk_displacement: float
    mean_chunk_displacement: float
    max_drift_deg: float
    verifier_result: tuple
    verifier_hash: str
    timestamp_utc: str
    encoder_id: str
    encoder_package_version: str
    pre_reg_hash: str
    manifest_hash: str
    error: Optional[str]
    # --- v4.1 additive provenance fields (all Optional, default None) ---
    api_request_id: Optional[str] = None
    actual_token_usage: Optional[Dict[str, int]] = field(default=None)
    stop_reason: Optional[str] = None
    openrouter_provider: Optional[str] = None
    wall_time_ms: Optional[float] = None

    def verifier_dict(self) -> dict:
        """Return verifier_result re-projected as a dict.

        verifier_result is stored as a tuple of (key, value) pairs to
        preserve dataclass hashability. This helper unpacks it.
        """
        return dict(self.verifier_result)

    def chunk_trace_list(self) -> list:
        """Return chunk_trace as a list of dicts.

        chunk_trace is stored as a tuple of tuples-of-pairs to keep the
        packet hashable; this helper reconstructs the dict-per-chunk view.
        """
        return [dict(c) for c in self.chunk_trace]
