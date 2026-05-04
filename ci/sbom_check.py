"""SBOM lineage check.

Verifies three properties of the dependency manifest:

  (a) every package pinned in requirements.lock.txt has a
      corresponding entry in ci/sbom_manifest.json,
  (b) every entry in the manifest has a non-empty license field,
  (c) the lockfile parses cleanly (each non-comment line is
      either blank or a `name==version` pin).

Exit code 0 if all three hold, 1 otherwise. Stdlib only.
Belongs to the provenance suite.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements.lock.txt"
MANIFEST = REPO_ROOT / "ci" / "sbom_manifest.json"
RESULTS = REPO_ROOT / "ci" / "sbom_check_results.json"

PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-+]+)\s*$")


def parse_lockfile(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return (name->version, parse_errors)."""
    pins: dict[str, str] = {}
    errors: list[str] = []
    if not path.exists():
        errors.append(f"lockfile not found: {path}")
        return pins, errors
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN_RE.match(line)
        if not m:
            errors.append(f"line {lineno}: not a `name==version` pin: {raw!r}")
            continue
        name = m.group(1).lower()
        if name in pins:
            errors.append(f"line {lineno}: duplicate pin for {name}")
            continue
        pins[name] = m.group(2)
    return pins, errors


def load_manifest(path: Path) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    if not path.exists():
        errors.append(f"manifest not found: {path}")
        return [], errors
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"manifest JSON parse error: {exc}")
        return [], errors
    pkgs = data.get("packages")
    if not isinstance(pkgs, list):
        errors.append("manifest missing top-level `packages` array")
        return [], errors
    return pkgs, errors


def main() -> int:
    pins, lock_errors = parse_lockfile(LOCKFILE)
    pkgs, manifest_errors = load_manifest(MANIFEST)

    manifest_index = {
        (p.get("name") or "").lower(): p for p in pkgs if isinstance(p, dict)
    }

    missing_in_manifest = sorted(n for n in pins if n not in manifest_index)
    license_gaps = sorted(
        name
        for name, entry in manifest_index.items()
        if not (entry.get("license") or "").strip()
    )

    ok = (
        not lock_errors
        and not manifest_errors
        and not missing_in_manifest
        and not license_gaps
    )

    summary = {
        "lockfile": str(LOCKFILE.relative_to(REPO_ROOT)),
        "manifest": str(MANIFEST.relative_to(REPO_ROOT)),
        "pinned_count": len(pins),
        "manifest_count": len(manifest_index),
        "missing_in_manifest": missing_in_manifest,
        "license_gaps": license_gaps,
        "lockfile_parse_errors": lock_errors,
        "manifest_errors": manifest_errors,
        "verdict": "PASS" if ok else "FAIL",
    }

    try:
        RESULTS.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as exc:
        # results write is best effort; the exit code still reflects checks
        print(f"warning: could not write results JSON: {exc}", file=sys.stderr)

    if ok:
        print(
            f"SBOM check PASS: {len(pins)} pinned, "
            f"{len(manifest_index)} manifest entries, all licensed."
        )
        return 0

    print("SBOM check FAIL", file=sys.stderr)
    for err in lock_errors:
        print(f"  lockfile: {err}", file=sys.stderr)
    for err in manifest_errors:
        print(f"  manifest: {err}", file=sys.stderr)
    for name in missing_in_manifest:
        print(f"  missing in manifest: {name}", file=sys.stderr)
    for name in license_gaps:
        print(f"  empty license: {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
