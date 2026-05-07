"""RelationClass enum (locked, pre-declared per pre-reg v4 §4)."""

from enum import Enum


class RelationClass(Enum):
    """Eight relation classes locked in pre-registration v4 §4."""

    AGREEMENT = "agreement"
    LOCAL_CONTRACT_DIVERGENCE = "local_contract_divergence"
    CHART_TRANSITION = "chart_transition"
    PIVOT_DISAGREEMENT = "pivot_disagreement"
    VERIFIER_SURFACE_MISMATCH = "verifier_surface_mismatch"
    SMOOTH_SUCCESS_EXCEPTION = "smooth_success_exception"
    TRUE_CERTIFICATE_REFUTATION = "true_certificate_refutation"
    INSUFFICIENT_OBSERVABILITY = "insufficient_observability"
