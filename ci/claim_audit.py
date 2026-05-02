#!/usr/bin/env python3
"""
claim_audit.py - Audit numeric claims in main.tex against CLAIM_AUDIT.md registry.

Walks the 25 critical (Tier 1) claims registered in ../CLAIM_AUDIT.md and
verifies each numeric signature still appears in ../paper/main.tex.

Lessons baked in:
  * main.tex is ~2,560 source lines. NEVER read it whole. We stream the file
    line-by-line and match per-line so memory and context stay bounded.
  * No auto-fix. Report only. The human decides what drift means.
  * Lexicon-respecting: the script only audits numeric signatures and never
    proposes prose changes. Protected terms (kinematic, judge-free, regime
    index, diagonal cost, ...) are loaded for visibility but never edited.

Exit codes:
  0  every claim found (with or without drift warnings)
  1  one or more claims missing entirely

Usage:
  python claim_audit.py              # quiet: summary + JSON only
  python claim_audit.py --verbose    # per-claim status as it goes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Paths (anchored to this script's location so it runs from any CWD)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CLAIMS_MD = REPO_ROOT / "CLAIM_AUDIT.md"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
LEXICON_MD = SCRIPT_DIR / "de_llm_lexicon.md"
RESULTS_JSON = SCRIPT_DIR / "claim_audit_results.json"

# Status glyphs (kept ASCII-friendly so Windows consoles render them).
FOUND = "OK"      # exact match for at least one signature
DRIFT = "WARN"    # partial match: some signatures hit, others did not
MISSING = "MISS"  # no signatures matched anywhere

# ---------------------------------------------------------------------------
# Hand-curated signature table for the 25 Tier-1 claims.
#
# Each claim gets a list of regex patterns (compiled later). A claim counts
# as FOUND when ALL its pattern groups hit; DRIFT when SOME hit but not all;
# MISSING when none hit. Patterns are LaTeX-aware: numbers are matched
# loosely (\, between digits is allowed because LaTeX often writes 4{,}800
# or 4,800 or 4{,}\!800).
#
# These signatures are the *minimum distinguishing fingerprint* of each
# claim, not a full transcription. If a claim's numeric value drifts, the
# regex stops matching and the audit flags it.
# ---------------------------------------------------------------------------

# Helper regex fragments
NUM = r"[0-9]+(?:[,\\{}\\!\s]*[0-9]+)*"  # 4800, 4,800, 4{,}800, 4{,}\!800
DEC = r"[0-9]+(?:\.[0-9]+)?"
PM = r"(?:\\pm|\+/-)"

CLAIMS: list[dict] = [
    {
        "id": "C1",
        "description": r"$\delta_{\min} = \sqrt{2/(1-\rho)}$ diagonal cost (Theorem 3.1)",
        "patterns": [
            # Accept either surface form: sqrt(2/(1-rho)) or sqrt(2)/sqrt(1-rho)
            r"\\sqrt\s*\{\s*2\s*/\s*\(\s*1\s*-\s*\\rho\s*\)\s*\}|\\sqrt\s*\{\s*2\s*\}\s*\}?\s*/?\s*\}?\s*\{?\s*\\sqrt\s*\{\s*1\s*-\s*\\rho\s*\}",
            r"diagonal\s+cost",
        ],
    },
    {
        "id": "C2",
        "description": r"$\delta_{\min} = \sqrt{k}$ orthogonal k-scaling (Theorem 3.4)",
        "patterns": [
            r"\\sqrt\s*\{\s*k\s*\}",
            r"orthogonal",
        ],
    },
    {
        "id": "C3",
        "description": r"0/4,800 smooth-regime refutations (Abstract)",
        "patterns": [
            r"0\s*/\s*4[,\\{}\\!\s]*800",
        ],
    },
    {
        "id": "C4",
        "description": r"$r_s = 1.0$ rank correlation (Table 2)",
        "patterns": [
            r"r_s\s*[={]+\s*1\.0",
        ],
    },
    {
        "id": "C5",
        "description": r"4.8x staging at frontier (opus-4.5, Table 4)",
        "patterns": [
            r"4\.8\s*(?:\\times|x|\$\\times\$)",
        ],
    },
    {
        "id": "C6",
        "description": r"26.4x staging (opus-4.1 highest, Table 4)",
        "patterns": [
            r"26\.4\s*(?:\\times|x|\$\\times\$)",
        ],
    },
    {
        "id": "C7",
        "description": r"$\delta_{\min} = \sqrt{k/(1-\rho(k-1))}$ generalized (Corollary 3.5)",
        "patterns": [
            r"\\sqrt\s*\{\s*k\s*/\s*\(\s*1\s*[-{}\s]*\\rho\s*\(?\s*k\s*[-{}\s]*1",
            r"1\s*/\s*\(\s*k\s*[-{}\s]*1\s*\)",
        ],
    },
    {
        "id": "C8",
        "description": r"$\hat{L} \in [0.019, 0.031]$ across 4 models (Table 1)",
        "patterns": [
            r"0\.019",
            r"0\.031",
        ],
    },
    {
        "id": "C9",
        "description": r"89% smooth, 11% pivot regime (Section 1)",
        "patterns": [
            r"89\s*\\?%",
            r"11\s*\\?%",
        ],
    },
    {
        "id": "C10",
        "description": r"94% router agreement across encoders (Table 6)",
        "patterns": [
            r"94\s*\\?%",
        ],
    },
    {
        "id": "C11",
        "description": r"7.37% constitution conflict rate (Appendix W)",
        "patterns": [
            r"7\.37\s*\\?%",
        ],
    },
    {
        "id": "C12",
        "description": r"93% to 3% feasibility decay (k=2 to k=10) (Appendix V)",
        "patterns": [
            r"93\s*\\?%",
            r"3\s*\\?%",
        ],
    },
    {
        "id": "C13",
        "description": r"$\hat{\rho}_{\text{stage}} = 0.15$ (Algorithm 1)",
        "patterns": [
            r"(?:stage|\\text\{stage\})\s*\}?\s*=\s*0\.15",
        ],
    },
    {
        "id": "C14",
        "description": r"$\hat{\rho}_{\text{fail}} = 0.5$ (Algorithm 1)",
        "patterns": [
            r"(?:fail|\\text\{fail\})\s*\}?\s*=\s*0\.5",
        ],
    },
    {
        "id": "C15",
        "description": r"18% pass, 384 tokens, 0.67x efficiency (Table 11)",
        "patterns": [
            r"18\s*\\?%",
            r"384",
            r"0\.67\s*(?:\\times|x|\$\\times\$)",
        ],
    },
    {
        "id": "C16",
        "description": r">=91% threshold robustness (+/-20%) (Table 7)",
        "patterns": [
            r"91\s*\\?%",
            r"20\s*\\?%",
        ],
    },
    {
        "id": "C17",
        "description": r"$r_s \geq 0.94$ across encoders (Table 6)",
        "patterns": [
            r"r_s\s*(?:\\geq|>=|\\ge)\s*0\.94",
            r"0\.94",
        ],
    },
    {
        "id": "C18",
        "description": r"94% trajectories with drift <0.15 (Section 4)",
        "patterns": [
            r"94\s*\\?%",
            r"0\.15",
        ],
    },
    {
        "id": "C19",
        "description": r"6% high-drift = pivot completions (Appendix J)",
        "patterns": [
            r"6\s*\\?%",
            r"high[-\s]drift|pivot",
        ],
    },
    {
        "id": "C20",
        "description": r"<2% regret vs oracle (Table 11)",
        "patterns": [
            r"(?:<|\\leq|\\le)\s*2\s*\\?%|2\s*\\?%\s*regret|1\.8\s*\\?\\?pm\s*0\.4\s*\\?%",
        ],
    },
    {
        "id": "C21",
        "description": r"100/100 vs 0/100 hard-negative (Appendix D.5)",
        "patterns": [
            r"100\s*/\s*100",
            r"0\s*/\s*100",
        ],
    },
    {
        "id": "C22",
        "description": r"59% to 0% Bytebeat collapse (Section 5.2)",
        "patterns": [
            r"59\s*\\?%",
            r"Bytebeat|bytebeat",
        ],
    },
    {
        "id": "C23",
        "description": r"0% IF-DSL at rho>=1.0 (Section 5.2)",
        "patterns": [
            r"IF[-\s]?DSL",
            r"\\rho\s*(?:\\geq|>=|\\ge)\s*1\.0|0\s*\\?%",
        ],
    },
    {
        "id": "C24",
        "description": r"77.5% to 100% JSON-NL failure (Appendix H)",
        "patterns": [
            r"JSON[-\s]?NL",
            r"77\.5\s*\\?%|100\s*\\?%",
        ],
    },
    {
        "id": "C25",
        "description": r"20 principles -> 190 pairs (Appendix W)",
        "patterns": [
            r"190\s+pairs|190\\\s*pairs",
            r"20\s+principles|20\\\s*principles",
        ],
    },
]

assert len(CLAIMS) == 25, f"Expected 25 critical claims, got {len(CLAIMS)}"


# ---------------------------------------------------------------------------
# Lexicon load (visibility only; we never edit prose)
# ---------------------------------------------------------------------------
def load_protected_terms(path: Path) -> list[str]:
    """Pull the bullet-listed protected terms from the lexicon file.

    We extract from Sections 1a-1d so a future check can warn if someone
    proposes editing one. This script only reads them; it does not act.
    """
    if not path.exists():
        return []
    terms: list[str] = []
    in_protected = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## 1."):
            in_protected = True
            continue
        if stripped.startswith("## 2."):
            in_protected = False
            continue
        if not in_protected:
            continue
        # Pull terms from markdown table cells and bullet points.
        m = re.match(r"\|\s*`([^`]+)`", line)
        if m:
            terms.append(m.group(1))
            continue
        m = re.match(r"-\s*`([^`]+)`", line)
        if m:
            terms.append(m.group(1))
    return terms


# ---------------------------------------------------------------------------
# Audit core
# ---------------------------------------------------------------------------
@dataclass
class PatternHit:
    pattern: str
    locations: list[tuple[int, str]] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.locations)


@dataclass
class ClaimResult:
    claim_id: str
    description: str
    status: str  # FOUND / DRIFT / MISSING
    pattern_hits: list[PatternHit]
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "paper_text_signature": self.description,
            "found": self.status,
            "locations": [
                {
                    "pattern": h.pattern,
                    "hits": [{"line": ln, "snippet": snip} for ln, snip in h.locations],
                }
                for h in self.pattern_hits
            ],
            "notes": self.notes,
        }


def stream_lines(path: Path) -> Iterable[tuple[int, str]]:
    """Yield (line_no, line_text) WITHOUT loading the full file.

    This is the truncation-safe pathway: each line is processed once and
    discarded. No 51-page buffer, no stale snapshot.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            yield i, line.rstrip("\n")


