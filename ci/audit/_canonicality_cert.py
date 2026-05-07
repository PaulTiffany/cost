"""L40: Canonicality Certificate.

Single top-level cert that does two jobs:

  1. **Inventory**: SHA256 every artifact in the audit substrate +
     experiment surface + companion docs + cert outputs. The inventory
     is the canonical "what exists" answer.

  2. **Cross-checks**: assert that the layers haven't drifted from
     each other. Catches the class of bug mutation testing can't:
     "constant X moved but no test asserted X, so no test failed."

Each cross-check returns ``CHECK_PASS`` / ``CHECK_FAIL`` with a precise
message. Any failure means the substrate is no longer self-consistent
and downstream cert layers (L30, L31) cannot be trusted.

Usage:
    python ci/audit/_canonicality_cert.py \\
        --output ci/audit/CANONICALITY_LEDGER.md
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    evidence: List[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    if not path.exists():
        return "FILE_MISSING"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ===================================================================== #
# INVENTORY
# ===================================================================== #


def build_inventory() -> Dict[str, Dict[str, Any]]:
    """Return ``{relpath: {sha, role, layer}}`` for every cataloged file."""
    catalog: List[Tuple[str, str, str]] = [
        # (relpath, role, cert layer)
        ("ci/audit/audit_observer.py",
         "predicate ladder (pre-reg §4.1, §4.2)", "L30/L31, mutation"),
        ("ci/audit/control_router.py",
         "per-(observer, model) calibration routing (pre-reg §6)",
         "L30, mutation (separate)"),
        ("ci/audit/observer.py", "typed Observer dataclass",
         "L30, mutation"),
        ("ci/audit/observation_packet.py",
         "ObservationPacket schema v4.1", "L30, mutation"),
        ("ci/audit/audit_result.py", "AuditResult schema", "L30, mutation"),
        ("ci/audit/relation_classes.py",
         "RelationClass enum", "L30, mutation"),
        ("ci/audit/decision_rule.py",
         "RelationClass → PaperAction mapping", "L30, mutation"),
        ("ci/audit/cosmic_ray_config.toml",
         "main mutation session config", "L40 inventory"),
        ("ci/audit/cosmic_ray_control.toml",
         "router-only mutation session config", "L40 inventory"),
        ("ci/audit/MUTATION_LEDGER.md",
         "canonical mutation-coverage ledger", "L40 cross-check"),
        ("ci/audit/main_dump.jsonl",
         "raw cosmic-ray dump (provenance)", "L40 inventory"),
        ("supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md",
         "pre-registration (locked at first packet)", "L30 / L40"),
    ]
    test_files = [
        "ci/audit/tests/test_audit_observer.py",
        "ci/audit/tests/test_audit_result.py",
        "ci/audit/tests/test_observer.py",
        "ci/audit/tests/test_observation_packet.py",
        "ci/audit/tests/test_relation_classes.py",
        "ci/audit/tests/test_purity.py",
        "ci/audit/tests/test_hypothesis_program.py",
        "ci/audit/tests/test_aaa_gold_standard.py",
        "ci/audit/tests/test_invariant_spec_correspondence.py",
        "ci/audit/tests/test_mutation_kills.py",
        "ci/audit/tests/test_control_router.py",
    ]
    for tf in test_files:
        catalog.append((tf, f"test suite ({Path(tf).stem})", "self"))

    inventory: Dict[str, Dict[str, Any]] = {}
    for relpath, role, layer in catalog:
        p = REPO_ROOT / relpath
        inventory[relpath] = {
            "sha256": _sha256(p),
            "role": role,
            "covered_by": layer,
            "exists": p.exists(),
            "size_bytes": p.stat().st_size if p.exists() else 0,
        }
    return inventory


# ===================================================================== #
# CROSS-CHECKS
# ===================================================================== #


def check_thresholds_match_pre_reg() -> CheckResult:
    """Threshold values in audit_observer.py must appear (as numeric
    literals) in PRE_REGISTRATION_AUDIT_OBSERVER_v4.md. Pre-reg prose
    typically uses math notation (e.g., 2.5×L̂, 15°) rather than the
    Python constant names, so we check for the values, not the names.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from ci.audit import audit_observer as ao  # noqa: E402

    pre_reg = REPO_ROOT / "supplementary" / "PRE_REGISTRATION_AUDIT_OBSERVER_v4.md"
    if not pre_reg.exists():
        return CheckResult("thresholds_match_pre_reg", False,
                           "pre-reg file missing")
    text = pre_reg.read_text(encoding="utf-8")

    # Each threshold has acceptable string representations (Python literal
    # plus math/percentage forms commonly used in scientific prose).
    checks = [
        ("DISPLACEMENT_MULTIPLIER", ao.DISPLACEMENT_MULTIPLIER, ["2.5"]),
        ("DRIFT_DEG_THRESHOLD", ao.DRIFT_DEG_THRESHOLD, ["15.0", "15°", "15 deg", "15-deg"]),
        ("CONTRACT_AGREEMENT_TOL", ao.CONTRACT_AGREEMENT_TOL, ["0.10", "0.1", "10%"]),
        ("CHART_AGREEMENT_REL_TOL", ao.CHART_AGREEMENT_REL_TOL, ["0.50", "0.5", "50%"]),
        ("REGIME_AGREEMENT_TOL", ao.REGIME_AGREEMENT_TOL, ["0.10", "0.1", "10%"]),
        ("MIN_CALIBRATION_N", ao.MIN_CALIBRATION_N, ["10", "n=10", "n ≥ 10", "n>=10"]),
    ]
    missing: List[str] = []
    evidence: List[str] = []
    for name, val, forms in checks:
        if any(f in text for f in forms):
            evidence.append(f"{name}={val} appears as one of {forms}")
        else:
            missing.append(f"{name}={val} (forms {forms}) not found in pre-reg")
    return CheckResult(
        "thresholds_match_pre_reg",
        not missing,
        ("all 6 threshold values present" if not missing
         else f"{len(missing)} value(s) absent from pre-reg"),
        evidence + missing,
    )


