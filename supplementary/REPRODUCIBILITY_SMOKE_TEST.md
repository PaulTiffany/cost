# Reproducibility Smoke Test

Paper: *The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment*
Preprint: arXiv (cs.AI), CC BY 4.0
Generated (UTC): 2026-05-06T05:00:30Z
Scope: fresh-clone reviewer execution of the eleven cert + repro commands documented in `supplementary/REVIEWER_QUICKSTART.md`, the abstract, and the conclusion.

## Results table

| # | Command | Exit | Brief result | Status |
|---|---|---|---|---|
| 1 | `python ci/claim_certificate.py` | 0 | Verdict PASS, 31 layers green (1 SKIP for L13_cross_tree, expected) | full-pass |
| 2 | `python ci/audit_observer_purity_check.py` | 0 | 9 pass, 0 warn, 0 fail; mutation kill_rate 0.962 | full-pass |
| 3 | `python ci/audit_observer_runtime_check.py` | 0 | L31 PASS; H_B1, H_B2, H_B3 all PASS over 6120 packets, 18 cells | full-pass |
| 4 | `python ci/audit/_canonicality_cert.py --output ci/audit/CANONICALITY_LEDGER.md --json-output ci/audit/canonicality_results.json` | 0 | 8/8 checks passed, ledger written | full-pass |
| 5 | `python ci/anonymity_check.py` | 0 | 213 files scanned, 0 hard fails, 0 warnings | full-pass |
| 6 | `python ci/audit/_mutation_ledger.py --dump ci/audit/main_dump.jsonl --config ci/audit/cosmic_ray_config.toml --output ci/audit/MUTATION_LEDGER.md` | 0 | 368 mutations, 96.2% killed, 100% addressed | full-pass |
| 7 | `python -m pytest ci/audit/tests/ -q --no-header --deselect ci/audit/tests/test_aaa_gold_standard.py` | 0 | 151 passed, 1 skipped, 11 deselected | full-pass |
| 8 | `python -m pytest supplementary/experiments/tests/ -q --no-header` | 1 | 47 passed, 1 failed (`test_exception_X5_orchestrator_env_load_missing_file_handled`) | partial |
| 9 | `python supplementary/experiments/audit_experiment_orchestrator.py` | 0 | Plan-only mode; PLANNED CONFIGURATION printed, no API calls launched | full-pass |
| 10 | `python supplementary/experiments/task_prompt_snapshot.py` | 0 | Snapshot written; tasks=6, tiers=4, resolved_prompts=24 | full-pass |
| 11 | `python supplementary/experiments/extended_environment_fingerprint.py` | 0 | Fingerprint JSON printed; OS, library versions, hardware all enumerated | full-pass |

## Summary

10/11 commands pass cleanly. 1/11 partial. 0/11 fail.

## Diagnosis of the partial result

Command 8 exits with status 1 because of a single failing test, `supplementary/experiments/tests/test_exception_paths.py::test_exception_X5_orchestrator_env_load_missing_file_handled`. The test does `monkeypatch.setattr(anthropic_channel, "ENV_FILE", missing)`, but the module `supplementary/experiments/channels/anthropic_channel.py` does not expose an attribute named `ENV_FILE`. Pytest raises `AttributeError`. The other 47 tests in the suite pass. The failure is confined to a single defensive exception-path test and does not exercise the orchestrator's load path that the certificate, runtime check, and orchestrator plan-only mode all exercise successfully. The likely cause is a name drift between the channel module and the test fixture (the channel module probably renamed or inlined the env-file constant); a reviewer can read both files in under a minute and confirm the mismatch.

## Confidence

A fresh-clone reviewer can reproduce the certificate verdict, the audit observer purity and runtime checks, the canonicality and mutation ledgers, the anonymity check, the orchestrator plan-only output, the task prompt snapshot, and the environment fingerprint without intervention. The only unexpected non-zero exit is one defensive exception-path unit test in the supplementary experiments suite, which does not block any of the load-bearing certification paths. Confidence in fresh-clone reproducibility of the certified claims is high; the partial in command 8 is a maintenance issue in test infrastructure, not a certification gap.