def audit_claims(tex_path: Path, claims: list[dict], verbose: bool = False) -> list[ClaimResult]:
    """Single streaming pass over main.tex; multi-pattern matcher per line."""
    # Pre-compile every pattern (deterministic; no DOTALL — we match per line).
    compiled: dict[str, list[tuple[str, re.Pattern]]] = {}
    hits: dict[str, list[PatternHit]] = {}
    for claim in claims:
        compiled[claim["id"]] = [(p, re.compile(p)) for p in claim["patterns"]]
        hits[claim["id"]] = [PatternHit(pattern=p) for p in claim["patterns"]]

    # Single sequential pass.
    for line_no, line in stream_lines(tex_path):
        for claim_id, patterns in compiled.items():
            for idx, (raw, regex) in enumerate(patterns):
                if regex.search(line):
                    snippet = line.strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    hits[claim_id][idx].locations.append((line_no, snippet))

    # Roll up status.
    results: list[ClaimResult] = []
    for claim in claims:
        h = hits[claim["id"]]
        n_total = len(h)
        n_found = sum(1 for ph in h if ph.found)
        if n_found == n_total:
            status = FOUND
            notes = f"All {n_total} signature patterns matched."
        elif n_found == 0:
            status = MISSING
            notes = "No signature patterns matched. Number may have drifted, or claim was removed."
        else:
            status = DRIFT
            missed = [ph.pattern for ph in h if not ph.found]
            notes = f"{n_found}/{n_total} patterns matched. Missing: {missed}"

        result = ClaimResult(
            claim_id=claim["id"],
            description=claim["description"],
            status=status,
            pattern_hits=h,
            notes=notes,
        )
        results.append(result)

        if verbose:
            badge = {FOUND: "[OK]  ", DRIFT: "[WARN]", MISSING: "[MISS]"}[status]
            print(f"  {badge} {claim['id']}: {claim['description']}")
            print(f"         {notes}")
            for ph in h:
                if ph.found:
                    ln, snip = ph.locations[0]
                    print(f"           hit @ line {ln}: {snip}")

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(results: list[ClaimResult], lexicon_terms: list[str]) -> None:
    n_found = sum(1 for r in results if r.status == FOUND)
    n_drift = sum(1 for r in results if r.status == DRIFT)
    n_missing = sum(1 for r in results if r.status == MISSING)

    print("=" * 70)
    print("CLAIM AUDIT REPORT  (Tier 1: 25 critical claims)")
    print("=" * 70)
    print(f"Paper:    {MAIN_TEX}")
    print(f"Registry: {CLAIMS_MD}")
    print(f"Lexicon:  {LEXICON_MD}  ({len(lexicon_terms)} protected terms loaded)")
    print()
    print(f"  Verbatim found : {n_found:>2} / 25")
    print(f"  Drift warning  : {n_drift:>2} / 25")
    print(f"  Missing        : {n_missing:>2} / 25")
    print()

    if n_drift or n_missing:
        print("-" * 70)
        print("Claims requiring human review:")
        print("-" * 70)
        for r in results:
            if r.status == FOUND:
                continue
            badge = "[WARN]" if r.status == DRIFT else "[MISS]"
            print(f"  {badge} {r.claim_id}: {r.description}")
            print(f"         {r.notes}")
        print()

    print("Full per-claim JSON written to:")
    print(f"  {RESULTS_JSON}")
    print()


