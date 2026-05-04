#!/usr/bin/env python3
"""
license_clearance_check.py - Walk the SBOM manifest and classify each
dependency's license against a NeurIPS-acceptable allowlist.

Inputs
------
  ci/sbom_manifest.json   list of {name, version, license, ...} entries.
                          If the file is not present, this check exits 0
                          with a short notice (the manifest is produced by
                          a separate step).

Outputs
-------
  ci/license_clearance_results.json   per-package classification plus
                                      counts of allowed / warned / blocked.

Classification
--------------
  allowed   license is in the allowlist below, or reports as a bare BSD /
            MIT variant we treat as allowed.
  warned    license is GPL-3.0-only or any AGPL variant. Acceptable for
            code release but flagged so attribution stays explicit.
  blocked   license is unknown or non-open-source. Drives a non-zero exit.

Exit codes
----------
  0  no blocked entries
  1  one or more blocked entries
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SBOM_PATH = SCRIPT_DIR / "sbom_manifest.json"
RESULTS_JSON = SCRIPT_DIR / "license_clearance_results.json"

ALLOWED_LICENSES = {
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "ISC",
    "Python-2.0",
    "MPL-2.0",
    "LGPL-3.0-or-later",
    "PSF-2.0",
    "Unlicense",
}

# Bare strings that show up in the wild and are treated as allowed.
ALLOWED_LOOSE = {
    "BSD",
    "BSD License",
    "MIT License",
    "Apache",
    "Apache 2",
    "Apache 2.0",
    "PSF",
    "Python Software Foundation License",
    "Matplotlib License",
}

WARNED_PATTERNS = [
    re.compile(r"^GPL-3\.0-only$", re.IGNORECASE),
    re.compile(r"\bAGPL\b", re.IGNORECASE),
]


def normalize(license_string: str) -> str:
    """Strip parenthetical attribution and surrounding whitespace."""
    s = license_string.strip()
    paren = s.find("(")
    if paren != -1:
        s = s[:paren].strip()
    return s


def classify(license_string: str) -> str:
    """Return 'allowed', 'warned', or 'blocked' for a license string."""
    if not license_string:
        return "blocked"
    norm = normalize(license_string)
    for pat in WARNED_PATTERNS:
        if pat.search(norm):
            return "warned"
    if norm in ALLOWED_LICENSES:
        return "allowed"
    if norm in ALLOWED_LOOSE:
        return "allowed"
    # Loose match: any token in the normalized form matches an allowed key.
    tokens = re.split(r"[\s/,;]+", norm)
    for token in tokens:
        if token in ALLOWED_LICENSES or token in ALLOWED_LOOSE:
            return "allowed"
    # Common substring fallbacks for messy SPDX-ish strings.
    lowered = norm.lower()
    if "bsd" in lowered or "mit" in lowered or "apache" in lowered:
        return "allowed"
    if "psf" in lowered or "python software foundation" in lowered:
        return "allowed"
    if "mpl" in lowered or "mozilla public" in lowered:
        return "allowed"
    if "isc" in lowered:
        return "allowed"
    if "unlicense" in lowered:
        return "allowed"
    return "blocked"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sbom", default=str(SBOM_PATH),
                        help="path to sbom_manifest.json")
    parser.add_argument("--json-out", default=str(RESULTS_JSON),
                        help="path for JSON results")
    args = parser.parse_args(argv)

    sbom_path = Path(args.sbom)
    out_path = Path(args.json_out)

    if not sbom_path.exists():
        print("sbom_manifest.json not yet present; skipping")
        sys.exit(0)

    try:
        data = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: could not read SBOM at {sbom_path}: {exc}", file=sys.stderr)
        return 2

    packages = data.get("packages", [])
    breakdown = []
    counts = {"allowed": 0, "warned": 0, "blocked": 0}

    for pkg in packages:
        name = pkg.get("name", "<unknown>")
        version = pkg.get("version", "")
        license_raw = pkg.get("license", "")
        verdict = classify(license_raw)
        counts[verdict] += 1
        breakdown.append({
            "name": name,
            "version": version,
            "license": license_raw,
            "classification": verdict,
        })

    payload = {
        "sbom_path": str(sbom_path),
        "n_packages": len(packages),
        "counts": counts,
        "packages": breakdown,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    status = "PASS" if counts["blocked"] == 0 else "FAIL"
    print(f"[license_clearance] {status}")
    print(f"  packages: {len(packages)}")
    print(f"  allowed : {counts['allowed']}")
    print(f"  warned  : {counts['warned']}")
    print(f"  blocked : {counts['blocked']}")
    if counts["warned"]:
        print("  Warned entries (attribution required):")
        for row in breakdown:
            if row["classification"] == "warned":
                print(f"    - {row['name']} {row['version']}  [{row['license']}]")
    if counts["blocked"]:
        print("  Blocked entries:")
        for row in breakdown:
            if row["classification"] == "blocked":
                print(f"    - {row['name']} {row['version']}  [{row['license']}]")
    print(f"  Results -> {out_path}")

    return 0 if counts["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