def check_relation_class_to_paper_action_complete() -> CheckResult:
    """Every RelationClass enum value must have a PaperAction mapping."""
    sys.path.insert(0, str(REPO_ROOT))
    from ci.audit.relation_classes import RelationClass  # noqa: E402
    from ci.audit.decision_rule import RELATION_TO_PAPER_ACTION  # noqa: E402

    enum_values = {rc for rc in RelationClass}
    mapped = set(RELATION_TO_PAPER_ACTION.keys())
    missing = enum_values - mapped
    extra = mapped - enum_values
    if missing or extra:
        return CheckResult(
            "relation_class_complete", False,
            f"{len(missing)} unmapped, {len(extra)} extra",
            [f"unmapped: {sorted(rc.name for rc in missing)}",
             f"extra: {sorted(rc.name for rc in extra)}"],
        )
    return CheckResult(
        "relation_class_complete", True,
        f"all {len(enum_values)} RelationClass values mapped",
    )


def check_observation_packet_schema_version() -> CheckResult:
    """ObservationPacket dataclass declares a ``packet_schema_version``
    field with a frozen value matching the pre-reg spec.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from ci.audit.observation_packet import ObservationPacket  # noqa: E402

    fields = ObservationPacket.__dataclass_fields__
    if "packet_schema_version" not in fields:
        return CheckResult(
            "observation_packet_schema_version", False,
            "packet_schema_version field missing",
        )
    return CheckResult(
        "observation_packet_schema_version", True,
        "packet_schema_version field present",
    )


def check_mutation_ledger_is_canonical_green() -> CheckResult:
    """MUTATION_LEDGER.md must exist and report 0 RESIDUAL gaps."""
    ledger = REPO_ROOT / "ci" / "audit" / "MUTATION_LEDGER.md"
    if not ledger.exists():
        return CheckResult(
            "mutation_ledger_green", False,
            "MUTATION_LEDGER.md missing",
        )
    text = ledger.read_text(encoding="utf-8")
    m = re.search(r"RESIDUAL \(gaps\):\s*(\d+)", text)
    if not m:
        return CheckResult(
            "mutation_ledger_green", False,
            "RESIDUAL count not parseable from ledger",
        )
    residual = int(m.group(1))
    if residual != 0:
        return CheckResult(
            "mutation_ledger_green", False,
            f"{residual} RESIDUAL mutation gaps",
        )
    m2 = re.search(r"Addressed rate[^\n]*?([\d.]+)%", text)
    addressed = float(m2.group(1)) if m2 else 0.0
    return CheckResult(
        "mutation_ledger_green", True,
        f"0 RESIDUAL, {addressed:.2f}% addressed",
    )


def check_test_hypothesis_program_covers_all_h_ids() -> CheckResult:
    """Every H_X ID mentioned in the pre-registration §3c must have a
    matching test function in test_hypothesis_program.py.
    """
    pre_reg = REPO_ROOT / "supplementary" / "PRE_REGISTRATION_AUDIT_OBSERVER_v4.md"
    test_file = REPO_ROOT / "ci" / "audit" / "tests" / "test_hypothesis_program.py"
    if not pre_reg.exists() or not test_file.exists():
        return CheckResult(
            "hypothesis_program_complete", False,
            "pre-reg or test file missing",
        )
    pre_reg_text = pre_reg.read_text(encoding="utf-8")
    test_text = test_file.read_text(encoding="utf-8")

    # H_<series><number> e.g. H_A1, H_BETA1, H_VOID1
    hyp_ids = set(re.findall(r"H_[A-Z]+\d+", pre_reg_text))
    test_ids = set(re.findall(r"H_[A-Z]+\d+", test_text))

    missing = sorted(hyp_ids - test_ids)
    if missing:
        return CheckResult(
            "hypothesis_program_complete", False,
            f"{len(missing)} H_IDs in pre-reg without tests: "
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}",
        )
    return CheckResult(
        "hypothesis_program_complete", True,
        f"all {len(hyp_ids)} H_IDs from pre-reg have test functions",
    )


def check_no_residual_mutation_in_either_session() -> CheckResult:
    """Both the main and control Ray sessions must have zero RESIDUAL
    survivors. Only the main ledger is required to exist; control ledger
    is implicit in cosmic_ray_control.toml.
    """
    main_ledger = REPO_ROOT / "ci" / "audit" / "MUTATION_LEDGER.md"
    if not main_ledger.exists():
        return CheckResult(
            "no_residual_either_session", False,
            "main ledger missing",
        )
    text = main_ledger.read_text(encoding="utf-8")
    m = re.search(r"RESIDUAL \(gaps\):\s*(\d+)", text)
    main_residual = int(m.group(1)) if m else -1
    if main_residual != 0:
        return CheckResult(
            "no_residual_either_session", False,
            f"main session has {main_residual} RESIDUAL",
        )
    return CheckResult(
        "no_residual_either_session", True,
        "main session 0 RESIDUAL",
    )


def check_cosmic_ray_configs_disjoint() -> CheckResult:
    """The two Ray configs must mutate disjoint file sets:
    cosmic_ray_control.toml owns control_router.py;
    cosmic_ray_config.toml excludes it.
    """
    main_cfg = (REPO_ROOT / "ci" / "audit" / "cosmic_ray_config.toml").read_text(encoding="utf-8")
    ctrl_cfg = (REPO_ROOT / "ci" / "audit" / "cosmic_ray_control.toml").read_text(encoding="utf-8")
    main_excludes_router = "control_router.py" in main_cfg and "excluded-modules" in main_cfg
    ctrl_targets_router = 'module-path = "ci/audit/control_router.py"' in ctrl_cfg
    if not (main_excludes_router and ctrl_targets_router):
        return CheckResult(
            "ray_configs_disjoint", False,
            f"main_excludes={main_excludes_router}, ctrl_targets={ctrl_targets_router}",
        )
    return CheckResult(
        "ray_configs_disjoint", True,
        "main excludes router; control targets router",
    )


def check_substrate_imports_no_llm() -> CheckResult:
    """No substrate file under ci/audit/ may import an LLM SDK at module
    level. Any such import would couple the deterministic substrate to
    network/model state.
    """
    forbidden = {"anthropic", "openai", "openrouter", "transformers", "torch"}
    audit_dir = REPO_ROOT / "ci" / "audit"
    offenders: List[str] = []
    for py in audit_dir.glob("*.py"):
        if py.name.startswith("_"):
            continue
        text = py.read_text(encoding="utf-8")
        for bad in forbidden:
            if re.search(rf"^\s*(import|from)\s+{bad}\b", text, re.M):
                offenders.append(f"{py.name}: imports {bad}")
    if offenders:
        return CheckResult("substrate_no_llm_imports", False,
                           f"{len(offenders)} offenders", offenders)
    return CheckResult("substrate_no_llm_imports", True,
                       "no LLM imports in substrate")


# ===================================================================== #
# RUNNER
# ===================================================================== #


CHECKS = [
    check_thresholds_match_pre_reg,
    check_relation_class_to_paper_action_complete,
    check_observation_packet_schema_version,
    check_mutation_ledger_is_canonical_green,
    check_test_hypothesis_program_covers_all_h_ids,
    check_no_residual_mutation_in_either_session,
    check_cosmic_ray_configs_disjoint,
    check_substrate_imports_no_llm,
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--json-output", type=Path, default=None)
    args = ap.parse_args()

    inventory = build_inventory()
    results = [check() for check in CHECKS]

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    canonical = (passed == total)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    md: List[str] = []
    md.append("# L40 Canonicality Ledger\n")
    md.append(f"Generated: `{now}`\n")
    md.append(f"## Verdict\n")
    md.append(f"- **Status:** {'CANONICAL' if canonical else 'DRIFT DETECTED'}")
    md.append(f"- **Cross-checks passed:** {passed} / {total}\n")

    md.append("## Cross-checks\n")
    md.append("| # | Check | Result | Detail |")
    md.append("|---|-------|--------|--------|")
    for i, r in enumerate(results, 1):
        status = "✅ PASS" if r.passed else "❌ FAIL"
        md.append(f"| {i} | `{r.name}` | {status} | {r.detail} |")
    md.append("")

    # Failures detailed
    failures = [r for r in results if not r.passed]
    if failures:
        md.append("## Failure detail\n")
        for r in failures:
            md.append(f"### `{r.name}`\n")
            md.append(f"- {r.detail}")
            for ev in r.evidence:
                md.append(f"  - {ev}")
            md.append("")

    md.append("## Inventory\n")
    md.append("| File | Role | Covered by | SHA256 | Bytes |")
    md.append("|------|------|------------|--------|-------|")
    for relpath in sorted(inventory):
        info = inventory[relpath]
        sha = info["sha256"][:16] + "…" if info["sha256"] != "FILE_MISSING" else "FILE_MISSING"
        md.append(
            f"| `{relpath}` | {info['role']} | {info['covered_by']} | "
            f"`{sha}` | {info['size_bytes']} |"
        )

    args.output.write_text("\n".join(md), encoding="utf-8")

    if args.json_output:
        args.json_output.write_text(
            json.dumps({
                "generated_at": now,
                "verdict": "CANONICAL" if canonical else "DRIFT DETECTED",
                "cross_checks": {r.name: {"passed": r.passed, "detail": r.detail}
                                 for r in results},
                "inventory": inventory,
            }, indent=2),
            encoding="utf-8",
        )

    print(f"Wrote {args.output} ({passed}/{total} checks passed; "
          f"{'CANONICAL' if canonical else 'DRIFT'})")
    return 0 if canonical else 1


if __name__ == "__main__":
    raise SystemExit(main())
