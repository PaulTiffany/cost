"""AAA tests for the RelationClass enum (pre-reg v4 §4)."""

from ci.audit.relation_classes import RelationClass


def test_eight_relation_classes_exist():
    # Arrange
    expected = {
        "AGREEMENT",
        "LOCAL_CONTRACT_DIVERGENCE",
        "CHART_TRANSITION",
        "PIVOT_DISAGREEMENT",
        "VERIFIER_SURFACE_MISMATCH",
        "SMOOTH_SUCCESS_EXCEPTION",
        "TRUE_CERTIFICATE_REFUTATION",
        "INSUFFICIENT_OBSERVABILITY",
    }

    # Act
    actual_names = {member.name for member in RelationClass}

    # Assert
    assert len(RelationClass) == 8
    assert actual_names == expected


def test_relation_class_values_are_unique_and_match_pre_reg():
    # Arrange
    expected_values = {
        "agreement",
        "local_contract_divergence",
        "chart_transition",
        "pivot_disagreement",
        "verifier_surface_mismatch",
        "smooth_success_exception",
        "true_certificate_refutation",
        "insufficient_observability",
    }

    # Act
    actual_values = {member.value for member in RelationClass}

    # Assert
    assert actual_values == expected_values
    assert len(actual_values) == 8
