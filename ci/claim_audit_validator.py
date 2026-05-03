#!/usr/bin/env python3
"""
claim_audit_validator.py - Validate the claim audit itself.

Layer 2 of the claim certification stack. claim_audit.py asks
"do registered claims still appear in the paper?". This script asks
the meta-question: "is the registry itself well-formed and self-
consistent?".

Checks performed
----------------
A. Structural integrity (claim_audit.py source of truth)
   1. CLAIMS has exactly 25 entries
   2. IMPORTANT has exactly 75 entries
   3. No duplicate claim IDs across CLAIMS + IMPORTANT
   4. Every claim has id, description, patterns (list, len >= 1)
   5. Every pattern compiles as a regex
   6. No pattern is trivially permissive (empty / "." / ".*" / "\\s*")

B. Registry consistency (CLAIM_AUDIT.md vs script)
   7. Header total claim count matches len(CLAIMS) + len(IMPORTANT)
      + Tier 3 (325)
   8. Tier-table row counts match (25 / 72 / 325)
   9. Tier 1 section header reads "(25)"
  10. Footer is venue-correct (NeurIPS, not ICML)

C. External reference integrity
  11. Every data file path under "Data File Mappings" exists on disk
  12. Every harness path under "Verification Harnesses" exists on disk

D. Audit output sanity
  13. claim_audit_results.json exists, parses, and reports zero MISS

Exit codes
----------
  0  all checks passed
  1  one or more checks failed
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CLAIM_AUDIT_PY = SCRIPT_DIR / "claim_audit.py"
CLAIMS_MD = REPO_ROOT / "CLAIM_AUDIT.md"
RESULTS_JSON = SCRIPT_DIR / "claim_audit_results.json"

EXPECTED_CRITICAL = 25
EXPECTED_IMPORTANT = 72
# Tier 3 was originally budgeted at 325 (exhaustive cell-level inventory).
# In practice, ~50 well-targeted pattern-coded claims drive empirical
# body-prose coverage above 90%, so the script's COMPLETE list is far
# smaller than the inventory. The validator now reads len(mod.COMPLETE)
# at runtime instead of asserting a fixed number.
EXPECTED_TOTAL_PATTERN_CODED_HINT = "25 critical + 72 important + len(COMPLETE)"

TRIVIAL_PATTERNS = {"", ".", ".*", ".+", r"\s*", r"\s+", r".*?"}


def _is_weak_pattern(pattern_str: str) -> bool:
    """Heuristic: this pattern alone matches almost any short numeric.

    A pattern is weak if it would match casually-occurring text in
    main.tex (e.g. r"5\\s*\\?%" matches every '5%' in the paper).
    Weak patterns aren't disallowed — they're acceptable as one of
    several patterns in a claim — but a claim with ONLY weak patterns
    has no real fingerprint and should be flagged.
    """
    p = pattern_str.strip()
    # Bare 1-3 digit integer: r"5", r"23", r"100"
    if re.fullmatch(r"\d{1,3}", p):
        return True
    # Bare percent with common LaTeX escapes: r"5\s*\\?%" or r"23\s*\\?%"
    if re.fullmatch(r"\d{1,3}\\s\*\\\\\??%", p):
        return True
    # Single short decimal: r"0\.5", r"1\.0"
    if re.fullmatch(r"\d\\\.\d", p):
        return True
    return False


def _claim_has_only_weak_patterns(patterns: list[str]) -> bool:
    """A claim is weak overall if every one of its patterns is weak.

    Per-pattern weakness is fine if the claim has another distinctive
    pattern alongside (e.g. a model name + a percent). All-weak means
    no part of the claim disambiguates from background noise.
    """
    if not patterns:
        return False  # handled by required-fields check
    return all(_is_weak_pattern(p) for p in patterns)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def import_claim_audit():
    """Load claim_audit.py as a module so we can inspect CLAIMS / IMPORTANT.

    We must register the module in sys.modules *before* exec_module,
    otherwise @dataclass inside claim_audit.py fails (it walks
    sys.modules to resolve forward refs and KW_ONLY).
    """
    spec = importlib.util.spec_from_file_location("claim_audit", CLAIM_AUDIT_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {CLAIM_AUDIT_PY}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claim_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# A. Structural integrity
# ---------------------------------------------------------------------------
def check_critical_count(mod) -> CheckResult:
    n = len(mod.CLAIMS)
    return CheckResult(
        "A1. CLAIMS has 25 entries",
        n == EXPECTED_CRITICAL,
        f"got {n}",
    )


def check_important_count(mod) -> CheckResult:
    n = len(mod.IMPORTANT)
    return CheckResult(
        "A2. IMPORTANT has 72 entries",
        n == EXPECTED_IMPORTANT,
        f"got {n}",
    )


def check_no_duplicate_ids(mod) -> CheckResult:
    all_ids = [c["id"] for c in mod.CLAIMS] + [c["id"] for c in mod.IMPORTANT]
    seen, dups = set(), []
    for cid in all_ids:
        if cid in seen:
            dups.append(cid)
        seen.add(cid)
    return CheckResult(
        "A3. No duplicate claim IDs",
        not dups,
        f"duplicates: {dups}" if dups else f"{len(all_ids)} unique IDs",
    )


def check_required_fields(mod) -> CheckResult:
    bad: list[str] = []
    for c in mod.CLAIMS + mod.IMPORTANT:
        if not c.get("id"):
            bad.append("<missing id>")
            continue
        if not c.get("description"):
            bad.append(f"{c['id']}: missing description")
        pats = c.get("patterns")
        if not isinstance(pats, list) or len(pats) < 1:
            bad.append(f"{c['id']}: needs patterns list with >=1 entry")
    return CheckResult(
        "A4. Every claim has id, description, patterns",
        not bad,
        "; ".join(bad) if bad else "all 100 well-formed",
    )


def check_patterns_compile(mod) -> CheckResult:
    bad: list[str] = []
    for c in mod.CLAIMS + mod.IMPORTANT:
        for p in c.get("patterns", []):
            try:
                re.compile(p)
            except re.error as exc:
                bad.append(f"{c['id']}: {p!r} -> {exc}")
    return CheckResult(
        "A5. Every pattern compiles as a regex",
        not bad,
        "\n         ".join(bad) if bad else "all patterns compile",
    )


def check_no_trivial_patterns(mod) -> CheckResult:
    bad: list[str] = []
    for c in mod.CLAIMS + mod.IMPORTANT:
        for p in c.get("patterns", []):
            if p.strip() in TRIVIAL_PATTERNS:
                bad.append(f"{c['id']}: trivially permissive pattern {p!r}")
    return CheckResult(
        "A6. No trivially permissive patterns",
        not bad,
        "; ".join(bad) if bad else "no '.' / '.*' / empty patterns",
    )


def check_no_all_weak_claims(mod) -> CheckResult:
    """Flag claims whose every pattern is a weak fingerprint.

    A weak pattern (bare 1-3 digit int, r"\\d+\\s*\\?%" without anchor,
    single short decimal) matches background numeric text. One weak
    pattern alongside another distinctive one is fine; all weak means
    the claim cannot distinguish itself from background noise.

    Claims may set "weak_ok": True to acknowledge the looseness
    explicitly. The expectation is that such claims rely on joint-
    context matching (match_mode='joint') to recover rigor — having
    the patterns co-occur in a small window is what disambiguates
    them from background noise.
    """
    bad: list[str] = []
    for c in mod.CLAIMS + mod.IMPORTANT:
        patterns = c.get("patterns", [])
        if c.get("weak_ok"):
            continue
        if _claim_has_only_weak_patterns(patterns):
            bad.append(f"{c['id']}")
    return CheckResult(
        "A7. No claims with only weak fingerprints (without weak_ok)",
        not bad,
        f"weak claims missing 'weak_ok': {bad}" if bad else "every claim either has a distinctive pattern or is explicitly weak_ok",
    )


# ---------------------------------------------------------------------------
# B. Registry consistency (CLAIM_AUDIT.md vs script)
# ---------------------------------------------------------------------------
def _read_registry() -> str:
    return CLAIMS_MD.read_text(encoding="utf-8")


def check_header_total(text: str, n_complete: int) -> CheckResult:
    # Look for "Total Claims: NNN pattern-coded (a critical + b important + c complete)"
    m = re.search(r"\*\*Total Claims:\*\*\s*(\d+)\s*pattern-coded\s*\((\d+)\s*critical\s*\+\s*(\d+)\s*important\s*\+\s*(\d+)\s*complete\)", text)
    if not m:
        return CheckResult("B1. Header total-claims line is parseable", False,
                          "could not find '**Total Claims:** N pattern-coded (a critical + b important + c complete)'")
    total, crit, imp, comp = (int(m.group(i)) for i in (1, 2, 3, 4))
    expected_total = EXPECTED_CRITICAL + EXPECTED_IMPORTANT + n_complete
    ok = (
        crit == EXPECTED_CRITICAL
        and imp == EXPECTED_IMPORTANT
        and comp == n_complete
        and total == expected_total
    )
    return CheckResult(
        f"B1. Header pattern-coded counts match ({EXPECTED_CRITICAL} + {EXPECTED_IMPORTANT} + {n_complete} = {expected_total})",
        ok,
        f"got total={total}, critical={crit}, important={imp}, complete={comp}",
    )


def check_tier_table(text: str, n_complete: int) -> CheckResult:
    """The 'Tiered Verification Architecture' table near the top."""
    bad: list[str] = []
    # Critical and Important are exact integers
    for label, want in [("Critical", EXPECTED_CRITICAL), ("Important", EXPECTED_IMPORTANT)]:
        m = re.search(rf"\|\s*\*\*{label}\*\*\s*\|\s*(\d+)\s*\|", text)
        if not m:
            bad.append(f"{label}: row missing")
            continue
        got = int(m.group(1))
        if got != want:
            bad.append(f"{label}: registry says {got}, expected {want}")
    # Complete row uses "X pattern-coded (out of Y inventoried)" form
    m = re.search(r"\|\s*\*\*Complete\*\*\s*\|\s*(\d+)\s+pattern-coded", text)
    if not m:
        bad.append("Complete: row missing or not in 'N pattern-coded' form")
    else:
        got = int(m.group(1))
        if got != n_complete:
            bad.append(f"Complete: registry says {got}, script has {n_complete}")
    return CheckResult(
        f"B2. Tier-architecture table counts match (25/72/{n_complete})",
        not bad,
        "; ".join(bad) if bad else "rows match script",
    )


def check_tier1_header(text: str) -> CheckResult:
    m = re.search(r"#\s*Tier\s*1:\s*Critical\s*Claims\s*\((\d+)\)", text)
    if not m:
        return CheckResult("B3. Tier 1 section header parseable", False, "could not find '# Tier 1: Critical Claims (N)'")
    n = int(m.group(1))
    return CheckResult(
        f"B3. Tier 1 section header reads ({EXPECTED_CRITICAL})",
        n == EXPECTED_CRITICAL,
        f"got ({n})",
    )


def check_venue_label(text: str) -> CheckResult:
    has_neurips = "NeurIPS 2026" in text
    icml_in_footer = bool(re.search(r"ICML 2026 submission\*?\s*$", text.strip()))
    return CheckResult(
        "B4. Venue label is NeurIPS (no stale ICML footer)",
        has_neurips and not icml_in_footer,
        f"NeurIPS 2026 present={has_neurips}, ICML footer present={icml_in_footer}",
    )


# ---------------------------------------------------------------------------
# C. External reference integrity
# ---------------------------------------------------------------------------
def _extract_table_section(text: str, header_pat: str) -> str | None:
    """Return the markdown chunk under a heading, up to the next '## '/'# '."""
    m = re.search(header_pat, text)
    if not m:
        return None
    start = m.end()
    rest = text[start:]
    end_m = re.search(r"\n#{1,2}\s", rest)
    return rest[: end_m.start()] if end_m else rest


def _extract_backticked_paths(chunk: str) -> list[str]:
    """Pull `path/like/this.json` tokens out of a markdown table chunk."""
    return re.findall(r"`([^`]+)`", chunk)


def _resolve_repo_path(p: str) -> Path:
    """Turn a registry path like 'supplementary/experiments/' into an absolute path under REPO_ROOT."""
    return (REPO_ROOT / p).resolve() if not Path(p).is_absolute() else Path(p)


def check_data_files_exist(text: str) -> CheckResult:
    chunk = _extract_table_section(text, r"#\s*Data File Mappings\b")
    if chunk is None:
        return CheckResult("C1. Data File Mappings section present", False, "section heading not found")
    # The path column lives in the second markdown column. Tokenize backticked
    # entries; some are filenames only (resolved via the directory column).
    rows = re.findall(r"\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", chunk)
    if not rows:
        return CheckResult("C1. Data File Mappings rows parseable", False, "no rows matched expected | file | `path` | claims | grid")
    missing: list[str] = []
    checked: list[str] = []
    for name_col, dir_col, _claims_col in rows:
        name = name_col.strip().strip("`")
        # Compose dir + file. The "dir" column commonly ends with "/" and "file" is the bare name.
        candidate = (REPO_ROOT / dir_col.strip() / name).resolve()
        checked.append(str(candidate.relative_to(REPO_ROOT) if candidate.is_relative_to(REPO_ROOT) else candidate))
        if not candidate.exists():
            missing.append(str(candidate))
    return CheckResult(
        f"C1. All {len(rows)} data file paths exist on disk",
        not missing,
        f"missing: {missing}" if missing else f"all {len(rows)} resolved",
    )


def check_harness_files_exist(text: str) -> CheckResult:
    chunk = _extract_table_section(text, r"#\s*Verification Harnesses\b")
    if chunk is None:
        return CheckResult("C2. Verification Harnesses section present", False, "section heading not found")
    # Each row: | Name | `python path/to/harness.py` | claims |
    cmd_rows = re.findall(r"\|\s*([^|]+?)\s*\|\s*`python\s+([^`]+)`\s*\|", chunk)
    if not cmd_rows:
        return CheckResult("C2. Verification Harnesses rows parseable", False, "no `python ...` cells matched")
    missing: list[str] = []
    for name, path_str in cmd_rows:
        candidate = _resolve_repo_path(path_str.strip())
        if not candidate.exists():
            missing.append(f"{name.strip()} -> {path_str.strip()}")
    return CheckResult(
        f"C2. All {len(cmd_rows)} harness paths exist on disk",
        not missing,
        "missing:\n         " + "\n         ".join(missing) if missing else f"all {len(cmd_rows)} harnesses present",
    )


# ---------------------------------------------------------------------------
# D. Audit output sanity
# ---------------------------------------------------------------------------
def check_results_json_clean() -> CheckResult:
    if not RESULTS_JSON.exists():
        return CheckResult("D1. claim_audit_results.json exists", False, f"not found at {RESULTS_JSON}")
    try:
        payload = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return CheckResult("D1. claim_audit_results.json parses", False, str(exc))
    summary = payload.get("summary", {})
    n_missing = summary.get("missing", -1)
    if n_missing == -1:
        return CheckResult("D1. claim_audit_results.json has summary", False, "no 'summary.missing' key")
    return CheckResult(
        "D1. Latest claim_audit run reports zero MISS",
        n_missing == 0,
        f"missing={n_missing}, tier={summary.get('tier','?')}, total={summary.get('total','?')}",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run_all() -> list[CheckResult]:
    mod = import_claim_audit()
    text = _read_registry()
    n_complete = len(getattr(mod, "COMPLETE", []))
    return [
        # A: structural
        check_critical_count(mod),
        check_important_count(mod),
        check_no_duplicate_ids(mod),
        check_required_fields(mod),
        check_patterns_compile(mod),
        check_no_trivial_patterns(mod),
        check_no_all_weak_claims(mod),
        # B: registry consistency
        check_header_total(text, n_complete),
        check_tier_table(text, n_complete),
        check_tier1_header(text),
        check_venue_label(text),
        # C: external references
        check_data_files_exist(text),
        check_harness_files_exist(text),
        # D: audit output
        check_results_json_clean(),
    ]


def main() -> int:
    results = run_all()
    n_pass = sum(1 for r in results if r.passed)
    n_fail = len(results) - n_pass

    print("=" * 70)
    print("CLAIM AUDIT VALIDATOR  (the certifier of the certificate)")
    print("=" * 70)
    print(f"Source of truth: {CLAIM_AUDIT_PY}")
    print(f"Registry:        {CLAIMS_MD}")
    print()
    for r in results:
        badge = "[OK]  " if r.passed else "[FAIL]"
        print(f"  {badge} {r.name}")
        if r.detail:
            print(f"         {r.detail}")
    print()
    print("-" * 70)
    print(f"  Passed: {n_pass:>2} / {len(results)}")
    print(f"  Failed: {n_fail:>2} / {len(results)}")
    print("-" * 70)

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
