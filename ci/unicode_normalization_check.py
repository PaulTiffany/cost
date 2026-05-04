"""
unicode_normalization_check.py -- Scan generated cert markdown files for mojibake
characters and replacement glyphs.

Files scanned:
  - ci/claim_certificate.md
  - ci/claim_certificate_reviewer.md
  - ci/CERTIFICATE_CHANGELOG.md

Detects:
  - U+FFFD (Unicode replacement character)
  - Common windows-1252 -> utf-8 mojibake sequences
  - BOM (U+FEFF)
  - Unprintable control characters (except normal whitespace)

Output:
  - ci/unicode_normalization_results.json
  - Human-readable summary to stdout

Exit codes:
  0 - PASS (no issues found)
  1 - FAIL (issues found)
  2 - invocation / unexpected error

Usage:
  python ci/unicode_normalization_check.py
"""

import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"

TARGET_FILES = [
    CI_DIR / "claim_certificate.md",
    CI_DIR / "claim_certificate_reviewer.md",
    CI_DIR / "CERTIFICATE_CHANGELOG.md",
]

# Common windows-1252 -> utf-8 mojibake sequences (the bytes were decoded as latin-1
# then re-encoded as utf-8, producing multi-byte runs).
# Format: (display_name, substring_to_search)
MOJIBAKE_PATTERNS = [
    ("smart_apostrophe_right",  "â"),   # â€™  = '
    ("smart_quote_open",        "â"),   # â€œ  = "
    ("smart_quote_close",       "â"),   # â€   = "
    ("em_dash",                 "â"),   # â€"  = —
    ("en_dash",                 "â"),   # â€"  = –
    ("ellipsis",                "â¦"),   # â€¦  = …
    ("bullet",                  "â¢"),   # â€¢  = •
    ("smart_apostrophe_left",   "â"),   # â€˜  = '
    ("nbsp_mojibake",           "Â "),         # Â    = NBSP
    ("registered_mojibake",     "Â®"),         # Â®   = ®
    ("copyright_mojibake",      "Â©"),         # Â©   = ©
    ("degree_mojibake",         "Â°"),         # Â°   = °
]

REPLACEMENT_CHAR = "�"
BOM_CHAR = "﻿"


def is_unprintable_control(ch: str) -> bool:
    """Return True for control characters that should not appear in markdown prose."""
    if ch in ("\t", "\n", "\r"):
        return False
    cat = unicodedata.category(ch)
    return cat in ("Cc", "Cf") and ch not in (BOM_CHAR,)


def scan_file(path: Path) -> list[dict]:
    """Scan a single file and return a list of finding dicts."""
    findings = []

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        findings.append({
            "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "line": 0,
            "issue": "utf8_decode_error",
            "snippet": str(exc)[:200],
        })
        return findings

    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")

    for lineno, line in enumerate(text.splitlines(), start=1):
        # 1. Replacement character
        if REPLACEMENT_CHAR in line:
            findings.append({
                "file": rel,
                "line": lineno,
                "issue": "replacement_char_U+FFFD",
                "snippet": _snippet(line, REPLACEMENT_CHAR),
            })

        # 2. BOM in middle of file
        if BOM_CHAR in line and not (lineno == 1 and line.startswith(BOM_CHAR)):
            findings.append({
                "file": rel,
                "line": lineno,
                "issue": "BOM_U+FEFF",
                "snippet": _snippet(line, BOM_CHAR),
            })

        # 3. Mojibake sequences
        for pattern_name, pattern in MOJIBAKE_PATTERNS:
            if pattern in line:
                findings.append({
                    "file": rel,
                    "line": lineno,
                    "issue": f"mojibake_{pattern_name}",
                    "snippet": _snippet(line, pattern),
                })

        # 4. Unprintable controls
        for pos, ch in enumerate(line):
            if is_unprintable_control(ch):
                findings.append({
                    "file": rel,
                    "line": lineno,
                    "issue": f"unprintable_control_U+{ord(ch):04X}",
                    "snippet": repr(line[max(0, pos - 10): pos + 10]),
                })
                break  # one finding per line for controls

    # Check for BOM at very start of file
    if text.startswith(BOM_CHAR):
        findings.insert(0, {
            "file": rel,
            "line": 1,
            "issue": "BOM_at_file_start_U+FEFF",
            "snippet": repr(text[:20]),
        })

    return findings


def _snippet(line: str, match: str) -> str:
    idx = line.find(match)
    if idx < 0:
        return ""
    start = max(0, idx - 20)
    end = min(len(line), idx + len(match) + 20)
    return repr(line[start:end])


def main() -> int:
    try:
        all_findings: list[dict] = []
        total_files = 0
        passed = 0
        failed = 0
        skipped = 0

        for target in TARGET_FILES:
            if not target.exists():
                skipped += 1
                continue

            total_files += 1
            findings = scan_file(target)
            if findings:
                failed += 1
                all_findings.extend(findings)
            else:
                passed += 1

        summary = {
            "total_files": total_files,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total_findings": len(all_findings),
        }

        result = {
            "_meta": {
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            },
            "summary": summary,
            "findings": all_findings,
        }

        out_path = CI_DIR / "unicode_normalization_results.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

        # Human-readable output
        if failed == 0:
            status = "PASS"
            print(f"PASS  unicode_normalization_check: {total_files} files scanned, "
                  f"0 findings ({skipped} skipped/absent)")
        else:
            status = "FAIL"
            print(f"FAIL  unicode_normalization_check: {failed}/{total_files} files "
                  f"have issues, {len(all_findings)} total findings")
            for f in all_findings[:20]:
                print(f"  {f['file']}:{f['line']}  [{f['issue']}]  {f['snippet']}")
            if len(all_findings) > 20:
                print(f"  ... ({len(all_findings) - 20} more findings in JSON)")

        print(f"  Results written to {out_path}")
        return 0 if failed == 0 else 1

    except Exception as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
