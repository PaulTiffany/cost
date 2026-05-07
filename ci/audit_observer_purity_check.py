#!/usr/bin/env python3
"""
audit_observer_purity_check.py - Cert layer L30_audit_observer_purity.

Hard-asserts the structural integrity of the deterministic audit substrate
at ``ci/audit/`` against pre-registration v4 (see
``supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md``). The audit observer
must remain LLM-free, schema-locked, and behaviorally green so the headline
under O_audit_v1 ("0/4,272 smooth-regime refutations" et al.) ships with
mechanical backing rather than asserted-by-fiat constants.

Checks performed
----------------
1. no_llm_imports
   AST-grep every .py file under ci/audit/ (excluding tests/) and confirm
   none import anthropic, openai, openrouter, transformers, or torch.
   Hard-fail on any offender.

2. observation_packet_schema
   Import ci/audit/observation_packet.py's ObservationPacket, enumerate
   dataclass fields, assert exactly the 24 fields named in pre-reg v4 §3
   (in declaration order) are present.

3. audit_result_schema
   Import ci/audit/audit_result.py's AuditResult, assert it is a frozen
   dataclass and that the expected field names are present.

4. relation_class_enum
   Import RelationClass and confirm exactly the 8 values listed in
   pre-reg v4 §4.

5. hypothesis_program_pytest_green
   Subprocess-invoke ``pytest ci/audit/tests/ -q``; assert exit code 0.

6. aaa_gold_standard_green
   Subprocess-invoke ``pytest ci/audit/tests/test_aaa_gold_standard.py -q``;
   assert exit code 0.

7. mutation_test_ledger (advisory)
   If ``ci/audit/mutation_test_results.json`` exists, parse and report
   kill_rate; if absent, emit a WARN (not FAIL).

8. hypothesis_program_completeness
   Programmatically re-runs the
   ``TestHypothesisProgramCompleteness::test_every_hypothesis_in_pre_reg_has_a_named_pytest_function``
   check by importing the test module and confirming every name in
   ``REQUIRED_HYPOTHESIS_TEST_NAMES`` resolves to a pytest function.

This script is itself pure stdlib + the audit substrate it audits. No
LLM-API import. No network. The only subprocess invocations are pytest
runs of the substrate's own test suite.

Output
------
``ci/audit_observer_purity_results.json``::

  {
    "check": "audit_observer_purity",
    "summary": {"hard_fail_count": N, "warn_count": N,
                "pass_count": N, "passed": bool},
    "checks": [
        {"name": "...", "status": "PASS|FAIL|WARN", "detail": "..."},
        ...
    ],
    "ran_at_utc": "..."
  }

Exit codes
----------
  0  no hard failures
  1  one or more hard failures
"""

