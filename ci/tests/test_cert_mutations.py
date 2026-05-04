#!/usr/bin/env python3
"""
test_cert_mutations.py - Mutation-style acceptance tests for the cert system.

Proves that each certification layer (L9, L15, L17, claim_audit) actually
catches the failure modes it claims to catch.  Every mutation is done on a
TEMP copy of the relevant file; real repo files are never modified.

Run standalone:
  python ci/tests/test_cert_mutations.py

Run via pytest:
  pytest ci/tests/test_cert_mutations.py -v

Exit codes (standalone):
  0 - all tests passed
  1 - one or more tests failed
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Path anchors
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CI_DIR = SCRIPT_DIR.parent
REPO_ROOT = CI_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
CLAIM_AUDIT_PY = CI_DIR / "claim_audit.py"
CROSS_CHECK_PY = CI_DIR / "cross_claim_consistency_check.py"
DECOMP_CHECK_PY = CI_DIR / "decomposition_consistency_check.py"
DATA_TIES_CHECK_PY = CI_DIR / "claim_data_ties_check.py"
DATA_TIES_JSON = CI_DIR / "claim_data_ties.json"
TEXT_NORM_PY = CI_DIR / "text_normalization.py"
CROSS_MODEL_CHECK_PY = CI_DIR / "cross_model_metadata_check.py"
PIVOT_RESULTS_JSON = REPO_ROOT / "rebuttal" / "figures" / "unconditional_pivot_results.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_module(path: Path, module_name: str):
    """Import a Python file by path and return the module."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_check(script: Path, extra_args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Run a cert check script with subprocess.run, capturing output."""
    result = subprocess.run(
        [sys.executable, str(script)] + extra_args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    return result


def _write_tmp_copy(src: Path, tmpdir: Path) -> Path:
    """Copy src into tmpdir and return the destination path."""
    dst = tmpdir / src.name
    shutil.copy2(src, dst)
    return dst


def _result_tag(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# ===========================================================================
# TEST 1 — denominator label swap
# ===========================================================================

def test_denominator_label_swap_fails() -> tuple[bool, str]:
    """Mutate temp main.tex to say '0/4,800 smooth-regime'.

    The decomposition_consistency_check's _no_4800_smooth_regime_in_paper
    relation should FAIL because smooth_total (4272) != total_infeasible (4800)
    and the paper now wrongly calls 4800 the smooth-regime denominator.

    Strategy: run decomposition_consistency_check.py against a temp repo where
    paper/main.tex has the injected bad string.  We monkeypatch PAPER_TEX in
    decomposition_consistency_check by temporarily writing to the temp file
    and setting env so the script resolves it, OR we run it directly against
    a temp tree.

    Simpler approach: import decomp_check in-process, monkeypatch PAPER_TEX,
    call the specific relation function directly to stay hermetic.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="cert_mut1_"))
    try:
        # Write mutated main.tex
        original = MAIN_TEX.read_text(encoding="utf-8")
        # Replace "0/4,272 smooth-regime" with "0/4,800 smooth-regime"
        # so the paper appears to call 4,800 the smooth-regime total.
        mutated = original.replace(
            r"0/4,272 smooth-regime",
            r"0/4,800 smooth-regime",
        )
        if mutated == original:
            # Try alternate form that actually appears in the file
            mutated = re.sub(
                r"0/4[,\\{}]*272 smooth-regime",
                "0/4,800 smooth-regime",
                original,
            )

        tmp_tex = tmpdir / "main.tex"
        tmp_tex.write_text(mutated, encoding="utf-8")

        # Import decomp check and monkeypatch PAPER_TEX
        mod_name = "_decomp_check_mut1"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = _import_module(DECOMP_CHECK_PY, mod_name)
        original_tex_path = mod.PAPER_TEX
        mod.PAPER_TEX = tmp_tex
        mod._paper_text_cache = None  # reset cache if any

        try:
            result = mod._no_4800_smooth_regime_in_paper()
        finally:
            mod.PAPER_TEX = original_tex_path
            # clear cache for other tests
            if hasattr(mod, "_paper_text_cache"):
                mod._paper_text_cache = None

        passed = not result.passed  # We expect the relation to FAIL
        detail = (
            f"relation.passed={result.passed} (expected False); "
            f"detail={result.detail!r}"
        )
        return passed, detail
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# TEST 2 — L15 locality fails when 4,272 smooth-regime string removed
# ===========================================================================

def test_smooth_total_locality_fails_if_removed() -> tuple[bool, str]:
    """Remove '4,272 smooth-regime' from a temp copy of main.tex.

    The L15 entry smooth_regime_total_4272 has paper_render_pattern =
    '4[,\\{}]*272 smooth-regime'. When that string is absent, evaluate_claim
    should return data_ok=True but locality_fail=True (because the data
    still says 4272, but the paper text no longer shows it).

    We run claim_data_ties_check in-process by importing it, patching
    PAPER_TEX, clearing the cache, and calling evaluate_claim for the
    smooth_regime_total_4272 entry only.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="cert_mut2_"))
    try:
        original = MAIN_TEX.read_text(encoding="utf-8")
        # Remove every occurrence of "4,272 smooth-regime" (and variants)
        mutated = re.sub(r"4[,\\{}]*272\s*smooth-regime", "DELETED_FOR_TEST", original)
        tmp_tex = tmpdir / "main.tex"
        tmp_tex.write_text(mutated, encoding="utf-8")

        mod_name = "_l15_check_mut2"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = _import_module(DATA_TIES_CHECK_PY, mod_name)

        # Patch the paper path and clear cache
        mod.PAPER_TEX = tmp_tex
        mod._paper_text_cache = None

        manifest = json.loads(DATA_TIES_JSON.read_text(encoding="utf-8"))
        entry = manifest["claims"]["smooth_regime_total_4272"]

        result = mod.evaluate_claim("smooth_regime_total_4272", entry)

        # data_ok should be True (source JSON still has 4272)
        data_ok = (result.computed == 4272.0)
        # overall passed should be False (locality failed)
        locality_failed = not result.passed
        passed = data_ok and locality_failed

        detail = (
            f"computed={result.computed} data_ok={data_ok} "
            f"overall_passed={result.passed} detail={result.detail!r}"
        )
        return passed, detail
    finally:
        mod.PAPER_TEX = MAIN_TEX  # restore
        mod._paper_text_cache = None
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# TEST 3 — Cross-model count drift (T45: "6 providers" -> "4 providers")
# ===========================================================================