def write_json(results: list[ClaimResult], lexicon_terms: list[str]) -> None:
    n_found = sum(1 for r in results if r.status == FOUND)
    n_drift = sum(1 for r in results if r.status == DRIFT)
    n_missing = sum(1 for r in results if r.status == MISSING)
    payload = {
        "summary": {
            "total": len(results),
            "found_verbatim": n_found,
            "drift": n_drift,
            "missing": n_missing,
        },
        "paper_path": str(MAIN_TEX),
        "registry_path": str(CLAIMS_MD),
        "lexicon_protected_terms": lexicon_terms,
        "claims": [r.to_dict() for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print per-claim status as it runs")
    args = parser.parse_args(argv)

    if not MAIN_TEX.exists():
        print(f"ERROR: paper not found at {MAIN_TEX}", file=sys.stderr)
        return 2
    if not CLAIMS_MD.exists():
        print(f"ERROR: claim registry not found at {CLAIMS_MD}", file=sys.stderr)
        return 2

    lexicon_terms = load_protected_terms(LEXICON_MD)

    if args.verbose:
        print(f"Loaded {len(lexicon_terms)} protected lexicon terms (read-only).")
        print(f"Streaming {MAIN_TEX} (line-by-line; no full-file buffer)...")
        print()

    results = audit_claims(MAIN_TEX, CLAIMS, verbose=args.verbose)

    print_report(results, lexicon_terms)
    write_json(results, lexicon_terms)

    n_missing = sum(1 for r in results if r.status == MISSING)
    return 1 if n_missing else 0


if __name__ == "__main__":
    sys.exit(main())
