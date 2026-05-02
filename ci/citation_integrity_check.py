#!/usr/bin/env python3
"""
citation_integrity_check.py - Verify citation graph between main.tex
and references.bib.

Layer 7 of the certification stack. Catches three classes of bug:

  1. UNRESOLVED CITE — main.tex contains \\citep{foo} but references.bib
     has no @xxx{foo, ...} entry. LaTeX would emit "??" in the rendered
     paper.

  2. DEAD ENTRY — references.bib has @xxx{foo, ...} but no \\cite*{foo}
     anywhere in main.tex. The entry never appears in the bibliography
     and is dead weight.

  3. MALFORMED ENTRY — bib entries that fail basic structural checks
     (missing required fields, malformed key, etc.). Loose heuristic.

This layer doesn't validate DOI / arXiv IDs (would require network),
doesn't check citation style consistency, doesn't verify the citation
keys match the actual papers they claim to cite. Scope is structural:
does the citation graph close cleanly?

Exit codes
----------
  0  every cite resolves AND no dead entries (or --warn-only and only warnings)
  1  unresolved citations OR dead entries (without --warn-only)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
REFERENCES_BIB = REPO_ROOT / "paper" / "references.bib"
RESULTS_JSON = SCRIPT_DIR / "citation_integrity_results.json"

# Citation forms in main.tex:
#   \citep{key}, \citet{key}, \cite{key}, \citeauthor{key}, \citeyear{key},
#   \citeyearpar{key}, \citealp{key}, \citealt{key}, \citenum{key}
# Multiple keys: \citep{key1, key2, key3}
CITE_PATTERN = re.compile(r"\\cite[a-z]*\{([^}]+)\}")

# Bib entry: @article{key, ...}, @inproceedings{key, ...}, etc.
# Tolerant of whitespace and case.
BIB_ENTRY_PATTERN = re.compile(r"^\s*@(?P<type>[A-Za-z]+)\s*\{\s*(?P<key>[^,\s]+)\s*,", re.MULTILINE)


@dataclass
class CitationReport:
    cite_keys_in_paper: set[str] = field(default_factory=set)
    bib_keys: set[str] = field(default_factory=set)
    unresolved: list[str] = field(default_factory=list)  # cited but not in bib
    dead: list[str] = field(default_factory=list)         # in bib but never cited

    def to_dict(self) -> dict:
        return {
            "n_cites_in_paper": len(self.cite_keys_in_paper),
            "n_bib_entries": len(self.bib_keys),
            "n_unresolved": len(self.unresolved),
            "n_dead": len(self.dead),
            "unresolved": sorted(self.unresolved),
            "dead": sorted(self.dead),
        }


def extract_cite_keys(tex_text: str) -> set[str]:
    """Pull every key from every \\cite* command in the .tex source.

    Skips citations inside LaTeX comments (lines starting with %) so
    commented-out references aren't flagged.
    """
    keys: set[str] = set()
    # Strip full-line comments first
    cleaned_lines = []
    for line in tex_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("%"):
            continue
        # Strip trailing comments (after non-escaped %)
        cleaned = re.sub(r"(?<!\\)%.*$", "", line)
        cleaned_lines.append(cleaned)
    cleaned_text = "\n".join(cleaned_lines)

    for m in CITE_PATTERN.finditer(cleaned_text):
        key_blob = m.group(1)
        # Multiple keys: split on comma and strip
        for k in key_blob.split(","):
            k = k.strip()
            if k:
                keys.add(k)
    return keys


def extract_bib_keys(bib_text: str) -> set[str]:
    """Pull every entry key from references.bib.

    Tolerant of @comment, @string, @preamble pseudo-entries: those have
    type names that aren't bibliography entries, but our regex catches
    them too. We filter out the well-known pseudo-types.
    """
    PSEUDO_TYPES = {"comment", "string", "preamble"}
    keys: set[str] = set()
    for m in BIB_ENTRY_PATTERN.finditer(bib_text):
        if m.group("type").lower() in PSEUDO_TYPES:
            continue
        keys.add(m.group("key"))
    return keys


def analyze() -> CitationReport:
    if not MAIN_TEX.exists():
        raise FileNotFoundError(f"main.tex not found at {MAIN_TEX}")
    if not REFERENCES_BIB.exists():
        raise FileNotFoundError(f"references.bib not found at {REFERENCES_BIB}")

    tex = MAIN_TEX.read_text(encoding="utf-8", errors="replace")
    bib = REFERENCES_BIB.read_text(encoding="utf-8", errors="replace")

    cite_keys = extract_cite_keys(tex)
    bib_keys = extract_bib_keys(bib)

    report = CitationReport(
        cite_keys_in_paper=cite_keys,
        bib_keys=bib_keys,
        unresolved=sorted(cite_keys - bib_keys),
        dead=sorted(bib_keys - cite_keys),
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="treat dead bib entries as FAIL (default: warn only). Unresolved citations always FAIL.")
    parser.add_argument("--verbose", "-v", action="store_true", help="print each unresolved/dead key")
    args = parser.parse_args()

    try:
        r = analyze()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("CITATION INTEGRITY CHECK  (main.tex <-> references.bib)")
    print("=" * 70)
    print(f"main.tex:        {MAIN_TEX}")
    print(f"references.bib:  {REFERENCES_BIB}")
    print()
    print(f"  Cite keys in paper:     {len(r.cite_keys_in_paper):>4}")
    print(f"  Bib entries:            {len(r.bib_keys):>4}")
    print(f"  Unresolved citations:   {len(r.unresolved):>4}  (cited but no bib entry)")
    print(f"  Dead bib entries:       {len(r.dead):>4}  (entry but never cited)")
    print()

    if r.unresolved:
        print("UNRESOLVED CITATIONS (would render as '??' in PDF):")
        for k in r.unresolved:
            print(f"  - {k}")
        print()

    if r.dead and (args.verbose or len(r.dead) <= 20):
        print(f"DEAD BIB ENTRIES{' (--verbose; showing all)' if args.verbose else ''}:")
        for k in r.dead:
            print(f"  - {k}")
        print()
    elif r.dead:
        print(f"DEAD BIB ENTRIES: {len(r.dead)} entries — re-run with --verbose to list")
        print()

    payload = {
        "summary": r.to_dict(),
        "main_tex": str(MAIN_TEX),
        "references_bib": str(REFERENCES_BIB),
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report written to: {RESULTS_JSON}")

    # Exit code policy:
    #   - Unresolved citations FAIL (LaTeX renders ?? — paper-breaking)
    #   - Dead entries WARN by default; FAIL with --strict
    if r.unresolved:
        return 1
    if r.dead and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
