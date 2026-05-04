"""Container lineage check.

Verifies three properties of the repo's container recipe:

  (a) Dockerfile exists at the repo root,
  (b) Dockerfile references requirements.lock.txt,
  (c) the Python version pinned in the Dockerfile matches the
      Python version recorded in ci/claim_certificate.json under
      profile.python_version (or, if that key is absent, under
      provenance.dependencies.python.version_short). If neither
      key is present, the version comparison is skipped.

Exit code 0 if all applicable checks hold, 1 otherwise.
Stdlib only. Belongs to the artifact_lineage suite.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
CERT_JSON = REPO_ROOT / "ci" / "claim_certificate.json"
RESULTS = REPO_ROOT / "ci" / "container_lineage_results.json"

# Match `FROM python:<version>[-suffix]`. Captures the dotted
# numeric version, ignoring the optional tag suffix like -slim.
FROM_RE = re.compile(
    r"^\s*FROM\s+python:([0-9]+(?:\.[0-9]+)*)(?:-[A-Za-z0-9_.\-]+)?\s*$",
    re.MULTILINE,
)


def extract_dockerfile_python(text: str) -> str | None:
    m = FROM_RE.search(text)
    return m.group(1) if m else None


def extract_cert_python(cert_path: Path) -> tuple[str | None, str]:
    """Return (version_or_none, source_label)."""
    if not cert_path.exists():
        return None, "cert_json_missing"
    try:
        data = json.loads(cert_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "cert_json_unparseable"

    profile = data.get("profile")
    if isinstance(profile, dict):
        v = profile.get("python_version")
        if isinstance(v, str) and v.strip():
            return v.strip(), "profile.python_version"

    prov = data.get("provenance", {})
    deps = prov.get("dependencies", {}) if isinstance(prov, dict) else {}
    py = deps.get("python", {}) if isinstance(deps, dict) else {}
    if isinstance(py, dict):
        v = py.get("version_short")
        if isinstance(v, str) and v.strip():
            return v.strip(), "provenance.dependencies.python.version_short"

    return None, "absent"


def versions_match(dockerfile_v: str, cert_v: str) -> bool:
    """Match component-wise on the shorter dotted prefix.

    Treats `3.13.4` and `3.13` as compatible. This avoids
    false positives when one source records only the
    minor version.
    """
    a = dockerfile_v.split(".")
    b = cert_v.split(".")
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def main() -> int:
    failures: list[str] = []

    dockerfile_exists = DOCKERFILE.exists()
    if not dockerfile_exists:
        failures.append("Dockerfile not found at repo root")

    references_lockfile = False
    dockerfile_python: str | None = None
    if dockerfile_exists:
        text = DOCKERFILE.read_text(encoding="utf-8")
        references_lockfile = "requirements.lock.txt" in text
        if not references_lockfile:
            failures.append("Dockerfile does not reference requirements.lock.txt")
        dockerfile_python = extract_dockerfile_python(text)
        if dockerfile_python is None:
            failures.append("Dockerfile has no parseable `FROM python:<version>` line")

    cert_python, cert_source = extract_cert_python(CERT_JSON)

    version_check = "skipped"
    if dockerfile_python and cert_python:
        if versions_match(dockerfile_python, cert_python):
            version_check = "match"
        else:
            version_check = "mismatch"
            failures.append(
                f"Python version mismatch: Dockerfile={dockerfile_python} "
                f"vs {cert_source}={cert_python}"
            )
    elif dockerfile_python and not cert_python:
        version_check = "skipped_no_cert_value"

    ok = not failures

    summary = {
        "dockerfile_path": str(DOCKERFILE.relative_to(REPO_ROOT)),
        "dockerfile_exists": dockerfile_exists,
        "references_lockfile": references_lockfile,
        "dockerfile_python_version": dockerfile_python,
        "cert_python_version": cert_python,
        "cert_python_source": cert_source,
        "version_check": version_check,
        "failures": failures,
        "verdict": "PASS" if ok else "FAIL",
    }

    try:
        RESULTS.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"warning: could not write results JSON: {exc}", file=sys.stderr)

    if ok:
        print(
            "container lineage PASS: Dockerfile present, lockfile referenced, "
            f"python version {version_check}"
            + (f" ({dockerfile_python})" if dockerfile_python else "")
        )
        return 0

    print("container lineage FAIL", file=sys.stderr)
    for f in failures:
        print(f"  {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
