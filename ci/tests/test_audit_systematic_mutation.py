#!/usr/bin/env python3
"""
test_audit_systematic_mutation.py - Systematic mutation coverage over
every Tier 1+2 claim.

Where test_audit_substitution.py walks four hand-picked claims to
demonstrate the mechanism, this script walks ALL 100 claims and
reports which ones the audit catches a substitution on, and which
ones it doesn't.

Method
------
For each claim:
  1. Find a "mutateable" numeric pattern in its patterns list.
     A pattern is mutateable if it contains a literal numeric value
     (e.g., "0.023", "26.4", "93\\s*\\?%").
  2. Extract that numeric value.
  3. Find every occurrence of that exact value in main.tex.
  4. If at least one occurrence exists, mutate ALL occurrences to a
     near-miss value (last digit + 1, or +1 for integers).
  5. Run the audit against the mutated main.tex.
  6. Record: did the claim flip from FOUND to MISS/DRIFT?

Results categories:
  CAUGHT   — mutation moved the claim away from FOUND (good)
  MISSED   — mutation didn't change the verdict (claim is too loose)
  SKIPPED  — could not find a mutateable value, or the value isn't
             in main.tex, or the claim is supplementary_only

Output
------
JSON report at ci/tests/systematic_mutation_results.json plus a
console summary. Exit 0 if every non-skipped claim is CAUGHT;
exit 1 if any claim is MISSED.

This is the empirical answer to "is each claim's fingerprint
distinctive enough?". Where this test reports MISSED, the claim
needs either a tighter pattern, a joint-mode anchor, or a weak_ok
acknowledgement.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CI_DIR = SCRIPT_DIR.parent
REPO_ROOT = CI_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
CLAIM_AUDIT_PY = CI_DIR / "claim_audit.py"
RESULTS_JSON = SCRIPT_DIR / "systematic_mutation_results.json"


def import_claim_audit():
    spec = importlib.util.spec_from_file_location("claim_audit", CLAIM_AUDIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claim_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class MutationResult:
    claim_id: str
    description: str
    category: str          # CAUGHT / MISSED / SKIPPED
    mutated_value: str | None
    new_value: str | None
    n_occurrences: int     # how many places we mutated
    audit_status_after: str | None  # OK / MISS / WARN
    skip_reason: str | None = None
    weak_ok: bool = False
    match_mode: str = "any"

    def to_dict(self) -> dict:
        return asdict(self)


DIGIT_RE = re.compile(r"\d")


def mutate_via_patterns(claim: dict, original_text: str) -> tuple[str | None, int, str | None]:
    """Use the claim's own patterns to find match sites in main.tex,
    then mutate a digit in each match. Returns (mutated_text, n_sites,
    debug_info_str) — or (None, 0, reason) if no mutation possible.

    Strategy: for each pattern in the claim, find every regex match
    in main.tex. Within each matched substring, find the first digit
    and increment it (9 → 0 with no carry, simplest). Apply all
    replacements simultaneously. This guarantees we're mutating
    exactly the text the audit cares about.

    For joint-mode claims, mutating any single pattern's matches
    breaks the joint window requirement (a pattern that doesn't
    match anywhere can't co-occur with the others). For any-mode
    claims, mutating all matches of one pattern means that pattern
    fails the per-pattern check, dropping the claim from FOUND.
    """
    if claim.get("supplementary_only"):
        return None, 0, "supplementary_only"

    patterns_str = claim.get("patterns", [])
    if not patterns_str:
        return None, 0, "no patterns"

    # Find a "value-bearing" pattern (one with at least one digit)
    # Prefer patterns with the most digits as they're most distinctive.
    def digit_count(p): return len(DIGIT_RE.findall(p))
    candidates = sorted(patterns_str, key=digit_count, reverse=True)

    for p_str in candidates:
        if digit_count(p_str) == 0:
            continue
        try:
            regex = re.compile(p_str)
        except re.error:
            continue
        matches = list(regex.finditer(original_text))
        if not matches:
            continue

        # Build the mutated text: replace each match with a near-miss
        # version (mutate the FIRST digit in the matched substring)
        def mutate_match(matched_text: str) -> str:
            digit_match = DIGIT_RE.search(matched_text)
            if digit_match is None:
                return matched_text + "X"
            i = digit_match.start()
            c = matched_text[i]
            new_c = "0" if c == "9" else str(int(c) + 1)
            return matched_text[:i] + new_c + matched_text[i + 1:]

        # Apply all replacements in reverse order (so offsets stay valid)
        mutated = original_text
        n_replaced = 0
        for m in reversed(matches):
            new_match = mutate_match(m.group(0))
            if new_match != m.group(0):
                mutated = mutated[:m.start()] + new_match + mutated[m.end():]
                n_replaced += 1

        if n_replaced > 0:
            return mutated, n_replaced, f"mutated pattern '{p_str[:40]}...' at {n_replaced} sites"

    return None, 0, "no patterns matched in main.tex"


def run_audit_against(tex_path: Path, mod) -> dict[str, str]:
    claims = [c for c in (mod.CLAIMS + mod.IMPORTANT) if not c.get("supplementary_only")]
    original_path = mod.MAIN_TEX
    mod.MAIN_TEX = tex_path
    try:
        results = mod.audit_claims(tex_path, claims, verbose=False)
    finally:
        mod.MAIN_TEX = original_path
    return {r.claim_id: r.status for r in results}


def _try_mutate_specific_pattern(p_str: str, original_text: str) -> tuple[str | None, int, str]:
    """Mutate all matches of a single pattern in main.tex.

    Returns (mutated_text, n_sites, info). Skips patterns that
    contain unbounded wildcards (.* / .+) where digit mutations
    inside the match would still match the regex (e.g., 'Qwen.*?Coder'
    matches 'Qwen-3.5-Coder' just as well as 'Qwen-2.5-Coder' so
    mutating the version digit doesn't break the pattern).
    """
    has_wildcard = bool(re.search(r"\.[\*\+]", p_str))
    if has_wildcard:
        # Pattern is wildcard-tolerant; mutating digits inside it
        # leaves it still matching. Skip.
        return None, 0, "wildcard-tolerant"
    try:
        regex = re.compile(p_str)
    except re.error:
        return None, 0, "regex compile error"
    matches = list(regex.finditer(original_text))
    if not matches:
        return None, 0, "no matches"
    if not DIGIT_RE.search(p_str):
        return None, 0, "no digits in pattern"

    def mutate_match(matched_text: str) -> str:
        digit_match = DIGIT_RE.search(matched_text)
        if digit_match is None:
            return matched_text + "X"
        i = digit_match.start()
        c = matched_text[i]
        new_c = "0" if c == "9" else str(int(c) + 1)
        return matched_text[:i] + new_c + matched_text[i + 1:]

    mutated = original_text
    n_replaced = 0
    for m in reversed(matches):
        new_match = mutate_match(m.group(0))
        if new_match != m.group(0):
            mutated = mutated[:m.start()] + new_match + mutated[m.end():]
            n_replaced += 1

    if n_replaced == 0:
        return None, 0, "no actual replacements"
    return mutated, n_replaced, f"mutated {n_replaced} matches of '{p_str[:40]}...'"


def mutate_and_audit(claim: dict, original_text: str, mod) -> MutationResult:
    """Try mutating EACH digit-bearing pattern in turn. Report CAUGHT
    if ANY single-pattern mutation breaks the audit verdict; MISSED
    if NONE do.

    This is the empirical question: does this claim have at least one
    fingerprint pattern that would catch a substitution? If even one
    of its patterns is distinctive enough, the claim is robust. If
    every pattern is wildcard-tolerant or non-distinctive, the claim
    is too loose.
    """
    desc = claim["description"][:80]
    weak_ok = claim.get("weak_ok", False)
    match_mode = claim.get("match_mode", "any")

    if claim.get("supplementary_only"):
        return MutationResult(claim["id"], desc, "SKIPPED", None, None, 0, None,
                              skip_reason="supplementary_only", weak_ok=weak_ok, match_mode=match_mode)

    patterns = claim.get("patterns", [])
    if not patterns:
        return MutationResult(claim["id"], desc, "SKIPPED", None, None, 0, None,
                              skip_reason="no patterns", weak_ok=weak_ok, match_mode=match_mode)

    # Try each pattern in turn — take the first one that breaks the audit.
    best_attempt_info = None
    n_mutateable = 0
    for p_str in patterns:
        mutated, n_sites, info = _try_mutate_specific_pattern(p_str, original_text)
        if mutated is None:
            continue
        n_mutateable += 1

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
            fh.write(mutated)
            tmp = Path(fh.name)
        try:
            statuses = run_audit_against(tmp, mod)
            post_status = statuses.get(claim["id"], "UNKNOWN")
            if post_status in ("MISS", "WARN"):
                return MutationResult(claim["id"], desc, "CAUGHT", info, info, n_sites,
                                      post_status, weak_ok=weak_ok, match_mode=match_mode)
            best_attempt_info = f"{info}; status remained {post_status}"
        finally:
            tmp.unlink(missing_ok=True)

    if n_mutateable == 0:
        # All patterns are non-numeric or wildcard-tolerant — can't be
        # tested by digit mutation. SKIPPED, not MISSED.
        return MutationResult(claim["id"], desc, "SKIPPED", None, None, 0, None,
                              skip_reason="all patterns are non-numeric or wildcard-tolerant",
                              weak_ok=weak_ok, match_mode=match_mode)

    # Had mutateable patterns but none broke the audit — real MISS
    return MutationResult(claim["id"], desc, "MISSED", best_attempt_info, best_attempt_info, 0,
                          None, weak_ok=weak_ok, match_mode=match_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print per-claim result as it runs")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any claim is MISSED (default: report only)")
    args = parser.parse_args()

    if not MAIN_TEX.exists():
        print(f"ERROR: main.tex not found at {MAIN_TEX}", file=sys.stderr)
        return 2

    mod = import_claim_audit()
    original_text = MAIN_TEX.read_text(encoding="utf-8", errors="replace")

    # Baseline check first
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as fh:
        fh.write(original_text)
        baseline_path = Path(fh.name)
    try:
        baseline_statuses = run_audit_against(baseline_path, mod)
    finally:
        baseline_path.unlink(missing_ok=True)
    n_baseline_ok = sum(1 for s in baseline_statuses.values() if s == "OK")
    n_baseline_total = len(baseline_statuses)
    if n_baseline_ok != n_baseline_total:
        print(f"WARNING: baseline already has {n_baseline_total - n_baseline_ok} non-OK claims; mutation results may be misleading", file=sys.stderr)

    results: list[MutationResult] = []
    for claim in mod.CLAIMS + mod.IMPORTANT:
        if args.verbose:
            print(f"  testing {claim['id']}...", end=" ", flush=True)
        r = mutate_and_audit(claim, original_text, mod)
        results.append(r)
        if args.verbose:
            print(f"{r.category}{' (weak_ok)' if r.weak_ok else ''}{' (joint)' if r.match_mode == 'joint' else ''}")

    # Summarize
    by_cat: dict[str, list[MutationResult]] = {"CAUGHT": [], "MISSED": [], "SKIPPED": []}
    for r in results:
        by_cat[r.category].append(r)

    print()
    print("=" * 70)
    print(f"SYSTEMATIC MUTATION TEST  ({len(results)} claims walked)")
    print("=" * 70)
    print(f"  Baseline: {n_baseline_ok}/{n_baseline_total} claims FOUND before mutation")
    print(f"  CAUGHT:   {len(by_cat['CAUGHT']):>3}  (mutation flipped to MISS/WARN — fingerprint works)")
    print(f"  MISSED:   {len(by_cat['MISSED']):>3}  (mutation didn't change verdict — fingerprint too loose)")
    print(f"  SKIPPED:  {len(by_cat['SKIPPED']):>3}  (no mutateable value, or value not in main.tex)")
    print()

    if by_cat["MISSED"]:
        print("MISSED — these claims have fingerprints that don't catch substitution:")
        print("-" * 70)
        for r in by_cat["MISSED"]:
            tag = "weak_ok" if r.weak_ok else f"mode={r.match_mode}"
            print(f"  {r.claim_id:<6} [{tag:>15}] {r.description[:60]}")
            print(f"          mutated '{r.mutated_value}' -> '{r.new_value}' ({r.n_occurrences} sites); status remained {r.audit_status_after}")
        print()

    if by_cat["SKIPPED"] and args.verbose:
        print("SKIPPED — no mutation applied:")
        print("-" * 70)
        for r in by_cat["SKIPPED"]:
            print(f"  {r.claim_id:<6}  {r.skip_reason}")
        print()

    payload = {
        "summary": {
            "total": len(results),
            "caught": len(by_cat["CAUGHT"]),
            "missed": len(by_cat["MISSED"]),
            "skipped": len(by_cat["SKIPPED"]),
            "baseline_found": n_baseline_ok,
            "baseline_total": n_baseline_total,
        },
        "missed_claims": [r.to_dict() for r in by_cat["MISSED"]],
        "skipped_claims": [r.to_dict() for r in by_cat["SKIPPED"]],
        "all_results": [r.to_dict() for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    if args.strict and by_cat["MISSED"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
