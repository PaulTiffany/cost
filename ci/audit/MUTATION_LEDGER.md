# Mutation Coverage Ledger

Generated: `2026-05-06T05:00:13+00:00`

## Aggregate

- **Total mutations:** 368
- **Killed (test failure):** 316
- **Incompetent (broken AST — counts as killed):** 38
- **Effective killed:** 354
- **Survived:** 14
  - EQUIVALENT (documented): 14
  - DEAD-CODE (documented): 0
  - RESIDUAL (gaps): 0
- **Other incomplete:** 0
- **Kill rate (effective_killed / completed):** 96.20%
- **Addressed rate (killed + documented / total):** 100.00%

## Provenance

SHA256 of every artifact at generation time:

- `ci/audit/__init__.py`: `7404bc0adb0e079249993600868e08289acce53b801f2b4a7385cfac68509897`
- `ci/audit/_canonicality_cert.py`: `d74d07e97331df12b65193fc96af035487cd8129971a8c907a10b99681540a40`
- `ci/audit/_inspect_runtime.py`: `e5af0bf936802e7e7b4156cd7522106d437f43fb75a220cf4762e0d0eea64754`
- `ci/audit/_run_tests_utf8.py`: `a245c17dc495cfe3ee5e8d4bd4785095e5b98f0db12d88af218692656c5fd0bf`
- `ci/audit/audit_observer.py`: `7068d09887f50d7f88e4a642be7cc3f4b5a81f80d20c91a6eec351079b3243bc`
- `ci/audit/audit_result.py`: `6d31a1fa0dab7371b35cb2123fa0a91d6bc521a7006ff29250c56700819fc888`
- `ci/audit/control_router.py`: `4fc7dcc43eac66c0aa6d0c851e78f0ae5a2e05a4a017d1a002c1f980b73c65ef`
- `ci/audit/cosmic_ray_config.toml`: `e729de1e5c3797553dc55dd75d78eb6e2c5d75857aba4405b60a6b2e3bc9a37f`
- `ci/audit/decision_rule.py`: `b6f39141f4c79d9a1e2718bfd1076862f2a13a2287dee4069f20f7755964e9a8`
- `ci/audit/main_dump.jsonl`: `11faeb7360af2d3d26981099d8655632c4ad36bc0739a5d8bbc34053305b9c6e`
- `ci/audit/observation_packet.py`: `a5736402b90bdaf18d475f80dcc79d14421d6a400bfb2849a252884f1cd81dd9`
- `ci/audit/observer.py`: `8a96d2eb996d303a738f61d91fa896c96b7f6ae0ecfce8694a4eb9dba431ab31`
- `ci/audit/relation_classes.py`: `7b8a4aa6a27ea7810e055cca5b1c105e08b53a4dfd422c60e12c96e7d4d39354`
- `ci/audit/tests/test_aaa_gold_standard.py`: `696b2f30a2d6590176af3a351c1490905dcd990dd02e22734fac86676b24dc8e`
- `ci/audit/tests/test_audit_observer.py`: `ffe884f53f6cb57bba1f48256425d5ed902888a73a9bcc46078b13f3c4146773`
- `ci/audit/tests/test_audit_result.py`: `113edba5c67709f1fad187f0ebf7efc1254276cda2a09f3ff98770b001b3db2e`
- `ci/audit/tests/test_control_router.py`: `b2deb7bbe34ca15d53eee05fce344393d452b8c82417c8a699ba310102138389`
- `ci/audit/tests/test_hypothesis_program.py`: `ce481adc8b748b771ec7040d4806454465efd8d9acd0655f51e1720c8c4a704b`
- `ci/audit/tests/test_invariant_spec_correspondence.py`: `f7098dcbd0cd850115e3ed60f49af7b130edf24be3a96b32161f9e4e82ea06ba`
- `ci/audit/tests/test_mutation_kills.py`: `2571dc15e379c8216e6b4dbdcd0859a2d06871184a3dde202b26b9c6fa1f1a81`
- `ci/audit/tests/test_observation_packet.py`: `4376106f79056b438cd719e74f5da78c3ea8322b50e6661e1deb48eccb4033ce`
- `ci/audit/tests/test_observer.py`: `b52ec7f3b2c6e5a7fee1f11dea96d6aecf80d606b85140cafedc01a265063b04`
- `ci/audit/tests/test_purity.py`: `6e62053963e44c4be5a0b7984f4fd4e2e0b1558419c6ed7e099fff25471a4f0f`
- `ci/audit/tests/test_relation_classes.py`: `fa24bfc5c67a94ce7c7c61c9fe7f4a3156b88fd005e5d10b5e48d85197d6f455`

## Survivors

