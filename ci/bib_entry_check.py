#!/usr/bin/env python3
"""
bib_entry_check.py - Structural validation of references.bib entries.

Layer 10 of the certification stack. Complement to L7 (which only
checks the cite/bib graph closure). L10 looks INSIDE each bib entry
and verifies it has the structural fields needed to render a
well-formed bibliography.

For each entry, by type:
  - article:       title, author, journal, year
  - inproceedings: title, author, booktitle, year
  - book:          title, author OR editor, publisher, year
  - techreport:    title, author, institution, year
  - misc:          title, author OR howpublished
  - thesis:        title, author, school, year

Plus universal checks:
  - The entry has a key (already enforced by parser)
  - The year, if present, is a 4-digit value in a sensible range
  - If `url` field present, it's well-formed
  - If `doi` field present, it matches DOI syntax

Out of scope: verifying that the cited paper actually exists at
that DOI/URL (network), or that title/author match the canonical
record. Those would require live API calls (CrossRef, Semantic
Scholar, etc.) and aren't deadline-friendly.

Exit codes
----------
  0  every entry well-formed
  1  one or more entries malformed (missing required field, bad year,
     malformed URL/DOI)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
REFERENCES_BIB = REPO_ROOT / "paper" / "references.bib"
RESULTS_JSON = SCRIPT_DIR / "bib_entry_check_results.json"

# Entry-type → required fields (lower-case set)
REQUIRED_FIELDS = {
    "article":       {"title", "author", "journal", "year"},
    "inproceedings": {"title", "author", "booktitle", "year"},
    "incollection":  {"title", "author", "booktitle", "year"},
    "conference":    {"title", "author", "booktitle", "year"},
    "book":          {"title", "publisher", "year"},  # author OR editor checked separately
    "inbook":        {"title", "publisher", "year"},
    "techreport":    {"title", "author", "institution", "year"},
    "manual":        {"title"},
    "misc":          {"title"},  # author OR howpublished checked separately
    "online":        {"title"},
    "phdthesis":     {"title", "author", "school", "year"},
    "mastersthesis": {"title", "author", "school", "year"},
    "unpublished":   {"title", "author", "note"},
}

# Entry types where we check author OR alternative
EITHER_OR_CHECKS = {
    "book":   ("author", "editor"),
    "inbook": ("author", "editor"),
    "misc":   ("author", "howpublished"),
    "online": ("author", "howpublished"),
}

DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
URL_RE = re.compile(r"^https?://[^\s]+$")
YEAR_RE = re.compile(r"^\s*\{?(\d{4})\}?\s*,?\s*$")
ENTRY_HEADER = re.compile(r"^\s*@(?P<type>[A-Za-z]+)\s*\{\s*(?P<key>[^,\s]+)\s*,", re.MULTILINE)
PSEUDO_TYPES = {"comment", "string", "preamble"}


@dataclass
class EntryReport:
    key: str
    entry_type: str
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_entries(text: str) -> list[tuple[str, str, dict[str, str]]]:
    """Return [(key, type, {field: value, ...}), ...].

    Tolerant parser: finds @type{key, ... } blocks, balances braces
    naively to find the entry boundary, then splits fields by
    top-level commas.
    """
    out: list[tuple[str, str, dict[str, str]]] = []
    for m in ENTRY_HEADER.finditer(text):
        etype = m.group("type").lower()
        if etype in PSEUDO_TYPES:
            continue
        key = m.group("key")
        # Find matching close brace
        i = m.end()
        depth = 1  # we've already entered the outer @type{
        while i < len(text) and depth > 0:
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        body = text[m.end():i - 1]  # interior of the entry
        # Split fields at top-level commas (depth 0 within body)
        fields: dict[str, str] = {}
        depth = 0
        cur = []
        for c in body:
            if c == "{":
                depth += 1
                cur.append(c)
            elif c == "}":
                depth -= 1
                cur.append(c)
            elif c == "," and depth == 0:
                token = "".join(cur).strip()
                if "=" in token:
                    name, _, value = token.partition("=")
                    fields[name.strip().lower()] = value.strip()
                cur = []
            else:
                cur.append(c)
        token = "".join(cur).strip()
        if "=" in token:
            name, _, value = token.partition("=")
            fields[name.strip().lower()] = value.strip()
        out.append((key, etype, fields))
    return out


def check_entry(key: str, etype: str, fields: dict[str, str]) -> EntryReport:
    r = EntryReport(key=key, entry_type=etype)

    required = REQUIRED_FIELDS.get(etype)
    if required is None:
        r.issues.append(f"unknown entry type '{etype}' (no required-field rule)")
    else:
        missing = required - set(fields.keys())
        if missing:
            r.issues.append(f"missing required fields: {sorted(missing)}")

    # Either-or checks
    if etype in EITHER_OR_CHECKS:
        opt1, opt2 = EITHER_OR_CHECKS[etype]
        if opt1 not in fields and opt2 not in fields:
            r.issues.append(f"needs '{opt1}' OR '{opt2}'")

    # Year sanity
    if "year" in fields:
        m = YEAR_RE.match(fields["year"].strip().rstrip(","))
        if not m:
            r.issues.append(f"year value not parseable: {fields['year']!r}")
        else:
            year = int(m.group(1))
            if not (1900 <= year <= 2030):
                r.issues.append(f"year out of plausible range: {year}")

    # URL / DOI shape
    if "url" in fields:
        url = fields["url"].strip().strip("{}").strip(",").strip().strip("{}")
        if url and not URL_RE.match(url):
            r.issues.append(f"url malformed: {url!r}")
    if "doi" in fields:
        doi = fields["doi"].strip().strip("{}").strip(",").strip().strip("{}")
        # Strip leading "https://doi.org/" if present
        if doi.lower().startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]
        elif doi.lower().startswith("doi:"):
            doi = doi[len("doi:"):]
        if doi and not DOI_RE.match(doi):
            r.issues.append(f"doi shape malformed: {doi!r}")

    return r


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="treat unknown entry-types as FAIL (default: WARN)")
    parser.add_argument("--verbose", "-v", action="store_true", help="print every entry's status")
    args = parser.parse_args()

    if not REFERENCES_BIB.exists():
        print(f"ERROR: references.bib not found at {REFERENCES_BIB}", file=sys.stderr)
        return 2

    text = REFERENCES_BIB.read_text(encoding="utf-8", errors="replace")
    entries = parse_entries(text)

    print("=" * 70)
    print("BIB ENTRY CHECK  (structural validation of references.bib)")
    print("=" * 70)
    print(f"references.bib:  {REFERENCES_BIB}")
    print(f"Entries parsed:  {len(entries)}")
    print()

    reports: list[EntryReport] = []
    for key, etype, fields in entries:
        r = check_entry(key, etype, fields)
        reports.append(r)
        if args.verbose or r.issues:
            badge = "[OK]" if not r.issues else "[ISSUE]"
            print(f"  {badge} @{etype}{{{key}}}")
            for issue in r.issues:
                print(f"         {issue}")

    n_ok = sum(1 for r in reports if not r.issues)
    n_issue = len(reports) - n_ok
    print()
    print("-" * 70)
    print(f"  Entries OK:     {n_ok:>3} / {len(reports)}")
    print(f"  Entries with issues: {n_issue:>3}")
    print("-" * 70)

    payload = {
        "summary": {
            "total": len(reports),
            "ok": n_ok,
            "with_issues": n_issue,
        },
        "issues": [r.to_dict() for r in reports if r.issues],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    return 0 if n_issue == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