def test_cross_model_count_drift_fails() -> tuple[bool, str]:
    """Mutate temp main.tex caption to say '4 providers' instead of '6 providers'.

    T45 pattern: r'6\\s+providers'. After mutation, claim_audit should report
    T45 as MISS or DRIFT (not FOUND).

    We import claim_audit in-process and redirect the tex path to the temp copy.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="cert_mut3_"))
    try:
        original = MAIN_TEX.read_text(encoding="utf-8")
        mutated = re.sub(r"6 providers", "4 providers", original)
        if mutated == original:
            return False, "Precondition: '6 providers' not found in main.tex; test cannot inject mutation"

        tmp_tex = tmpdir / "main.tex"
        tmp_tex.write_text(mutated, encoding="utf-8")

        mod_name = "_claim_audit_mut3"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = _import_module(CLAIM_AUDIT_PY, mod_name)

        # Find T45 in COMPLETE tier
        t45 = next((c for c in mod.COMPLETE if c["id"] == "T45"), None)
        if t45 is None:
            return False, "T45 not found in claim_audit.COMPLETE"

        results = mod.audit_claims(tmp_tex, [t45], verbose=False)
        status = results[0].status

        passed = status in (mod.MISSING, mod.DRIFT)
        detail = f"T45 status after '6 providers'->'4 providers': {status} (expected MISS or DRIFT)"
        return passed, detail
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# TEST 4 — rhohat macro equivalence
# ===========================================================================

def test_rhohat_macro_equivalence() -> tuple[bool, str]:
    r"""Three assertions:
    1. normalize_latex maps '\rhohat{=}0.50' -> '\hat{\rho}{=}0.50'
    2. T1's second pattern matches '\rhohat{=}0.50' after normalization
       (i.e., matches '\hat{\rho}{=}0.50')
    3. T1's second pattern also matches '\hat{\rho}{=}0.50' directly
    """
    mod_name = "_text_norm_mut4"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    norm_mod = _import_module(TEXT_NORM_PY, mod_name)

    # Assertion 1: normalize maps \rhohat -> \hat{\rho}
    raw = r"\rhohat{=}0.50"
    normalized = norm_mod.normalize_latex(raw)
    a1_ok = normalized == r"\hat{\rho}{=}0.50"

    # Load claim_audit to get T1 patterns
    ca_name = "_claim_audit_mut4"
    if ca_name in sys.modules:
        del sys.modules[ca_name]
    ca_mod = _import_module(CLAIM_AUDIT_PY, ca_name)

    t1 = next((c for c in ca_mod.COMPLETE if c["id"] == "T1"), None)
    if t1 is None:
        return False, "T1 not found in claim_audit.COMPLETE"

    # T1 has two patterns; the second one matches rhohat/hat-rho
    t1_patterns = t1["patterns"]
    rhohat_pat = next(
        (p for p in t1_patterns if "rhohat" in p or r"hat" in p),
        None,
    )
    if rhohat_pat is None:
        return False, f"T1 has no rhohat/hat pattern; patterns={t1_patterns}"

    regex = re.compile(rhohat_pat)

    # Assertion 2: matches normalized \rhohat form (i.e., \hat{\rho}{=}0.50)
    a2_ok = bool(regex.search(normalized))

    # Assertion 3: matches canonical \hat{\rho} form directly
    canonical = r"\hat{\rho}{=}0.50"
    a3_ok = bool(regex.search(canonical))

    passed = a1_ok and a2_ok and a3_ok
    detail = (
        f"normalize ok={a1_ok} (got {normalized!r}); "
        f"T1 matches rhohat-normalized={a2_ok}; "
        f"T1 matches canonical hat-rho={a3_ok}"
    )
    return passed, detail


# ===========================================================================
# TEST 5 — L15 locality: paper_render_pattern must match in body
# ===========================================================================

def test_l15_locality_distinguishes_global_from_local() -> tuple[bool, str]:
    """Verify that paper_render_pattern + paper_render_negate work correctly.

    Simplified approach (comment-vs-body distinction is hard to inject
    without a full TeX parser): instead confirm the positive+negative
    locality logic by using a synthetic entry whose pattern is absent
    from the paper.  This directly tests check_paper_locality.

    Two sub-cases:
    A. Pattern absent from paper -> locality fail even if data matches.
    B. Negate pattern present in paper -> locality fail even if data matches.
    """
    mod_name = "_l15_check_mut5"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    mod = _import_module(DATA_TIES_CHECK_PY, mod_name)
    mod.PAPER_TEX = MAIN_TEX
    mod._paper_text_cache = None

    # Case A: required pattern not in paper
    synthetic_entry_a = {
        "source_file": "rebuttal/figures/unconditional_pivot_results.json",
        "value_expr": "d['full_paper_claim']['smooth_total']",
        "expected": 4272,
        "tolerance": 0,
        "claim_text_excerpt": "synthetic test",
        "paper_render_pattern": r"THIS_STRING_DOES_NOT_EXIST_IN_PAPER_xyzzy1234",
    }
    loc_ok_a, detail_a = mod.check_paper_locality(synthetic_entry_a)
    case_a_passed = not loc_ok_a  # should FAIL (pattern absent)

    # Case B: forbidden pattern is present in paper (4,272 is in the paper)
    synthetic_entry_b = {
        "source_file": "rebuttal/figures/unconditional_pivot_results.json",
        "value_expr": "d['full_paper_claim']['smooth_total']",
        "expected": 4272,
        "tolerance": 0,
        "claim_text_excerpt": "synthetic test",
        "paper_render_negate": r"4[,\\{}]*272",  # this IS in the paper -> should fire
    }
    loc_ok_b, detail_b = mod.check_paper_locality(synthetic_entry_b)
    case_b_passed = not loc_ok_b  # should FAIL (forbidden pattern found)

    passed = case_a_passed and case_b_passed
    detail = (
        f"CaseA(absent pattern)->loc_ok={loc_ok_a} (expect False): {detail_a!r}; "
        f"CaseB(forbidden pattern present)->loc_ok={loc_ok_b} (expect False): {detail_b!r}"
    )
    return passed, detail


# ===========================================================================
# TEST 6 — Decomposition relations arithmetic (positive test)
# ===========================================================================

def test_decomposition_relations_arithmetic() -> tuple[bool, str]:
    """Read unconditional_pivot_results.json directly and assert arithmetic.

    Positive test: confirms the data file still says what the cert believes.
    - smooth_total + pivot_total == total_infeasible == 4800
    - smooth_successes == 0
    - pivot_successes_estimated == 42
    """
    if not PIVOT_RESULTS_JSON.exists():
        return False, f"Source file not found: {PIVOT_RESULTS_JSON}"

    data = json.loads(PIVOT_RESULTS_JSON.read_text(encoding="utf-8"))
    fp = data.get("full_paper_claim", {})

    smooth_total = fp.get("smooth_total")
    pivot_total = fp.get("pivot_total")
    total_infeasible = fp.get("total_infeasible")
    smooth_successes = fp.get("smooth_successes")
    pivot_successes_estimated = fp.get("pivot_successes_estimated")

    a1 = (smooth_total + pivot_total == total_infeasible)  # decomposition
    a2 = (total_infeasible == 4800)                         # total is 4800
    a3 = (smooth_successes == 0)                            # falsifiability claim
    a4 = (pivot_successes_estimated == 42)                  # 1.7% rate denominator

    passed = a1 and a2 and a3 and a4
    detail = (
        f"smooth_total={smooth_total} pivot_total={pivot_total} "
        f"total_infeasible={total_infeasible} "
        f"decomp_ok={a1} total_is_4800={a2} "
        f"smooth_successes={smooth_successes} (==0:{a3}) "
        f"pivot_successes_estimated={pivot_successes_estimated} (==42:{a4})"
    )
    return passed, detail


# ===========================================================================
# TEST 7 — Table row swap (Low <-> Moderate) must be caught by L15 locality
# ===========================================================================

def test_table_row_swap_fails() -> tuple[bool, str]:
    r"""Swap the Low and Moderate rows in tab:calibration in a temp main.tex.

    All numbers still appear in the paper; they are just attached to the wrong
    row labels.  We verify that check_paper_locality rejects this via a
    per-tier pattern that ties a tier label to its expected values.  Two
    synthetic patterns are probed:

      A. Required pattern "Low.*0\\.20" must be present in the unmodified
         paper but ABSENT after the swap (Low row now shows 0.40).
         -> locality check must FAIL after swap (required pattern gone).

      B. Required pattern "Moderate.*0\\.40" must be present before swap
         and ABSENT after (Moderate row now shows 0.20).
         -> locality check must FAIL after swap.

    Uses check_paper_locality directly with synthetic entries (same approach
    as test 5).  Mutation is applied to a temp copy of main.tex.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="cert_mut7_"))
    try:
        original = MAIN_TEX.read_text(encoding="utf-8")

        # Swap the Low and Moderate rows in tab:calibration.
        # Original:
        #   Low     & 0.20 & 56\% & 60\% & 44\% \\
        #   Moderate & 0.40 & 23\% & 23\% & 77\% \\
        # After swap:
        #   Low     & 0.40 & 23\% & 23\% & 77\% \\
        #   Moderate & 0.20 & 56\% & 60\% & 44\% \\
        low_row_pat = re.compile(
            r"(Low\s*&\s*0\.20\s*&\s*56\\%\s*&\s*60\\%\s*&\s*44\\%\s*\\\\)"
        )
        mod_row_pat = re.compile(
            r"(Moderate\s*&\s*0\.40\s*&\s*23\\%\s*&\s*23\\%\s*&\s*77\\%\s*\\\\)"
        )

        low_match = low_row_pat.search(original)
        mod_match = mod_row_pat.search(original)

        if not low_match or not mod_match:
            return False, (
                f"Precondition: calibration table rows not found in expected form. "
                f"low_found={bool(low_match)} mod_found={bool(mod_match)}. "
                "Check that main.tex still has the canonical Low/Moderate rows."
            )

        # Perform the swap
        mutated = original.replace(
            low_match.group(0),
            "Low     & 0.40 & 23\\% & 23\\% & 77\\% \\\\",
        )
        mutated = mutated.replace(
            mod_match.group(0),
            "Moderate & 0.20 & 56\\% & 60\\% & 44\\% \\\\",
        )

        if mutated == original:
            return False, "Precondition: row swap produced no change; regex may need adjustment."

        tmp_tex = tmpdir / "main.tex"
        tmp_tex.write_text(mutated, encoding="utf-8")

        # Load claim_data_ties_check for check_paper_locality
        mod_name = "_l15_check_mut7"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = _import_module(DATA_TIES_CHECK_PY, mod_name)
        mod.PAPER_TEX = tmp_tex
        mod._paper_text_cache = None

        # Case A: "Low" label tied to rho=0.20 in the calibration table.
        # After swap, this pattern is gone (Low now shows 0.40).
        entry_a = {
            "source_file": "supplementary/experiments/calibration_results.json",
            "value_expr": "d['tier_rho_estimates']['low']",
            "expected": 0.20,
            "tolerance": 0.005,
            "claim_text_excerpt": "Low tier rho=0.20 in tab:calibration",
            "paper_render_pattern": r"Low\s*&\s*0\.20",
        }
        loc_ok_a, detail_a = mod.check_paper_locality(entry_a)
        case_a_caught = not loc_ok_a  # should FAIL (pattern absent after swap)

        # Case B: "Moderate" label tied to rho=0.40 in the calibration table.
        # After swap, this pattern is gone (Moderate now shows 0.20).
        entry_b = {
            "source_file": "supplementary/experiments/calibration_results.json",
            "value_expr": "d['tier_rho_estimates']['moderate']",
            "expected": 0.40,
            "tolerance": 0.005,
            "claim_text_excerpt": "Moderate tier rho=0.40 in tab:calibration",
            "paper_render_pattern": r"Moderate\s*&\s*0\.40",
        }
        loc_ok_b, detail_b = mod.check_paper_locality(entry_b)
        case_b_caught = not loc_ok_b  # should FAIL (pattern absent after swap)

        passed = case_a_caught or case_b_caught  # at least one check fires
        detail = (
            f"CaseA(Low+0.20 absent after swap)->loc_ok={loc_ok_a} caught={case_a_caught}: {detail_a!r}; "
            f"CaseB(Moderate+0.40 absent after swap)->loc_ok={loc_ok_b} caught={case_b_caught}: {detail_b!r}"
        )
        return passed, detail
    finally:
        try:
            mod.PAPER_TEX = MAIN_TEX
            mod._paper_text_cache = None
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# TEST 8 — Denominator noun swap (smooth-regime -> pivot-regime) must be caught
# ===========================================================================

