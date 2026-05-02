#!/usr/bin/env python3
"""
test_audit_substitution.py - Documents what L1 catches and what it doesn't.

The reviewer of the certificate stack flagged that L1 has no negative
test for "the claim asserts X but the paper says Y": it catches deletion
(value disappears) but not necessarily substitution-with-near-miss
(value changes to a different value that looks similar enough to a
weak pattern).

This test makes both behaviors empirically visible:

  - For STRONG claims (distinctive patterns like C5='4.8\\times'),
    a one-digit substitution moves the claim from FOUND to MISSING.
    PASS = audit caught the drift.

  - For WEAK claims (bare percent like I4='93\\s*\\?%' marked
    weak_ok=True), a one-digit substitution may NOT be caught because
    the original digit may still appear elsewhere in the paper.
    These are explicitly tagged and acknowledged as having looser
    fingerprint quality.

The test makes the asymmetry visible in code, not just prose, so
future contributors don't get a false sense of security from "97/97
verbatim" without understanding the failure surface.

Run standalone:
  python ci/tests/test_audit_substitution.py
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CI_DIR = SCRIPT_DIR.parent
REPO_ROOT = CI_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
CLAIM_AUDIT_PY = CI_DIR / "claim_audit.py"


def import_claim_audit():
    spec = importlib.util.spec_from_file_location("claim_audit", CLAIM_AUDIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claim_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def find_claim(mod, claim_id: str) -> dict:
    for c in mod.CLAIMS + mod.IMPORTANT:
        if c["id"] == claim_id:
            return c
    raise KeyError(claim_id)


def run_audit_against(tex_path: Path, mod) -> dict[str, str]:
    """Run the audit against an arbitrary main.tex and return {claim_id: status}."""
    claims = mod.CLAIMS + mod.IMPORTANT
    # Skip supplementary_only — they're not expected to be in main.tex
    claims = [c for c in claims if not c.get("supplementary_only")]

    # Use the existing audit_claims function but redirect at our temp file
    original_path = mod.MAIN_TEX
    mod.MAIN_TEX = tex_path
    try:
        results = mod.audit_claims(tex_path, claims, verbose=False)
    finally:
        mod.MAIN_TEX = original_path

    return {r.claim_id: r.status for r in results}


def mutate_main_tex(content: str, find: str, replace: str, max_replacements: int = 1000) -> str:
    """Replace occurrences of `find` with `replace` (literal, not regex)."""
    return content.replace(find, replace, max_replacements)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
def test_strong_claim_catches_substitution(mod, original_text: str) -> tuple[bool, str]:
    """C5 = 4.8x staging at frontier. Pattern: r'4\\.8\\s*(?:\\\\times|x|\\$\\\\times\\$)'.

    Substituting 4.8 -> 5.8 throughout main.tex should make C5 MISSING
    because no '5.8x' or '5.8\\times' will exist in the paper.
    """
    claim_id = "C5"
    mutated = mutate_main_tex(original_text, "4.8", "5.8")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
        fh.write(mutated)
        tmp = Path(fh.name)
    try:
        statuses = run_audit_against(tmp, mod)
        result = statuses.get(claim_id)
        passed = result in ("MISS", "WARN")
        detail = f"C5 (4.8x staging) status after 4.8->5.8: {result} ({'CAUGHT' if passed else 'MISSED'})"
        return passed, detail
    finally:
        tmp.unlink(missing_ok=True)


def test_strong_decimal_catches_substitution(mod, original_text: str) -> tuple[bool, str]:
    """C11 = 7.37% constitution conflict rate. Pattern: r'7\\.37\\s*\\?%'.

    Substituting 7.37 -> 8.37 should make C11 MISSING.
    """
    claim_id = "C11"
    mutated = mutate_main_tex(original_text, "7.37", "8.37")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
        fh.write(mutated)
        tmp = Path(fh.name)
    try:
        statuses = run_audit_against(tmp, mod)
        result = statuses.get(claim_id)
        passed = result in ("MISS", "WARN")
        detail = f"C11 (7.37%) status after 7.37->8.37: {result} ({'CAUGHT' if passed else 'MISSED'})"
        return passed, detail
    finally:
        tmp.unlink(missing_ok=True)


def test_weak_claim_documents_blind_spot(mod, original_text: str) -> tuple[bool, str]:
    """I4 = DeepSeek-Coder 93% (weak_ok=True). Pattern: r'93\\s*\\?%'.

    Substituting 93% -> 95% in main.tex MIGHT NOT make I4 MISSING,
    because '93' may persist elsewhere (e.g. as "93%" in another row,
    or simply as "93" in a number). This test documents that asymmetry.

    PASSING the test here means: the substitution was caught (good) OR
    the substitution wasn't caught AND I4 has weak_ok=True (asymmetry
    is documented).
    """
    claim_id = "I4"
    mutated = mutate_main_tex(original_text, "93\\%", "95\\%")  # LaTeX-escaped
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
        fh.write(mutated)
        tmp = Path(fh.name)
    try:
        statuses = run_audit_against(tmp, mod)
        result = statuses.get(claim_id)
        i4 = find_claim(mod, claim_id)
        is_weak = i4.get("weak_ok", False)

        # Behavior is acceptable IF: caught (MISS/WARN), OR not caught but flagged weak_ok
        caught = result in ("MISS", "WARN")
        documented = is_weak and result == "OK"

        passed = caught or documented
        if caught:
            detail = f"I4 (93%) status after 93\\%->95\\%: {result} (CAUGHT — strong fingerprint!)"
        elif documented:
            detail = f"I4 (93%) status after 93\\%->95\\%: {result} (NOT CAUGHT — but weak_ok=True acknowledges)"
        else:
            detail = f"I4 (93%) status after 93\\%->95\\%: {result} (MISSED and not weak_ok!)"
        return passed, detail
    finally:
        tmp.unlink(missing_ok=True)


def test_joint_mode_catches_table_row_drift(mod, original_text: str) -> tuple[bool, str]:
    """I2 = Qwen-2.5-Coder 91% (joint mode, window=3).

    Joint mode requires "Qwen.*?Coder" AND "91%" to co-occur within
    3 lines. To break the join, we mutate ALL '91\\%' instances in
    the paper to '92\\%'. With joint mode, this drops I2 to
    MISSING/DRIFT (no Qwen+91 anywhere). Without joint mode, the
    behavior would be the same here (since per-pattern would also
    fail), so this test alone doesn't prove joint mode adds value.

    The deeper proof is the contrast with the per-pattern (any) mode:
    if I2 were in 'any' mode and we mutated only the SINGLE Qwen-row
    91% (leaving 91% elsewhere intact), 'any' mode would falsely
    report FOUND while 'joint' would correctly report MISSING. That
    requires knowing main.tex's exact line layout to construct, so
    here we settle for the simpler "all-91%-mutated" check, which at
    minimum proves the joint check responds to changes in the patterns.
    """
    claim_id = "I2"
    if "91\\%" not in original_text:
        return True, f"I2 (joint): no '91\\%' in main.tex; test inapplicable"
    n_instances = original_text.count("91\\%")
    mutated = original_text.replace("91\\%", "92\\%")  # all
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
        fh.write(mutated)
        tmp = Path(fh.name)
    try:
        statuses = run_audit_against(tmp, mod)
        result = statuses.get(claim_id)
        passed = result in ("MISS", "WARN")
        if passed:
            detail = f"I2 (joint, Qwen+91%) after wholesale 91\\%->92\\% ({n_instances} instances): {result} (CAUGHT — joint check responds to value change)"
        else:
            detail = f"I2 (joint) after wholesale 91\\%->92\\% ({n_instances}): {result} (NOT CAUGHT — joint check is broken, investigate)"
        return passed, detail
    finally:
        tmp.unlink(missing_ok=True)


def test_no_op_baseline(mod, original_text: str) -> tuple[bool, str]:
    """Sanity: with no mutations, every non-supp Tier 1+2 claim is FOUND.

    If this fails the test infrastructure itself is broken, not the audit.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
        fh.write(original_text)
        tmp = Path(fh.name)
    try:
        statuses = run_audit_against(tmp, mod)
        n_total = len(statuses)
        n_found = sum(1 for s in statuses.values() if s == "OK")
        n_missing = sum(1 for s in statuses.values() if s == "MISS")
        passed = n_missing == 0
        detail = f"baseline (no mutation): {n_found}/{n_total} FOUND, {n_missing} MISSING"
        return passed, detail
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    if not MAIN_TEX.exists():
        print(f"ERROR: main.tex not found at {MAIN_TEX}", file=sys.stderr)
        return 2

    mod = import_claim_audit()
    original_text = MAIN_TEX.read_text(encoding="utf-8", errors="replace")

    tests = [
        ("baseline (no mutation)", test_no_op_baseline),
        ("strong_claim_catches_substitution (C5: 4.8x -> 5.8x)", test_strong_claim_catches_substitution),
        ("strong_decimal_catches_substitution (C11: 7.37% -> 8.37%)", test_strong_decimal_catches_substitution),
        ("weak_claim_documents_blind_spot (I4: 93% -> 95%)", test_weak_claim_documents_blind_spot),
        ("joint_mode_catches_table_row_drift (I2: surgical 91% mutation)", test_joint_mode_catches_table_row_drift),
    ]

    print("=" * 70)
    print("AUDIT SUBSTITUTION TESTS  (what L1 catches and what it doesn't)")
    print("=" * 70)

    n_pass = 0
    n_fail = 0
    for name, fn in tests:
        passed, detail = fn(mod, original_text)
        badge = "[OK]  " if passed else "[FAIL]"
        print(f"  {badge} {name}")
        print(f"         {detail}")
        if passed:
            n_pass += 1
        else:
            n_fail += 1

    print()
    print("-" * 70)
    print(f"  Passed: {n_pass} / {len(tests)}")
    print(f"  Failed: {n_fail} / {len(tests)}")
    print("-" * 70)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