| File | Line | Operator | Class | Justification |
|------|------|----------|-------|---------------|
| `audit_observer.py` | 57 | `ReplaceFalseWithTrue` | **EQUIVALENT** | Cosmic-Ray: ReplaceFalseWithTrue on the .get() default is EQUIVALENT. ObservationPacket.verifier_dict() always populates pass_both (enforced by the dataclass __post_init__), so the default never fires. |
| `audit_observer.py` | 83 | `ReplaceComparisonOperator_Eq_Is` | **EQUIVALENT** | Cosmic-Ray: Eq_Is and Eq_LtE on `p.tier == "high"` are EQUIVALENT under the closed tier set {"control", "low", "high"}. Eq_Is: "high" is an interned string literal; tests pass tier as a literal, so `is` and `==` agree. Eq_LtE: alphabetically "control" < "high" < "low", so `tier <= "high"` matches {"control", "high"}; "control" is filtered upstream and never reaches this predicate, so the observable set collapses to {"high"} — same as `==`. |
| `audit_observer.py` | 83 | `ReplaceComparisonOperator_Eq_LtE` | **EQUIVALENT** | Cosmic-Ray: Eq_Is and Eq_LtE on `p.tier == "high"` are EQUIVALENT under the closed tier set {"control", "low", "high"}. Eq_Is: "high" is an interned string literal; tests pass tier as a literal, so `is` and `==` agree. Eq_LtE: alphabetically "control" < "high" < "low", so `tier <= "high"` matches {"control", "high"}; "control" is filtered upstream and never reaches this predicate, so the observable set collapses to {"high"} — same as `==`. |
| `audit_observer.py` | 126 | `ReplaceComparisonOperator_Eq_LtE` | **EQUIVALENT** | Cosmic-Ray: Eq_LtE on `denom == 0.0` is EQUIVALENT — denom is max(la, lb) and L_hat values are non-negative by construction (mean of non-negative chunk displacements), so denom <= 0 ↔ denom == 0. |
| `audit_observer.py` | 130 | `ReplaceComparisonOperator_Eq_GtE` | **EQUIVALENT** | Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `la == lb` are all EQUIVALENT inside this branch — the branch is reached only when both la and lb are 0.0, so all comparators agree. |
| `audit_observer.py` | 130 | `ReplaceComparisonOperator_Eq_Is` | **EQUIVALENT** | Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `la == lb` are all EQUIVALENT inside this branch — the branch is reached only when both la and lb are 0.0, so all comparators agree. |
| `audit_observer.py` | 130 | `ReplaceComparisonOperator_Eq_LtE` | **EQUIVALENT** | Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `la == lb` are all EQUIVALENT inside this branch — the branch is reached only when both la and lb are 0.0, so all comparators agree. |
| `audit_observer.py` | 232 | `ReplaceComparisonOperator_Eq_GtE` | **EQUIVALENT** | §4.2 second branch: high-tier smooth-success exceptions. Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `tier == "high"` are EQUIVALENT — Eq_LtE collapses to == under the closed tier set (see classify_packet); Eq_GtE would also enter for tier="low", but the inner classify_packet still filters via == "high", so `exc_a` and `exc_b` are empty and the branch returns to the ladder unchanged. |
| `audit_observer.py` | 232 | `ReplaceComparisonOperator_Eq_Is` | **EQUIVALENT** | §4.2 second branch: high-tier smooth-success exceptions. Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `tier == "high"` are EQUIVALENT — Eq_LtE collapses to == under the closed tier set (see classify_packet); Eq_GtE would also enter for tier="low", but the inner classify_packet still filters via == "high", so `exc_a` and `exc_b` are empty and the branch returns to the ladder unchanged. |
| `audit_observer.py` | 232 | `ReplaceComparisonOperator_Eq_LtE` | **EQUIVALENT** | §4.2 second branch: high-tier smooth-success exceptions. Cosmic-Ray: Eq_GtE / Eq_Is / Eq_LtE on `tier == "high"` are EQUIVALENT — Eq_LtE collapses to == under the closed tier set (see classify_packet); Eq_GtE would also enter for tier="low", but the inner classify_packet still filters via == "high", so `exc_a` and `exc_b` are empty and the branch returns to the ladder unchanged. |
| `audit_observer.py` | 357 | `ReplaceComparisonOperator_Eq_Is` | **EQUIVALENT** | Cosmic-Ray: Eq_Is and Eq_LtE on the two `tier == "control"` filters below are EQUIVALENT under the closed tier set (see partition_controls_by_model in control_router.py for the same justification: only "control" sorts <= "control" alphabetically among the three valid tiers). |
| `audit_observer.py` | 357 | `ReplaceComparisonOperator_Eq_LtE` | **EQUIVALENT** | Cosmic-Ray: Eq_Is and Eq_LtE on the two `tier == "control"` filters below are EQUIVALENT under the closed tier set (see partition_controls_by_model in control_router.py for the same justification: only "control" sorts <= "control" alphabetically among the three valid tiers). |
| `audit_observer.py` | 362 | `ReplaceComparisonOperator_Eq_Is` | **EQUIVALENT** | Cosmic-Ray: Eq_Is and Eq_LtE on the two `tier == "control"` filters below are EQUIVALENT under the closed tier set (see partition_controls_by_model in control_router.py for the same justification: only "control" sorts <= "control" alphabetically among the three valid tiers). |
| `audit_observer.py` | 362 | `ReplaceComparisonOperator_Eq_LtE` | **EQUIVALENT** | Cosmic-Ray: Eq_Is and Eq_LtE on the two `tier == "control"` filters below are EQUIVALENT under the closed tier set (see partition_controls_by_model in control_router.py for the same justification: only "control" sorts <= "control" alphabetically among the three valid tiers). |