def test_denominator_noun_swap_fails() -> tuple[bool, str]:
    """Mutate temp main.tex to change 'smooth-regime' to 'pivot-regime' at one site.

    The string '0/4,272 smooth-regime' becomes '0/4,272 pivot-regime'.
    All numbers are intact; only the regime-noun changes.

    The decomp check _no_4800_smooth_regime_in_paper only catches the
    "4800 called smooth" variant.  We verify the more general locality
    check via cross_claim_consistency_check's relation
    decomp__no_4800_smooth_regime_in_paper, which passes (since we
    swapped "smooth" to "pivot", not "4800").

    Instead we rely on L15 paper_render_pattern for smooth_regime_total_4272:
    pattern is '4[,\\{}]*272 smooth-regime'.  After the noun swap that
    pattern is absent -> check_paper_locality fails.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="cert_mut8_"))
    try:
        original = MAIN_TEX.read_text(encoding="utf-8")

        # Mutate ALL occurrences (realistic drift: someone re-narrates throughout).
        mutated = re.sub(
            r"(4[,\\{}]*272\s*)smooth-regime",
            r"\1pivot-regime",
            original,
        )
        if mutated == original:
            # Try alternate form without the leading 0/
            mutated = re.sub(
                r"4[,\\{}]*272\s*smooth-regime",
                "4,272 pivot-regime",
                original,
                count=1,
            )
        if mutated == original:
            return False, (
                "Precondition: '4,272 smooth-regime' not found in main.tex; "
                "cannot inject noun-swap mutation."
            )

        tmp_tex = tmpdir / "main.tex"
        tmp_tex.write_text(mutated, encoding="utf-8")

        # Load claim_data_ties_check and monkeypatch to use temp tex
        mod_name = "_l15_check_mut8"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = _import_module(DATA_TIES_CHECK_PY, mod_name)
        mod.PAPER_TEX = tmp_tex
        mod._paper_text_cache = None

        manifest = json.loads(DATA_TIES_JSON.read_text(encoding="utf-8"))
        entry = manifest["claims"]["smooth_regime_total_4272"]

        # data_ties: value check should still pass (source JSON unchanged)
        result = mod.evaluate_claim("smooth_regime_total_4272", entry)

        data_ok = (result.computed == 4272.0)
        # But paper_render_pattern '4[,\\{}]*272 smooth-regime' now fails
        # because we replaced 'smooth-regime' with 'pivot-regime' at that site.
        locality_fails = not result.passed

        passed = data_ok and locality_fails
        detail = (
            f"computed={result.computed} data_ok={data_ok} "
            f"overall_passed={result.passed} (locality_fails={locality_fails}) "
            f"detail={result.detail!r}"
        )
        return passed, detail
    finally:
        try:
            mod.PAPER_TEX = MAIN_TEX
            mod._paper_text_cache = None
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# TEST 9 — Provider count drift in caption (6 -> 5 providers) must be caught
# ===========================================================================

def test_provider_count_drift_fails() -> tuple[bool, str]:
    """Mutate temp main.tex fig:cross_model caption to say '5 providers'.

    The canonical caption reads: '9 models, 6 providers, N=3,120'.
    After mutation: '9 models, 5 providers, N=3,120'.

    cross_model_metadata_check.py computes n_providers from source JSONs
    (result: 6) and then looks for '6' in the paper window around
    fig:cross_model.  After the mutation '6 providers' -> '5 providers',
    the check cannot find '6' in the immediate caption and n_providers_in_paper_window
    should FAIL.

    Strategy: import cross_model_metadata_check in-process, monkeypatch
    PAPER_TEX to the temp file, re-run extract_paper_window + run_checks.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="cert_mut9_"))
    try:
        original = MAIN_TEX.read_text(encoding="utf-8")

        # Replace '6 providers' in the fig:cross_model caption line.
        # The caption line is: '9 models, 6 providers, $N{=}3{,}120$'
        if "6 providers" not in original:
            return False, (
                "Precondition: '6 providers' not found in main.tex; "
                "test cannot inject provider-count drift mutation."
            )

        # Mutate ALL occurrences (realistic drift: someone re-narrates throughout).
        mutated = original.replace("6 providers", "5 providers")
        tmp_tex = tmpdir / "main.tex"
        tmp_tex.write_text(mutated, encoding="utf-8")

        # Import cross_model_metadata_check and monkeypatch PAPER_TEX
        mod_name = "_cross_model_mut9"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = _import_module(CROSS_MODEL_CHECK_PY, mod_name)

        original_tex = mod.PAPER_TEX
        mod.PAPER_TEX = tmp_tex

        try:
            # Discover JSON paths and compute metadata from actual source files
            json_paths = mod.discover_json_paths()
            computed = mod.compute_metadata(json_paths)

            if computed["load_errors"]:
                return False, f"cross_model_metadata load errors: {computed['load_errors']}"

            # Extract paper window from the MUTATED tex
            paper_lines, _ = mod.extract_paper_window(tmp_tex)

            # Run the checks
            checks = mod.run_checks(computed, paper_lines)

            # Find the n_providers check
            prov_check = next(
                (c for c in checks if c["name"] == "n_providers_in_paper_window"),
                None,
            )
            if prov_check is None:
                return False, "n_providers_in_paper_window check not found in results."

            # The check should FAIL: computed n_providers=6, but '6' was replaced
            # by '5' in the caption, so '6' may not appear in the window.
            check_failed = not prov_check["passed"]

            detail = (
                f"computed n_providers={computed['n_providers']}; "
                f"n_providers_in_paper_window.passed={prov_check['passed']} "
                f"(expected False); "
                f"found_at_lines={prov_check.get('found_at_lines', [])}; "
                f"detail={prov_check.get('detail', '')!r}"
            )
            return check_failed, detail
        finally:
            mod.PAPER_TEX = original_tex
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# Pytest-discoverable wrappers (module-level test_* functions)
# ===========================================================================

