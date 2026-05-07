"""Decision rule mapping RelationClass -> PaperAction (pre-reg v4 §9).

Each RelationClass maps to exactly one PaperAction per the locked decision tree.
This module is part of the audit substrate and contains no LLM imports, no
model-weight access, and no unrestricted I/O. The mapping is total over the
RelationClass enum (verified by test_H_D1_decision_rule_total_over_relation_class_enum).
"""

from enum import Enum
from typing import Dict

from .relation_classes import RelationClass


class PaperAction(Enum):
    """Paper-level actions triggered by per-cell or stream-level audit results.

    Locked to the §9 decision tree. Adding a new action requires re-locking
    the pre-registration.
    """

    KEEP_HEADLINE = "keep_headline"
    ADD_MEASUREMENT_TABLE = "add_measurement_table"
    REFRAME_PER_FAMILY_L_HAT = "reframe_per_family_l_hat"
    SCOPE_TO_OPEN_WEIGHT = "scope_to_open_weight"
    MANDATORY_REWRITE = "mandatory_rewrite"
    FOOTNOTE_EXCEPTION = "footnote_exception"
    DEFER = "defer"
    INVESTIGATE_VERIFIER = "investigate_verifier"


# Total mapping: every RelationClass -> exactly one PaperAction (pre-reg §9).
RELATION_TO_PAPER_ACTION: Dict[RelationClass, PaperAction] = {
    RelationClass.AGREEMENT: PaperAction.ADD_MEASUREMENT_TABLE,
    RelationClass.LOCAL_CONTRACT_DIVERGENCE: PaperAction.SCOPE_TO_OPEN_WEIGHT,
    RelationClass.CHART_TRANSITION: PaperAction.REFRAME_PER_FAMILY_L_HAT,
    RelationClass.PIVOT_DISAGREEMENT: PaperAction.SCOPE_TO_OPEN_WEIGHT,
    RelationClass.VERIFIER_SURFACE_MISMATCH: PaperAction.INVESTIGATE_VERIFIER,
    RelationClass.SMOOTH_SUCCESS_EXCEPTION: PaperAction.FOOTNOTE_EXCEPTION,
    RelationClass.TRUE_CERTIFICATE_REFUTATION: PaperAction.MANDATORY_REWRITE,
    RelationClass.INSUFFICIENT_OBSERVABILITY: PaperAction.DEFER,
}


def apply_decision_rule(relation_class: RelationClass) -> PaperAction:
    """Look up the paper action for a relation class (pre-reg §9).

    Deterministic and total over RelationClass.
    """
    return RELATION_TO_PAPER_ACTION[relation_class]