from __future__ import annotations
import sys as _sys  # UTF-8 stdout (Windows cp1252 mojibake fix)
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(_sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

import argparse
import ast
import datetime
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from dataclasses import fields as dataclass_fields, is_dataclass
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
AUDIT_DIR = SCRIPT_DIR / "audit"
AUDIT_TESTS_DIR = AUDIT_DIR / "tests"
MUTATION_LEDGER = AUDIT_DIR / "mutation_test_results.json"
RESULTS_JSON = SCRIPT_DIR / "audit_observer_purity_results.json"

# Pre-reg-referenced docs that live outside ci/audit/. Hashes are pinned in
# the results JSON so a downstream cert can detect drift without itself
# reaching into supplementary/.
COMPANION_DOC_PATHS = (
    "supplementary/PRE_REGISTRATION_AUDIT_OBSERVER_v4.md",
    "supplementary/AUDIT_OBSERVER_INVARIANT_MAP.md",
    "ci/audit/README.md",
    "ci/audit/cosmic_ray_config.toml",
    "ci/audit/mutation_test_results.json",
    "ci/audit/decision_rule.py",
)

FORBIDDEN_LLM_MODULES = {
    "anthropic",
    "openai",
    "openrouter",
    "transformers",
    "torch",
}

# Pre-reg v4 §3: 24 fields, in declaration order.
EXPECTED_OBSERVATION_PACKET_FIELDS = (
    "packet_schema_version",
    "observer_id",
    "model_id",
    "api_model_snapshot",
    "task_id",
    "tier",
    "trial_idx",
    "prompt_text",
    "prompt_hash",
    "output_text",
    "output_hash",
    "chunk_trace",
    "n_chunks",
    "max_chunk_displacement",
    "mean_chunk_displacement",
    "max_drift_deg",
    "verifier_result",
    "verifier_hash",
    "timestamp_utc",
    "encoder_id",
    "encoder_package_version",
    "pre_reg_hash",
    "manifest_hash",
    "error",
    # v4.1 per-packet provenance additions (task #167):
    "api_request_id",
    "actual_token_usage",
    "stop_reason",
    "openrouter_provider",
    "wall_time_ms",
)

# AuditResult fields per ci/audit/audit_result.py.
EXPECTED_AUDIT_RESULT_FIELDS = (
    "observer_pair",
    "task",
    "tier",
    "relation_class",
    "pass_rate_a",
    "pass_rate_b",
    "smooth_fraction_a",
    "smooth_fraction_b",
    "l_hat_a",
    "l_hat_b",
    "n_a",
    "n_b",
    "reason",
    "offending_packet_hashes",
)

# Pre-reg v4 §4: 8 RelationClass values.
EXPECTED_RELATION_CLASS_VALUES = {
    "AGREEMENT": "agreement",
    "LOCAL_CONTRACT_DIVERGENCE": "local_contract_divergence",
    "CHART_TRANSITION": "chart_transition",
    "PIVOT_DISAGREEMENT": "pivot_disagreement",
    "VERIFIER_SURFACE_MISMATCH": "verifier_surface_mismatch",
    "SMOOTH_SUCCESS_EXCEPTION": "smooth_success_exception",
    "TRUE_CERTIFICATE_REFUTATION": "true_certificate_refutation",
    "INSUFFICIENT_OBSERVABILITY": "insufficient_observability",
}


# ---------------------------------------------------------------------------
# CheckResult dataclass-light
# ---------------------------------------------------------------------------
def _result(name: str, status: str, detail: str, **extra: Any) -> dict:
    rec = {"name": name, "status": status, "detail": detail}
    if extra:
        rec.update(extra)
    return rec


# ---------------------------------------------------------------------------
# 1. no_llm_imports
# ---------------------------------------------------------------------------
def _module_names_from_source(source: str) -> set[str]:
    """Return top-level module names imported by a .py source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module.split(".")[0])
    return names


def check_no_llm_imports() -> dict:
    """AST-scan every non-test .py under ci/audit/ for forbidden imports."""
    if not AUDIT_DIR.exists():
        return _result(
            "no_llm_imports", "FAIL",
            f"audit substrate not found at {AUDIT_DIR.relative_to(REPO_ROOT)}",
        )

    py_files = sorted(AUDIT_DIR.rglob("*.py"))
    # Exclude tests/ subtree; the spec is for the substrate itself.
    py_files = [p for p in py_files if AUDIT_TESTS_DIR not in p.parents]

    if not py_files:
        return _result(
            "no_llm_imports", "FAIL",
            f"no .py files found under {AUDIT_DIR.relative_to(REPO_ROOT)} (excl. tests/)",
        )

    offenders: dict[str, list[str]] = {}
    for path in py_files:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            offenders[str(path.relative_to(REPO_ROOT))] = [f"<read-error: {exc}>"]
            continue
        bad = sorted(_module_names_from_source(source) & FORBIDDEN_LLM_MODULES)
        if bad:
            offenders[str(path.relative_to(REPO_ROOT))] = bad

    if offenders:
        return _result(
            "no_llm_imports", "FAIL",
            f"forbidden LLM imports found in {len(offenders)} file(s)",
            offenders=offenders,
            scanned=len(py_files),
        )
    return _result(
        "no_llm_imports", "PASS",
        f"scanned {len(py_files)} substrate file(s); no forbidden imports",
        scanned=len(py_files),
        forbidden=sorted(FORBIDDEN_LLM_MODULES),
    )


# ---------------------------------------------------------------------------
# 2 & 3. dataclass schemas
# ---------------------------------------------------------------------------
def _import_module_from_path(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def check_observation_packet_schema() -> dict:
    """ObservationPacket has exactly the 24 pre-reg §3 fields, in order."""
    pkt_path = AUDIT_DIR / "observation_packet.py"
    if not pkt_path.exists():
        return _result(
            "observation_packet_schema", "FAIL",
            f"{pkt_path.relative_to(REPO_ROOT)} not found",
        )
    try:
        # Use the package import so dataclass internals resolve cleanly.
        # Add repo root to sys.path so `import ci.audit.*` works.
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from ci.audit.observation_packet import ObservationPacket  # type: ignore
    except Exception as exc:
        return _result(
            "observation_packet_schema", "FAIL",
            f"import failed: {type(exc).__name__}: {exc}",
        )

    if not is_dataclass(ObservationPacket):
        return _result(
            "observation_packet_schema", "FAIL",
            "ObservationPacket is not a dataclass",
        )
    if not getattr(ObservationPacket, "__dataclass_params__", None) or \
       not ObservationPacket.__dataclass_params__.frozen:
        return _result(
            "observation_packet_schema", "FAIL",
            "ObservationPacket is not frozen",
        )

    actual = tuple(f.name for f in dataclass_fields(ObservationPacket))
    expected = EXPECTED_OBSERVATION_PACKET_FIELDS

    if actual != expected:
        missing = [f for f in expected if f not in actual]
        extra = [f for f in actual if f not in expected]
        order_mismatch = (set(actual) == set(expected) and actual != expected)
        detail_bits = []
        if missing:
            detail_bits.append(f"missing={missing}")
        if extra:
            detail_bits.append(f"extra={extra}")
        if order_mismatch:
            detail_bits.append("field order differs from pre-reg v4 §3")
        return _result(
            "observation_packet_schema", "FAIL",
            "; ".join(detail_bits) or "field-set mismatch",
            actual_field_count=len(actual),
            expected_field_count=len(expected),
        )

    return _result(
        "observation_packet_schema", "PASS",
        f"all {len(expected)} pre-reg §3 fields present in declaration order",
        field_count=len(expected),
    )


def check_audit_result_schema() -> dict:
    """AuditResult is a frozen dataclass with expected field set."""
    ar_path = AUDIT_DIR / "audit_result.py"
    if not ar_path.exists():
        return _result(
            "audit_result_schema", "FAIL",
            f"{ar_path.relative_to(REPO_ROOT)} not found",
        )
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from ci.audit.audit_result import AuditResult  # type: ignore
    except Exception as exc:
        return _result(
            "audit_result_schema", "FAIL",
            f"import failed: {type(exc).__name__}: {exc}",
        )

    if not is_dataclass(AuditResult):
        return _result(
            "audit_result_schema", "FAIL",
            "AuditResult is not a dataclass",
        )
    if not getattr(AuditResult, "__dataclass_params__", None) or \
       not AuditResult.__dataclass_params__.frozen:
        return _result(
            "audit_result_schema", "FAIL",
            "AuditResult is not frozen",
        )

    actual = tuple(f.name for f in dataclass_fields(AuditResult))
    expected = EXPECTED_AUDIT_RESULT_FIELDS
    if set(actual) != set(expected):
        missing = [f for f in expected if f not in actual]
        extra = [f for f in actual if f not in expected]
        return _result(
            "audit_result_schema", "FAIL",
            f"field-set mismatch: missing={missing}, extra={extra}",
        )

    return _result(
        "audit_result_schema", "PASS",
        f"frozen dataclass; all {len(expected)} expected fields present",
        field_count=len(expected),
    )


# ---------------------------------------------------------------------------
# 4. RelationClass enum
# ---------------------------------------------------------------------------
def check_relation_class_enum() -> dict:
    rc_path = AUDIT_DIR / "relation_classes.py"
    if not rc_path.exists():
        return _result(
            "relation_class_enum", "FAIL",
            f"{rc_path.relative_to(REPO_ROOT)} not found",
        )
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from ci.audit.relation_classes import RelationClass  # type: ignore
    except Exception as exc:
        return _result(
            "relation_class_enum", "FAIL",
            f"import failed: {type(exc).__name__}: {exc}",
        )

    actual = {m.name: m.value for m in RelationClass}
    expected = EXPECTED_RELATION_CLASS_VALUES

    if actual != expected:
        missing_names = [n for n in expected if n not in actual]
        extra_names = [n for n in actual if n not in expected]
        wrong_values = {
            n: (actual.get(n), expected[n])
            for n in expected
            if n in actual and actual[n] != expected[n]
        }
        bits = []
        if missing_names:
            bits.append(f"missing_names={missing_names}")
        if extra_names:
            bits.append(f"extra_names={extra_names}")
        if wrong_values:
            bits.append(f"wrong_values={wrong_values}")
        return _result(
            "relation_class_enum", "FAIL",
            "; ".join(bits) or "enum mismatch",
            actual_count=len(actual),
            expected_count=len(expected),
        )

    return _result(
        "relation_class_enum", "PASS",
        f"all {len(expected)} pre-reg §4 enum values present",
        member_count=len(expected),
    )


# ---------------------------------------------------------------------------
# 5 & 6. pytest subprocess invocations
# ---------------------------------------------------------------------------
def _run_pytest(target: Path, label: str) -> dict:
    """Invoke ``python -m pytest <target> -q`` and capture exit code."""
    if not target.exists():
        return _result(
            label, "FAIL",
            f"target not found: {target.relative_to(REPO_ROOT)}",
        )

    cmd = [sys.executable, "-m", "pytest", str(target), "-q"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        return _result(label, "FAIL", f"pytest invocation failed: {exc}")
    except subprocess.TimeoutExpired:
        return _result(label, "FAIL", "pytest timeout (>600s)")

    # Pull the last few lines of stdout for the summary blurb.
    tail = "\n".join(
        (proc.stdout or "").rstrip().splitlines()[-3:]
    ).strip()
    if proc.returncode == 0:
        return _result(
            label, "PASS",
            f"pytest exit 0; tail: {tail!r}",
            return_code=0,
        )
    err_tail = "\n".join(
        (proc.stderr or "").rstrip().splitlines()[-3:]
    ).strip()
    return _result(
        label, "FAIL",
        f"pytest exit {proc.returncode}; stdout_tail={tail!r}; "
        f"stderr_tail={err_tail!r}",
        return_code=proc.returncode,
    )


def check_hypothesis_program_pytest_green() -> dict:
    return _run_pytest(AUDIT_TESTS_DIR, "hypothesis_program_pytest_green")


def check_aaa_gold_standard_green() -> dict:
    return _run_pytest(
        AUDIT_TESTS_DIR / "test_aaa_gold_standard.py",
        "aaa_gold_standard_green",
    )


# ---------------------------------------------------------------------------
# 7. mutation-test ledger (advisory)
# ---------------------------------------------------------------------------
def check_mutation_test_ledger() -> dict:
    if not MUTATION_LEDGER.exists():
        return _result(
            "mutation_test_ledger", "WARN",
            f"{MUTATION_LEDGER.relative_to(REPO_ROOT)} absent; "
            "Cosmic Ray pass may run separately",
            present=False,
        )
    try:
        payload = json.loads(MUTATION_LEDGER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result(
            "mutation_test_ledger", "WARN",
            f"ledger present but unreadable: {exc}",
            present=True,
        )

    # Best-effort kill-rate extraction; tolerate either Cosmic Ray's
    # native shape or a project-wrapped summary.
    kill_rate = None
    for key in ("kill_rate", "kill_rate_pct", "killed_fraction"):
        if isinstance(payload, dict) and key in payload:
            kill_rate = payload[key]
            break
    if kill_rate is None and isinstance(payload, dict):
        s = payload.get("summary", {})
        if isinstance(s, dict):
            for key in ("kill_rate", "kill_rate_pct", "killed_fraction"):
                if key in s:
                    kill_rate = s[key]
                    break

    detail = "ledger present"
    if kill_rate is not None:
        detail += f"; kill_rate={kill_rate}"
    return _result(
        "mutation_test_ledger", "PASS",
        detail,
        present=True,
        kill_rate=kill_rate,
    )


# ---------------------------------------------------------------------------
# 8. hypothesis-program completeness (programmatic re-run)
# ---------------------------------------------------------------------------
def check_hypothesis_program_completeness() -> dict:
    """Re-run TestHypothesisProgramCompleteness without invoking pytest."""
    test_file = AUDIT_TESTS_DIR / "test_hypothesis_program.py"
    if not test_file.exists():
        return _result(
            "hypothesis_program_completeness", "FAIL",
            f"{test_file.relative_to(REPO_ROOT)} not found",
        )
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        mod = _import_module_from_path(
            "ci_audit_tests_test_hypothesis_program",
            test_file,
        )
    except Exception as exc:
        return _result(
            "hypothesis_program_completeness", "FAIL",
            f"import failed: {type(exc).__name__}: {exc}",
        )

    cls = getattr(mod, "TestHypothesisProgramCompleteness", None)
    if cls is None:
        return _result(
            "hypothesis_program_completeness", "FAIL",
            "TestHypothesisProgramCompleteness class not found",
        )
    required = list(getattr(cls, "REQUIRED_HYPOTHESIS_TEST_NAMES", []))
    if not required:
        return _result(
            "hypothesis_program_completeness", "FAIL",
            "REQUIRED_HYPOTHESIS_TEST_NAMES is empty or missing",
        )

    all_test_names: set[str] = set()
    for _cls_name, kls in inspect.getmembers(mod, inspect.isclass):
        for name, _ in inspect.getmembers(kls, inspect.isfunction):
            if name.startswith("test_"):
                all_test_names.add(name)

    missing = [n for n in required if n not in all_test_names]
    if missing:
        return _result(
            "hypothesis_program_completeness", "FAIL",
            f"{len(missing)} required hypothesis test(s) missing: "
            f"{missing[:3]}{'...' if len(missing) > 3 else ''}",
            required_count=len(required),
            missing_count=len(missing),
        )
    return _result(
        "hypothesis_program_completeness", "PASS",
        f"all {len(required)} pre-reg §3c hypothesis test names present",
        required_count=len(required),
    )


# ---------------------------------------------------------------------------
# 9. companion_doc_hashes (informational)
# ---------------------------------------------------------------------------
def _compute_companion_doc_hashes() -> tuple[dict[str, str], int, int, list[str]]:
    """Return (hashes_dict, hashed_count, missing_count, unreadable_paths)."""
    hashes: dict[str, str] = {}
    hashed = 0
    missing = 0
    unreadable: list[str] = []
    for rel in COMPANION_DOC_PATHS:
        path = REPO_ROOT / rel
        if not path.exists():
            hashes[rel] = "FILE_MISSING"
            missing += 1
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            hashes[rel] = f"<read-error: {exc}>"
            unreadable.append(rel)
            continue
        hashes[rel] = digest
        hashed += 1
    return hashes, hashed, missing, unreadable


def check_companion_doc_hashes() -> dict:
    """Hash companion docs referenced by the pre-registration."""
    hashes, hashed, missing, unreadable = _compute_companion_doc_hashes()
    if unreadable:
        return _result(
            "companion_doc_hashes", "FAIL",
            f"{len(unreadable)} companion doc(s) present but unreadable: "
            f"{unreadable}",
            unreadable=unreadable,
            hashed=hashed,
            missing=missing,
        )
    return _result(
        "companion_doc_hashes", "PASS",
        f"hashed {hashed} companion docs ({missing} missing)",
        hashed=hashed,
        missing=missing,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
ALL_CHECKS: tuple[Callable[[], dict], ...] = (
    check_no_llm_imports,
    check_observation_packet_schema,
    check_audit_result_schema,
    check_relation_class_enum,
    check_hypothesis_program_pytest_green,
    check_aaa_gold_standard_green,
    check_mutation_test_ledger,
    check_hypothesis_program_completeness,
    check_companion_doc_hashes,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--json-out", default=str(RESULTS_JSON),
        help="path for JSON output (default: ci/audit_observer_purity_results.json)",
    )
    parser.add_argument(
        "--skip-pytest", action="store_true",
        help="(diagnostic) skip the two pytest subprocess invocations; "
             "useful for fast schema-only validation",
    )
    args = parser.parse_args(argv)
    out_path = Path(args.json_out)

    results: list[dict] = []
    for check_fn in ALL_CHECKS:
        if args.skip_pytest and check_fn.__name__ in {
            "check_hypothesis_program_pytest_green",
            "check_aaa_gold_standard_green",
        }:
            results.append(_result(
                check_fn.__name__.removeprefix("check_"),
                "WARN", "skipped via --skip-pytest",
            ))
            continue
        try:
            results.append(check_fn())
        except Exception as exc:  # defensive: never let one check blow the layer
            results.append(_result(
                check_fn.__name__.removeprefix("check_"),
                "FAIL", f"unhandled exception: {type(exc).__name__}: {exc}",
            ))

    pass_count = sum(1 for r in results if r["status"] == "PASS")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    hard_fail_count = sum(1 for r in results if r["status"] == "FAIL")
    passed = hard_fail_count == 0

    # Compute companion-doc hashes once for the top-level field, regardless
    # of whether the check itself ran (so the JSON always carries the digests
    # even under --skip-pytest-style partial invocations).
    companion_hashes, _, _, _ = _compute_companion_doc_hashes()

    payload = {
        "check": "audit_observer_purity",
        "summary": {
            "hard_fail_count": hard_fail_count,
            "warn_count": warn_count,
            "pass_count": pass_count,
            "passed": passed,
        },
        "checks": results,
        "companion_doc_hashes": companion_hashes,
        "ran_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    status = "PASS" if passed else "FAIL"
    print(f"[audit_observer_purity] {status}")
    print(f"  pass    : {pass_count}")
    print(f"  warn    : {warn_count}")
    print(f"  fail    : {hard_fail_count}")
    for r in results:
        line = f"  [{r['status']:<4}] {r['name']}"
        # Trim long detail blurbs for the console; full text lives in JSON.
        detail = r.get("detail", "")
        if len(detail) > 100:
            detail = detail[:97] + "..."
        if detail:
            line += f"  -- {detail}"
        print(line)
    print(f"  Results -> {out_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
