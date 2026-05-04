#!/usr/bin/env python3
"""
cert_anonymity_check.py - Scan the cert payload for author-identity leaks.

The cert ships with the supplementary; any leak deanonymizes the double-blind
submission. This check scans all cert files and key result JSONs.

Files scanned
-------------
  Primary cert payload (blocker severity if matched):
    ci/claim_certificate.json
    ci/claim_certificate.md

  Key result JSONs (warn if leak, info if only in non-shipping paths):
    ci/claim_data_ties_results.json
    ci/claim_audit_results.json
    ci/illustration_lineage_results.json
    ci/figure_lineage_results.json  (if exists)

  _meta source_file paths referenced in claim_data_ties.json

Identifiers searched (case-insensitive unless otherwise noted)
--------------------------------------------------------------
  PaulTiffany, Paul Tiffany, paulctiffany, Paul C. Tiffany, Tiffany (surname)
  paulctiffany@gmail.com or any @gmail.com address
  paulc as a username path component  (e.g. /Users/paulc/ or C:\\Users\\paulc\\)
  Principia Symbolica, PrincipiaSymbolica
  PyLantern, Py-Lantern, Fascia
  AGI-26, AGI26  (allowed only inside references.bib; flag everywhere else in cert)
  Cosmic Engineers, Order of Cosmic Engineers

Severity
--------
  blocker - match in cert payload (claim_certificate.json/.md)
  warn    - match in a result JSON _meta block that ships in supplementary
  info    - match in a path/field that would not ship

NOTE: This script intentionally does NOT print the leaking values to stdout.
Only severity counts + filenames are logged. The JSON output has full details.

Exit codes
----------
  0  no blockers found
  1  one or more blockers found
  2  invocation error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

RESULTS_JSON = SCRIPT_DIR / "cert_anonymity_results.json"

# ---------------------------------------------------------------------------
# Files to scan and their baseline severity
# ---------------------------------------------------------------------------

CERT_PAYLOAD_FILES = [
    SCRIPT_DIR / "claim_certificate.json",
    SCRIPT_DIR / "claim_certificate.md",
]

RESULT_JSON_FILES = [
    SCRIPT_DIR / "claim_data_ties_results.json",
    SCRIPT_DIR / "claim_audit_results.json",
    SCRIPT_DIR / "illustration_lineage_results.json",
    SCRIPT_DIR / "figure_lineage_results.json",  # optional
]

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Each entry: (pattern_id, compiled_regex, default_severity)
# severity may be overridden per-file context.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # Personal name variants
    ("name_paul_tiffany_concat",
     re.compile(r"PaulTiffany", re.IGNORECASE),
     "blocker"),
    ("name_paul_tiffany_spaced",
     re.compile(r"Paul\s+Tiffany", re.IGNORECASE),
     "blocker"),
    ("name_paul_c_tiffany",
     re.compile(r"Paul\s+C\.?\s+Tiffany", re.IGNORECASE),
     "blocker"),
    ("name_paulctiffany_word",
     re.compile(r"\bpaulctiffany\b", re.IGNORECASE),
     "blocker"),
    # "Tiffany" alone -- only flag as surname (word boundary), not inside
    # words like "Tiffany's" if that context isn't relevant; still blocker.
    ("name_tiffany_surname",
     re.compile(r"\bTiffany\b", re.IGNORECASE),
     "blocker"),

    # Email
    ("email_paulctiffany",
     re.compile(r"paulctiffany@gmail\.com", re.IGNORECASE),
     "blocker"),
    ("email_any_gmail",
     re.compile(r"[A-Za-z0-9._%+\-]+@gmail\.com", re.IGNORECASE),
     "blocker"),

    # Username path component  (Windows or POSIX)
    ("path_paulc_windows",
     re.compile(r"[Cc]:\\[Uu]sers\\paulc\\", re.IGNORECASE),
     "blocker"),
    ("path_paulc_posix",
     re.compile(r"/[Uu]sers/paulc/", re.IGNORECASE),
     "blocker"),
    ("path_paulc_home",
     re.compile(r"~[\\/]paulc[\\/]", re.IGNORECASE),
     "blocker"),
    # "paulc" as a bare username in any path-like context
    ("username_paulc_generic",
     re.compile(r"\bpaulc\b", re.IGNORECASE),
     "warn"),

    # Project identifiers that link to author
    ("project_principia_symbolica",
     re.compile(r"Principia\s*Symbolica", re.IGNORECASE),
     "blocker"),
    ("project_pylantern",
     re.compile(r"Py[\-]?Lantern", re.IGNORECASE),
     "blocker"),
    ("project_fascia",
     re.compile(r"\bFascia\b", re.IGNORECASE),
     "blocker"),
    ("project_cosmic_engineers",
     re.compile(r"(Order\s+of\s+)?Cosmic\s+Engineers", re.IGNORECASE),
     "blocker"),

    # AGI-26/AGI26 -- allowed in references.bib citation; flag anywhere in cert
    ("agi26_hyphen",
     re.compile(r"\bAGI[\-]?26\b", re.IGNORECASE),
     "warn"),  # severity set per-file for cert payload -> blocker
]


class Finding(NamedTuple):
    file: str
    severity: str
    match: str         # the matched text (stored in JSON, not printed to stdout)
    pattern_id: str
    location: str      # line number or JSON path hint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scan_text_file(path: Path, base_severity: str) -> list[Finding]:
    """Scan a plain-text (or markdown) file line by line."""
    findings: list[Finding] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings

    rel = str(path.relative_to(REPO_ROOT))

    for lineno, line in enumerate(lines, start=1):
        for pat_id, pattern, pat_sev in _PATTERNS:
            for m in pattern.finditer(line):
                sev = base_severity if base_severity == "blocker" else pat_sev
                # AGI-26 in cert payload is a blocker
                if pat_id.startswith("agi26") and base_severity == "blocker":
                    sev = "blocker"
                findings.append(Finding(
                    file=rel,
                    severity=sev,
                    match=m.group(0),
                    pattern_id=pat_id,
                    location=f"line {lineno}",
                ))

    return findings


def _scan_json_value(value: object, path_hint: str,
                     rel_file: str, base_severity: str) -> list[Finding]:
    """Recursively scan JSON values (dicts, lists, strings)."""
    findings: list[Finding] = []

    if isinstance(value, str):
        for pat_id, pattern, pat_sev in _PATTERNS:
            for m in pattern.finditer(value):
                sev = base_severity if base_severity == "blocker" else pat_sev
                if pat_id.startswith("agi26") and base_severity == "blocker":
                    sev = "blocker"
                findings.append(Finding(
                    file=rel_file,
                    severity=sev,
                    match=m.group(0),
                    pattern_id=pat_id,
                    location=path_hint,
                ))
    elif isinstance(value, dict):
        for k, v in value.items():
            findings.extend(
                _scan_json_value(v, f"{path_hint}.{k}", rel_file, base_severity)
            )
    elif isinstance(value, list):
        for i, item in enumerate(value):
            findings.extend(
                _scan_json_value(item, f"{path_hint}[{i}]", rel_file, base_severity)
            )

    return findings


def _scan_json_file(path: Path, base_severity: str) -> list[Finding]:
    """Parse and recursively scan a JSON file."""
    findings: list[Finding] = []
    if not path.exists():
        return findings

    rel = str(path.relative_to(REPO_ROOT))
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        # Fall back to line scan
        return _scan_text_file(path, base_severity)

    findings.extend(_scan_json_value(data, "$", rel, base_severity))
    return findings


def _collect_source_files_from_data_ties() -> list[Path]:
    """
    Read ci/claim_data_ties.json and collect the source_file paths
    listed in each claim's _meta-equivalent fields.
    These paths may contain absolute paths that leak username.
    """
    data_ties_path = SCRIPT_DIR / "claim_data_ties.json"
    paths: list[Path] = []
    if not data_ties_path.exists():
        return paths
    try:
        data = json.loads(data_ties_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return paths

    claims = data.get("claims", {})
    for claim_name, claim_data in claims.items():
        if isinstance(claim_data, dict):
            sf = claim_data.get("source_file")
            if sf:
                candidate = REPO_ROOT / sf
                if candidate.exists():
                    paths.append(candidate)
    return paths


def _deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Remove exact duplicates (same file+location+pattern+match)."""
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.file, f.location, f.pattern_id, f.match.lower())
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default=str(RESULTS_JSON),
                        help="Path for JSON output (default: ci/cert_anonymity_results.json)")
    args = parser.parse_args(argv)
    out_path = Path(args.json_out)

    all_findings: list[Finding] = []

    # 1. Cert payload files -> blocker severity
    for cert_file in CERT_PAYLOAD_FILES:
        if cert_file.suffix == ".json":
            all_findings.extend(_scan_json_file(cert_file, base_severity="blocker"))
        else:
            all_findings.extend(_scan_text_file(cert_file, base_severity="blocker"))

    # 2. Key result JSONs -> warn severity for _meta/path leaks
    for result_file in RESULT_JSON_FILES:
        if result_file.exists():
            all_findings.extend(_scan_json_file(result_file, base_severity="warn"))

    # 3. Source files referenced in claim_data_ties.json _meta blocks
    #    These typically wouldn't ship, so severity -> info, unless they contain
    #    a field that would surface in the cert.
    for src_file in _collect_source_files_from_data_ties():
        all_findings.extend(_scan_json_file(src_file, base_severity="info"))

    all_findings = _deduplicate_findings(all_findings)

    # Summarise
    blocker_count = sum(1 for f in all_findings if f.severity == "blocker")
    warn_count    = sum(1 for f in all_findings if f.severity == "warn")
    info_count    = sum(1 for f in all_findings if f.severity == "info")
    passed        = blocker_count == 0

    summary = {
        "blocker": blocker_count,
        "warn": warn_count,
        "info": info_count,
        "passed": passed,
    }

    findings_list = [
        {
            "file": f.file,
            "severity": f.severity,
            "match": f.match,
            "pattern_id": f.pattern_id,
            "location": f.location,
        }
        for f in all_findings
    ]

    result = {"summary": summary, "findings": findings_list}
    out_path.write_text(json.dumps(result, indent=2))

    # Console output -- intentionally does NOT print leaking values
    status = "PASS" if passed else "FAIL"
    print(f"[cert_anonymity] {status}")
    print(f"  blocker : {blocker_count}")
    print(f"  warn    : {warn_count}")
    print(f"  info    : {info_count}")

    if all_findings:
        # Group by file+severity for summary (no leaking values)
        from collections import Counter
        file_sev_counts: Counter[tuple[str, str]] = Counter()
        for f in all_findings:
            file_sev_counts[(f.file, f.severity)] += 1
        print("  Findings by file:")
        for (fp, sev), cnt in sorted(file_sev_counts.items()):
            print(f"    [{sev:7s}] {fp}  ({cnt} match{'es' if cnt != 1 else ''})")

    print(f"  Results -> {out_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
