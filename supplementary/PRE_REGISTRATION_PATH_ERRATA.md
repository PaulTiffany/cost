# Pre-Registration Path Errata

This errata documents path drifts between `PRE_REGISTRATION_AUDIT_OBSERVER_v4.md` and the current repository layout. The pre-registration file itself is methodologically locked at first observation-packet emission; its SHA256 is recorded in every emitted packet's `pre_reg_hash` field and is verified by L31 audit-observer runtime check. Modifying the pre-reg file would invalidate the chain of custody.

The path drifts below are documentation-level only. Methodology, thresholds, decision rule, hypothesis program, and packet schema all remain exactly as locked.

## Drift 1: Per-channel observation packet filenames

**Pre-reg reference (line 506):** `supplementary/experiments/outputs/audit_v4/claude_observation_packets.jsonl`

**Current location:** `supplementary/experiments/outputs/audit_v4/observation_packets.jsonl` (single combined file produced by the unified orchestrator)

**Resolution:** The orchestrator at `supplementary/experiments/audit_experiment_orchestrator.py` writes a single combined observation file rather than one per channel. The combined file contains all packets from both observers (B_claude and B_openweight). Per-run timestamped copies live under `supplementary/experiments/outputs/audit_v4/run_<UTC>/observation_packets.jsonl` for full provenance.

## Drift 2: Test paths shortened

**Pre-reg reference (line 155):** `tests/test_purity.py`

**Current location:** `ci/audit/tests/test_purity.py`

**Pre-reg reference (line 159):** `tests/test_audit_observer.py`

**Current location:** `ci/audit/tests/test_audit_observer.py`

**Resolution:** The audit substrate's tests live under `ci/audit/tests/` (the substrate-local test directory) rather than at a top-level `tests/`. The pre-reg used a sibling-relative shorthand that does not match the repository's actual test layout. All tests named in the pre-reg's hypothesis program section exist at the `ci/audit/tests/` location.

## Verification

A reviewer following any of the above paths and hitting a 404 should consult this errata for the current location. The cert layer `ci/reviewer_path_chain_check.py` flags these specific drifts and confirms that the corrected paths resolve to existing files.
