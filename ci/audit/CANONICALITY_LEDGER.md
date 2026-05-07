# L40 Canonicality Ledger

Generated: `2026-05-06T05:00:04+00:00`

## Verdict

- **Status:** CANONICAL
- **Cross-checks passed:** 8 / 8

## Cross-checks

| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | `thresholds_match_pre_reg` | ✅ PASS | all 6 threshold values present |
| 2 | `relation_class_complete` | ✅ PASS | all 8 RelationClass values mapped |
| 3 | `observation_packet_schema_version` | ✅ PASS | packet_schema_version field present |
| 4 | `mutation_ledger_green` | ✅ PASS | 0 RESIDUAL, 100.00% addressed |
| 5 | `hypothesis_program_complete` | ✅ PASS | all 30 H_IDs from pre-reg have test functions |
| 6 | `no_residual_either_session` | ✅ PASS | main session 0 RESIDUAL |
| 7 | `ray_configs_disjoint` | ✅ PASS | main excludes router; control targets router |
| 8 | `substrate_no_llm_imports` | ✅ PASS | no LLM imports in substrate |

## Inventory

| File | Role | Covered by | SHA256 | Bytes |
|------|------|------------|--------|-------|
| `ci/audit/MUTATION_LEDGER.md` | canonical mutation-coverage ledger | L40 cross-check | `1ab384a7e1bc8b2e…` | 8382 |
| `ci/audit/audit_observer.py` | predicate ladder (pre-reg §4.1, §4.2) | L30/L31, mutation | `7068d09887f50d7f…` | 15911 |
| `ci/audit/audit_result.py` | AuditResult schema | L30, mutation | `6d31a1fa0dab7371…` | 767 |
| `ci/audit/control_router.py` | per-(observer, model) calibration routing (pre-reg §6) | L30, mutation (separate) | `4fc7dcc43eac66c0…` | 2798 |
| `ci/audit/cosmic_ray_config.toml` | main mutation session config | L40 inventory | `e729de1e5c379755…` | 857 |
| `ci/audit/cosmic_ray_control.toml` | router-only mutation session config | L40 inventory | `01ee0a53ee77b012…` | 230 |
| `ci/audit/decision_rule.py` | RelationClass → PaperAction mapping | L30, mutation | `b6f39141f4c79d9a…` | 2049 |
| `ci/audit/main_dump.jsonl` | raw cosmic-ray dump (provenance) | L40 inventory | `11faeb7360af2d3d…` | 848419 |
| `ci/audit/observation_packet.py` | ObservationPacket schema v4.1 | L30, mutation | `a5736402b90bdaf1…` | 2294 |
| `ci/audit/observer.py` | typed Observer dataclass | L30, mutation | `8a96d2eb996d303a…` | 1666 |
| `ci/audit/relation_classes.py` | RelationClass enum | L30, mutation | `7b8a4aa6a27ea781…` | 609 |
| `ci/audit/tests/test_aaa_gold_standard.py` | test suite (test_aaa_gold_standard) | self | `696b2f30a2d65901…` | 27485 |
| `ci/audit/tests/test_audit_observer.py` | test suite (test_audit_observer) | self | `ffe884f53f6cb57b…` | 18282 |
| `ci/audit/tests/test_audit_result.py` | test suite (test_audit_result) | self | `113edba5c67709f1…` | 1812 |
| `ci/audit/tests/test_control_router.py` | test suite (test_control_router) | self | `b2deb7bbe34ca15d…` | 10062 |
| `ci/audit/tests/test_hypothesis_program.py` | test suite (test_hypothesis_program) | self | `ce481adc8b748b77…` | 103310 |
| `ci/audit/tests/test_invariant_spec_correspondence.py` | test suite (test_invariant_spec_correspondence) | self | `f7098dcbd0cd8501…` | 5612 |
| `ci/audit/tests/test_mutation_kills.py` | test suite (test_mutation_kills) | self | `2571dc15e379c821…` | 25566 |
| `ci/audit/tests/test_observation_packet.py` | test suite (test_observation_packet) | self | `4376106f79056b43…` | 7335 |
| `ci/audit/tests/test_observer.py` | test suite (test_observer) | self | `b52ec7f3b2c6e5a7…` | 1796 |
| `ci/audit/tests/test_purity.py` | test suite (test_purity) | self | `6e62053963e44c4b…` | 3000 |
| `ci/audit/tests/test_relation_classes.py` | test suite (test_relation_classes) | self | `fa24bfc5c67a94ce…` | 1159 |
| `supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md` | pre-registration (locked at first packet) | L30 / L40 | `13c4618d46caa588…` | 46001 |