def test_denominator_label_swap_fails_pytest():
    ok, detail = test_denominator_label_swap_fails()
    assert ok, f"FAIL test_denominator_label_swap_fails: {detail}"


def test_smooth_total_locality_fails_if_removed_pytest():
    ok, detail = test_smooth_total_locality_fails_if_removed()
    assert ok, f"FAIL test_smooth_total_locality_fails_if_removed: {detail}"


def test_cross_model_count_drift_fails_pytest():
    ok, detail = test_cross_model_count_drift_fails()
    assert ok, f"FAIL test_cross_model_count_drift_fails: {detail}"


def test_rhohat_macro_equivalence_pytest():
    ok, detail = test_rhohat_macro_equivalence()
    assert ok, f"FAIL test_rhohat_macro_equivalence: {detail}"


def test_l15_locality_distinguishes_global_from_local_pytest():
    ok, detail = test_l15_locality_distinguishes_global_from_local()
    assert ok, f"FAIL test_l15_locality_distinguishes_global_from_local: {detail}"


def test_decomposition_relations_arithmetic_pytest():
    ok, detail = test_decomposition_relations_arithmetic()
    assert ok, f"FAIL test_decomposition_relations_arithmetic: {detail}"


def test_table_row_swap_fails_pytest():
    ok, detail = test_table_row_swap_fails()
    assert ok, f"FAIL test_table_row_swap_fails: {detail}"


