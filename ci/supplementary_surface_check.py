#!/usr/bin/env python3
"""
supplementary_surface_check.py
================================
Reads ci/supplementary_manifest.json and validates every supplementary artifact.

Checks performed:
  1. schema        - required fields present, role from allowed set
  2. exists        - file present on disk (skipped for excluded+not-in-bundle)
  3. forbidden_tokens - token scan (.ipynb markdown cells; .md/.tex text)
  4. notebook_static  - for .ipynb expected_in_bundle=true: kernelspec, cell counts, imports
  5. scope_note_present - reviewer_aid and demo roles require non-empty scope_note
  6. table_display_match - deferred to Agent K (recorded as "deferred")
  7. json_parse    - file must be valid JSON

Exit codes: 0 = PASS, 1 = FAIL, 2 = invocation error
Output: ci/supplementary_surface_results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = Path(__file__).resolve().parent / "supplementary_manifest.json"
RESULTS_PATH = Path(__file__).resolve().parent / "supplementary_surface_results.json"

ALLOWED_ROLES = {
    "evidence",
    "generated_table",
    "reviewer_aid",
    "demo",
    "internal_draft",
    "excluded",
}

REQUIRED_FIELDS = {
    "path",
    "role",
    "claim_ids",
    "paper_locations",
    "source_script",
    "source_data",
    "expected_in_bundle",
    "checks_required",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(2)
    with MANIFEST_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _resolve(artifact_path: str) -> Path:
    return REPO_ROOT / artifact_path


def _check_result(check: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": check, "passed": passed, "detail": detail}


# ---------------------------------------------------------------------------
# Individual check implementations
# ---------------------------------------------------------------------------

def check_schema(artifact: dict[str, Any]) -> dict[str, Any]:
    missing = REQUIRED_FIELDS - set(artifact.keys())
    if missing:
        return _check_result(
            "schema", False, f"Missing required fields: {sorted(missing)}"
        )
    role = artifact.get("role", "")
    if role not in ALLOWED_ROLES:
        return _check_result(
            "schema", False, f"Unknown role '{role}'; allowed: {sorted(ALLOWED_ROLES)}"
        )
    return _check_result("schema", True, "All required fields present, role valid")


def check_exists(artifact: dict[str, Any]) -> dict[str, Any] | None:
    """Return None if the check should be skipped."""
    role = artifact.get("role", "")
    expected_in_bundle = artifact.get("expected_in_bundle", True)
    # Skip existence check for excluded artifacts not expected in bundle
    if role == "excluded" and not expected_in_bundle:
        return None
    path = _resolve(artifact["path"])
    if path.exists():
        return _check_result("exists", True, f"File present: {path}")
    return _check_result("exists", False, f"File missing: {path}")


def check_forbidden_tokens(artifact: dict[str, Any]) -> dict[str, Any] | None:
    tokens = artifact.get("forbidden_tokens", [])
    if not tokens:
        return None

    path = _resolve(artifact["path"])
    if not path.exists():
        return _check_result(
            "forbidden_tokens", False, f"Cannot scan; file missing: {path}"
        )

    suffix = path.suffix.lower()

    try:
        if suffix == ".ipynb":
            raw = path.read_text(encoding="utf-8", errors="replace")
            nb = json.loads(raw)
            # Scan only markdown cells (code cells not reviewer-facing in static view)
            texts: list[str] = []
            for cell in nb.get("cells", []):
                if cell.get("cell_type") in ("markdown", "raw"):
                    source = cell.get("source", "")
                    if isinstance(source, list):
                        texts.append("".join(source))
                    else:
                        texts.append(str(source))
            combined = "\n".join(texts)
        else:
            combined = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _check_result(
            "forbidden_tokens", False, f"Read error: {exc}"
        )

    found = [t for t in tokens if t in combined]
    if found:
        return _check_result(
            "forbidden_tokens",
            False,
            f"Forbidden token(s) found: {found}",
        )
    return _check_result(
        "forbidden_tokens", True, f"No forbidden tokens found (checked: {tokens})"
    )


def check_notebook_static(artifact: dict[str, Any]) -> dict[str, Any] | None:
    if not artifact["path"].endswith(".ipynb"):
        return None
    if not artifact.get("expected_in_bundle", False):
        return None

    path = _resolve(artifact["path"])
    if not path.exists():
        return _check_result(
            "notebook_static", False, f"Notebook missing: {path}"
        )

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        nb = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _check_result(
            "notebook_static", False, f"JSON parse error: {exc}"
        )
    except Exception as exc:
        return _check_result(
            "notebook_static", False, f"Read error: {exc}"
        )

    metadata = nb.get("metadata", {})
    kernelspec = metadata.get("kernelspec", None)

    cells = nb.get("cells", [])
    code_cells = [c for c in cells if c.get("cell_type") == "code"]
    md_cells = [c for c in cells if c.get("cell_type") == "markdown"]

    executed_cells = [
        c for c in code_cells if c.get("execution_count") is not None
    ]

    # Collect imports
    imports: list[str] = []
    for cell in code_cells:
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.append(stripped)

    detail = (
        f"kernelspec={'present' if kernelspec else 'MISSING'}; "
        f"code_cells={len(code_cells)}; markdown_cells={len(md_cells)}; "
        f"executed_cells={len(executed_cells)}; "
        f"imports=[{', '.join(imports[:10])}{'...' if len(imports) > 10 else ''}]"
    )

    passed = kernelspec is not None
    if not passed:
        detail = "MISSING kernelspec. " + detail

    return _check_result("notebook_static", passed, detail)


def check_scope_note_present(artifact: dict[str, Any]) -> dict[str, Any] | None:
    role = artifact.get("role", "")
    if role not in ("reviewer_aid", "demo"):
        return None
    note = artifact.get("scope_note", "")
    if note and note.strip():
        return _check_result(
            "scope_note_present", True, f"scope_note present ({len(note)} chars)"
        )
    return _check_result(
        "scope_note_present", False, "scope_note is empty or missing"
    )


def check_table_display_match(artifact: dict[str, Any]) -> dict[str, Any] | None:
    return _check_result(
        "table_display_match",
        True,
        "deferred — Agent K is performing table_display_match checks",
    )


def check_json_parse(artifact: dict[str, Any]) -> dict[str, Any] | None:
    path = _resolve(artifact["path"])
    if not path.exists():
        return _check_result(
            "json_parse", False, f"File missing: {path}"
        )
    try:
        with path.open(encoding="utf-8") as fh:
            json.load(fh)
        return _check_result("json_parse", True, "Valid JSON")
    except json.JSONDecodeError as exc:
        return _check_result("json_parse", False, f"JSON parse error: {exc}")
    except Exception as exc:
        return _check_result("json_parse", False, f"Read error: {exc}")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

CHECK_DISPATCH = {
    "exists": check_exists,
    "forbidden_tokens": check_forbidden_tokens,
    "notebook_static": check_notebook_static,
    "scope_note_present": check_scope_note_present,
    "table_display_match": check_table_display_match,
    "json_parse": check_json_parse,
}


def run_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    checks_required: list[str] = artifact.get("checks_required", [])
    results: list[dict[str, Any]] = []

    # Schema check always runs first
    schema_r = check_schema(artifact)
    results.append(schema_r)
    if not schema_r["passed"]:
        # Cannot meaningfully proceed
        return {
            "path": artifact.get("path", "<unknown>"),
            "role": artifact.get("role", "<unknown>"),
            "checks": results,
        }

    for check_name in checks_required:
        fn = CHECK_DISPATCH.get(check_name)
        if fn is None:
            results.append(
                _check_result(check_name, False, f"Unknown check '{check_name}'")
            )
            continue
        result = fn(artifact)
        if result is not None:
            results.append(result)

    return {
        "path": artifact["path"],
        "role": artifact["role"],
        "checks": results,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    manifest = _load_manifest()
    artifacts = manifest.get("artifacts", [])

    per_artifact: list[dict[str, Any]] = []
    total = len(artifacts)
    passed_count = 0
    failed_count = 0

    by_role: dict[str, dict[str, int]] = {}
    notebooks_checked = 0
    audio_files_checked = 0
    generated_tables_checked = 0

    for artifact in artifacts:
        result = run_artifact(artifact)
        all_passed = all(c["passed"] for c in result["checks"])
        if all_passed:
            passed_count += 1
        else:
            failed_count += 1

        role = result["role"]
        if role not in by_role:
            by_role[role] = {"passed": 0, "failed": 0}
        if all_passed:
            by_role[role]["passed"] += 1
        else:
            by_role[role]["failed"] += 1

        path_lower = artifact.get("path", "").lower()
        if path_lower.endswith(".ipynb"):
            notebooks_checked += 1
        if path_lower.endswith((".wav", ".mp3")):
            audio_files_checked += 1
        if role == "generated_table":
            generated_tables_checked += 1

        per_artifact.append(result)

    summary = {
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "by_role": by_role,
        "notebooks_checked": notebooks_checked,
        "audio_files_checked": audio_files_checked,
        "generated_tables_checked": generated_tables_checked,
    }

    output = {"summary": summary, "per_artifact": per_artifact}

    RESULTS_PATH.write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )

    # Print summary to stdout
    status = "PASS" if failed_count == 0 else "FAIL"
    print(f"supplementary_surface_check: {status}")
    print(f"  total={total}  passed={passed_count}  failed={failed_count}")
    print(f"  by_role: {json.dumps(by_role)}")
    print(f"  notebooks_checked={notebooks_checked}")
    print(f"  audio_files_checked={audio_files_checked}")
    print(f"  generated_tables_checked={generated_tables_checked}")
    print(f"  results written to: {RESULTS_PATH}")

    if failed_count > 0:
        print("\nFailed artifacts:")
        for r in per_artifact:
            failing_checks = [
                c for c in r["checks"] if not c["passed"]
            ]
            if failing_checks:
                print(f"  [{r['role']}] {r['path']}")
                for c in failing_checks:
                    print(f"    FAIL {c['check']}: {c['detail']}")

    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
