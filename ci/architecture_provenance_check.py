"""Architecture-provenance cert layer.

Verifies four properties of the cert system itself.
1. File naming. Python files in ci/ (excluding ci/audit/) are snake_case
   and every *_check.py has a paired *_results.json.
2. Schema floor. Each ci/*_results.json declares at least 3 of the
   approved top-level keys.
3. PII scan. ci/*.json and ci/*.md files do not contain forbidden tokens.
4. Self-hash provenance. Each *_check.py whose results JSON exists
   should record its own SHA256 in the JSON output.

Pure stdlib. Exits 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CI_DIR = Path(__file__).resolve().parent
REPO_ROOT = CI_DIR.parent
RESULTS_PATH = CI_DIR / "architecture_provenance_results.json"

# Forbidden-pattern scan targets. These are deanonymization tokens for
# the PII scan. They are NOT author identity in this source. They live
# here as literals because the scanner needs them to match.
_FORBIDDEN_PATTERNS = [
    (r"\bpaulc\b", True),
    (r"paultiffany", True),
    (r"cosmogenesis", True),
    (r"agi2026", True),
    (r"principia", True),
    (r"symbolica", True),
    (r"\bttdc\b", True),
    (r"\bsrmf\b", True),
    (r"pylantern", True),
    (r"fascia", True),
    (r"paultiffany@gmail", True),
    (r"C:\\\\Users\\\\paulc", True),
]

# Approved top-level keys for cert outputs. A results JSON passes the
# schema floor if it declares at least three of these.
_SCHEMA_FLOOR_KEYS = {
    "summary", "status", "layer_name", "results", "claims",
    "checks", "timestamp", "verdict", "passed", "failed",
}
_SCHEMA_FLOOR_MIN = 3

# Self-hash key paths the scanner accepts.
_SELF_HASH_PATHS = [
    ("_meta", "script_hash"),
    ("summary", "script_sha256"),
    ("provenance", "script_sha"),
]

_SNAKE_CASE = re.compile(r"^[a-z_][a-z0-9_]*\.py$")


@dataclass
class CheckResult:
    name: str
    status: str = "PASS"
    blockers: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_ci_python_files() -> list[Path]:
    out = []
    for p in sorted(CI_DIR.glob("*.py")):
        if p.is_file():
            out.append(p)
    return out


def _list_ci_json_files() -> list[Path]:
    return sorted([p for p in CI_DIR.glob("*.json") if p.is_file()])


def _list_ci_md_files() -> list[Path]:
    return sorted([p for p in CI_DIR.glob("*.md") if p.is_file()])


def check_file_naming() -> CheckResult:
    result = CheckResult(name="file_naming")
    for py in _list_ci_python_files():
        if not _SNAKE_CASE.match(py.name):
            result.blockers.append(f"non snake_case file: {py.name}")
        if py.name.endswith("_check.py"):
            paired = py.with_name(py.stem + "_results.json")
            alt = py.with_name(py.stem.replace("_check", "") + "_results.json")
            if not paired.exists() and not alt.exists():
                result.advisories.append(
                    f"missing paired results JSON for {py.name}"
                )
    if result.blockers:
        result.status = "FAIL"
    return result


def check_schema_floor() -> CheckResult:
    result = CheckResult(name="schema_floor")
    for jp in _list_ci_json_files():
        if not jp.name.endswith("_results.json"):
            continue
        try:
            with open(jp, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError) as exc:
            result.blockers.append(f"{jp.name}: unreadable ({exc})")
            continue
        if not isinstance(payload, dict):
            result.blockers.append(f"{jp.name}: top level is not an object")
            continue
        present = _SCHEMA_FLOOR_KEYS.intersection(payload.keys())
        if len(present) < _SCHEMA_FLOOR_MIN:
            result.blockers.append(
                f"{jp.name}: only {len(present)} of approved keys "
                f"(need {_SCHEMA_FLOOR_MIN})"
            )
    if result.blockers:
        result.status = "FAIL"
    return result


def _scan_text_for_pii(text: str) -> list[str]:
    found = []
    for raw, _is_hard in _FORBIDDEN_PATTERNS:
        if re.search(raw, text, re.IGNORECASE):
            found.append(raw)
    return found


def check_pii_scan(self_path: Path) -> CheckResult:
    result = CheckResult(name="pii_scan")
    targets = _list_ci_json_files() + _list_ci_md_files()
    if self_path not in targets:
        targets.append(self_path)
    for tp in targets:
        try:
            text = tp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.advisories.append(f"{tp.name}: read error ({exc})")
            continue
        if tp == self_path:
            text = _strip_forbidden_table(text)
        hits = _scan_text_for_pii(text)
        if hits:
            for h in hits:
                result.blockers.append(f"{tp.name}: pattern {h!r}")
    if result.blockers:
        result.status = "FAIL"
    return result


def _strip_forbidden_table(text: str) -> str:
    """Remove the literal _FORBIDDEN_PATTERNS table from self scan.

    The scanner has to embed the patterns to look for them. Their
    presence in this file is by design. Strip the dataclass region
    so a self scan only flags illegitimate occurrences.
    """
    start_marker = "_FORBIDDEN_PATTERNS = ["
    end_marker = "]"
    si = text.find(start_marker)
    if si == -1:
        return text
    ei = text.find(end_marker, si)
    if ei == -1:
        return text
    return text[:si] + text[ei + 1:]


def check_self_hash_provenance() -> CheckResult:
    result = CheckResult(name="self_hash_provenance")
    for py in _list_ci_python_files():
        if not py.name.endswith("_check.py"):
            continue
        results_json = py.with_name(py.stem + "_results.json")
        if not results_json.exists():
            continue
        try:
            with open(results_json, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError):
            result.advisories.append(f"{py.name}: results JSON unreadable")
            continue
        if not _has_self_hash(payload):
            result.advisories.append(
                f"{py.name}: results JSON has no recorded script hash"
            )
    return result


def _has_self_hash(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    for path in _SELF_HASH_PATHS:
        node = payload
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and isinstance(node, str) and len(node) >= 16:
            return True
    return False


def assemble(checks: list[CheckResult], self_hash: str) -> dict:
    blockers = sum(len(c.blockers) for c in checks)
    advisories = sum(len(c.advisories) for c in checks)
    status = "FAIL" if blockers > 0 else "PASS"
    return {
        "_meta": {
            "script": "ci/architecture_provenance_check.py",
            "script_hash": self_hash,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "status": status,
        "summary": {
            "status": status,
            "blockers": blockers,
            "advisories": advisories,
            "checks_run": len(checks),
        },
        "checks": [
            {
                "name": c.name,
                "status": c.status,
                "blockers": c.blockers,
                "advisories": c.advisories,
            }
            for c in checks
        ],
    }


def run() -> int:
    self_path = Path(__file__).resolve()
    self_hash = _sha256_of(self_path)
    checks = [
        check_file_naming(),
        check_schema_floor(),
        check_pii_scan(self_path),
        check_self_hash_provenance(),
    ]
    payload = assemble(checks, self_hash)
    RESULTS_PATH.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Architecture-provenance: {payload['summary']['status']}")
    print(f"  blockers={payload['summary']['blockers']}  "
          f"advisories={payload['summary']['advisories']}")
    for c in checks:
        print(f"  [{c.status}] {c.name}  "
              f"(blockers={len(c.blockers)}, advisories={len(c.advisories)})")
        for b in c.blockers:
            print(f"    BLOCK {b}")
        for a in c.advisories[:5]:
            print(f"    note  {a}")
        if len(c.advisories) > 5:
            print(f"    note  ... and {len(c.advisories) - 5} more")
    print(f"Results written to {RESULTS_PATH}")
    return 0 if payload["summary"]["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet", action="store_true", help="suppress per-check output"
    )
    parser.parse_args()
    return run()


if __name__ == "__main__":
    sys.exit(main())