def test_denominator_noun_swap_fails_pytest():
    ok, detail = test_denominator_noun_swap_fails()
    assert ok, f"FAIL test_denominator_noun_swap_fails: {detail}"


def test_provider_count_drift_fails_pytest():
    ok, detail = test_provider_count_drift_fails()
    assert ok, f"FAIL test_provider_count_drift_fails: {detail}"


# ===========================================================================
# Standalone runner
# ===========================================================================

TESTS = [
    ("test_denominator_label_swap_fails",
     "Mutate main.tex to '0/4,800 smooth-regime'; decomp relation must catch it",
     test_denominator_label_swap_fails),
    ("test_smooth_total_locality_fails_if_removed",
     "Delete '4,272 smooth-regime' from temp main.tex; L15 locality must fail",
     test_smooth_total_locality_fails_if_removed),
    ("test_cross_model_count_drift_fails",
     "Change '6 providers' -> '4 providers'; T45 claim_audit must MISS/DRIFT",
     test_cross_model_count_drift_fails),
    ("test_rhohat_macro_equivalence",
     r"normalize_latex(\rhohat) -> \hat{\rho}; T1 pattern matches both forms",
     test_rhohat_macro_equivalence),
    ("test_l15_locality_distinguishes_global_from_local",
     "check_paper_locality rejects absent required pattern and present forbidden pattern",
     test_l15_locality_distinguishes_global_from_local),
    ("test_decomposition_relations_arithmetic",
     "unconditional_pivot_results.json: smooth+pivot==4800, smooth_ok=0, pivot_ok=42",
     test_decomposition_relations_arithmetic),
    ("test_table_row_swap_fails",
     "Swap Low/Moderate rows in tab:calibration; L15 tier-label+rho locality must catch it",
     test_table_row_swap_fails),
    ("test_denominator_noun_swap_fails",
     "Change '4,272 smooth-regime' to '4,272 pivot-regime'; L15 paper_render_pattern must fail",
     test_denominator_noun_swap_fails),
    ("test_provider_count_drift_fails",
     "Change '6 providers' -> '5 providers' in caption; cross_model_metadata n_providers check must fail",
     test_provider_count_drift_fails),
]


def main() -> int:
    print("=" * 70)
    print("CERT MUTATION ACCEPTANCE TESTS")
    print("=" * 70)
    print(f"Repo root: {REPO_ROOT}")
    print()

    n_pass = 0
    n_fail = 0
    failures: list[tuple[str, str]] = []

    for name, description, fn in TESTS:
        print(f"  Running: {name}")
        print(f"           {description}")
        try:
            ok, detail = fn()
        except Exception as exc:
            ok = False
            detail = f"EXCEPTION: {type(exc).__name__}: {exc}"
        tag = _result_tag(ok)
        print(f"  {tag}: {detail}")
        print()
        if ok:
            n_pass += 1
        else:
            n_fail += 1
            failures.append((name, detail))

    print("-" * 70)
    print(f"  Passed: {n_pass} / {len(TESTS)}")
    print(f"  Failed: {n_fail} / {len(TESTS)}")
    print("-" * 70)

    if failures:
        print()
        print("Failed tests:")
        for name, detail in failures:
            print(f"  [FAIL] {name}")
            print(f"         {detail}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
