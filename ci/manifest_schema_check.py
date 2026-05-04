"""
manifest_schema_check.py  --  Cert layer: validate JSON manifests have required shape.

Checks three manifests under ci/:
  claim_data_ties.json      -- claims dict with typed entries
  figure_lineage.json       -- figures dict with script/data entries
  illustration_lineage.json -- illustrations dict with inputs/final_asset/hash/scope

Exit codes:
  0  all manifests passed
  1  one or more checks failed
  2  invocation error
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _err(msg: str) -> str:
    return msg


def check_file_exists(path: Path) -> list[str]:
    if not path.exists():
        return [f"File not found: {path}"]
    return []


def check_json_parses(path: Path) -> tuple[Any | None, list[str]]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), []
    except json.JSONDecodeError as exc:
        return None, [f"JSON parse error: {exc}"]
    except OSError as exc:
        return None, [f"IO error: {exc}"]


def _is_hex_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"[0-9a-fA-F]+", value))


def _resolve_path(rel: str) -> Path:
    """Resolve a path string relative to REPO_ROOT."""
    return REPO_ROOT / rel


# --------------------------------------------------------------------------- #
# Per-manifest validators
# --------------------------------------------------------------------------- #

def validate_claim_data_ties(path: Path) -> list[str]:
    """
    claim_data_ties.json schema:
      top-level key "claims" -> dict
      each entry has:
        source_file  str
        value_expr   str
        expected     number | bool
        tolerance    number >= 0
        compare_as   one of: int / float / bool / str
    Also checks that source_file paths exist relative to REPO_ROOT.
    """
    errors: list[str] = []

    exist_errs = check_file_exists(path)
    if exist_errs:
        return exist_errs

    data, parse_errs = check_json_parses(path)
    if parse_errs:
        return parse_errs

    if not isinstance(data, dict) or "claims" not in data:
        return [_err('Missing required top-level key "claims"')]

    claims = data["claims"]
    if not isinstance(claims, dict):
        return [_err('"claims" must be a dict')]

    valid_compare_as = {"int", "float", "bool", "str"}

    for claim_id, entry in claims.items():
        prefix = f"claims[{claim_id!r}]"

        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be a dict, got {type(entry).__name__}")
            continue

        # source_file: str, must exist
        sf = entry.get("source_file")
        if not isinstance(sf, str):
            errors.append(f"{prefix}.source_file: must be str, got {type(sf).__name__}")
        else:
            resolved = _resolve_path(sf)
            if not resolved.exists():
                errors.append(f"{prefix}.source_file: path does not exist: {resolved}")

        # value_expr: str
        ve = entry.get("value_expr")
        if not isinstance(ve, str):
            errors.append(f"{prefix}.value_expr: must be str, got {type(ve).__name__}")

        # expected: number or bool
        exp = entry.get("expected")
        if not isinstance(exp, (int, float, bool)):
            errors.append(
                f"{prefix}.expected: must be number or bool, got {type(exp).__name__}"
            )

        # tolerance: number >= 0
        tol = entry.get("tolerance")
        if not isinstance(tol, (int, float)):
            errors.append(
                f"{prefix}.tolerance: must be a number, got {type(tol).__name__}"
            )
        elif tol < 0:
            errors.append(f"{prefix}.tolerance: must be >= 0, got {tol}")

        # compare_as: one of valid set
        ca = entry.get("compare_as")
        if ca not in valid_compare_as:
            errors.append(
                f"{prefix}.compare_as: must be one of {sorted(valid_compare_as)}, got {ca!r}"
            )

    return errors


def validate_figure_lineage(path: Path) -> list[str]:
    """
    figure_lineage.json schema:
      Must have either top-level key "figures" (preferred) or be a dict where
      each value has "script" (str) and "data" (list).
    Also checks that "script" and "asset" paths exist (when present and when
    binary_asset is not set).
    """
    errors: list[str] = []

    exist_errs = check_file_exists(path)
    if exist_errs:
        return exist_errs

    data, parse_errs = check_json_parses(path)
    if parse_errs:
        return parse_errs

    if not isinstance(data, dict):
        return [_err("Top-level value must be a JSON object")]

    # Locate the figures dict
    if "figures" in data:
        figures = data["figures"]
        if not isinstance(figures, dict):
            return [_err('"figures" must be a dict')]
    else:
        # Fallback: treat root as the figures dict (legacy format)
        # Filter out known metadata keys
        figures = {
            k: v for k, v in data.items()
            if not k.startswith("_") and isinstance(v, dict)
        }
        if not figures:
            return [_err('No "figures" key and no non-meta dict entries found')]

    for fig_id, entry in figures.items():
        prefix = f"figures[{fig_id!r}]"

        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be a dict")
            continue

        is_tikz = entry.get("tikz_source", False)

        # TikZ-source figures have no script/data -- skip those field checks
        if not is_tikz:
            script = entry.get("script")
            if script is not None:
                if not isinstance(script, str):
                    errors.append(f"{prefix}.script: must be str")
                else:
                    if not _resolve_path(script).exists():
                        errors.append(
                            f"{prefix}.script: path does not exist: {_resolve_path(script)}"
                        )

            data_field = entry.get("data")
            if data_field is not None and not isinstance(data_field, list):
                errors.append(f"{prefix}.data: must be a list")

        # asset: str (optional but checked if present)
        asset = entry.get("asset")
        if asset is not None:
            if not isinstance(asset, str):
                errors.append(f"{prefix}.asset: must be str")
            else:
                binary = entry.get("binary_asset", False)
                if not binary and not _resolve_path(asset).exists():
                    errors.append(
                        f"{prefix}.asset: path does not exist: {_resolve_path(asset)}"
                    )

    return errors


def validate_illustration_lineage(path: Path) -> list[str]:
    """
    illustration_lineage.json schema:
      top-level key "illustrations" -> dict
      each entry has:
        inputs           list
        final_asset      str
        final_asset_hash hex string
        claim_scope      str
    Also checks that final_asset paths exist.
    """
    errors: list[str] = []

    exist_errs = check_file_exists(path)
    if exist_errs:
        return exist_errs

    data, parse_errs = check_json_parses(path)
    if parse_errs:
        return parse_errs

    if not isinstance(data, dict) or "illustrations" not in data:
        return [_err('Missing required top-level key "illustrations"')]

    illustrations = data["illustrations"]
    if not isinstance(illustrations, dict):
        return [_err('"illustrations" must be a dict')]

    for illus_id, entry in illustrations.items():
        prefix = f"illustrations[{illus_id!r}]"

        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be a dict")
            continue

        # inputs: list
        inputs_val = entry.get("inputs")
        if not isinstance(inputs_val, list):
            errors.append(
                f"{prefix}.inputs: must be a list, got {type(inputs_val).__name__}"
            )

        # final_asset: str, must exist
        fa = entry.get("final_asset")
        if not isinstance(fa, str):
            errors.append(
                f"{prefix}.final_asset: must be str, got {type(fa).__name__}"
            )
        else:
            resolved = _resolve_path(fa)
            if not resolved.exists():
                errors.append(f"{prefix}.final_asset: path does not exist: {resolved}")

        # final_asset_hash: hex string
        fah = entry.get("final_asset_hash")
        if not _is_hex_string(fah):
            errors.append(
                f"{prefix}.final_asset_hash: must be a hex string, got {fah!r}"
            )

        # claim_scope: str
        cs = entry.get("claim_scope")
        if not isinstance(cs, str):
            errors.append(
                f"{prefix}.claim_scope: must be str, got {type(cs).__name__}"
            )

    return errors


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

MANIFESTS = [
    {
        "key": "claim_data_ties",
        "path": CI_DIR / "claim_data_ties.json",
        "validator": validate_claim_data_ties,
    },
    {
        "key": "figure_lineage",
        "path": CI_DIR / "figure_lineage.json",
        "validator": validate_figure_lineage,
    },
    {
        "key": "illustration_lineage",
        "path": CI_DIR / "illustration_lineage.json",
        "validator": validate_illustration_lineage,
    },
]


def run_all() -> dict:
    per_manifest = []
    total = 0
    passed = 0
    failed = 0

    for spec in MANIFESTS:
        path: Path = spec["path"]
        rel = str(path.relative_to(REPO_ROOT))

        if not path.exists():
            record = {
                "path": rel,
                "passed": False,
                "status": "MISSING",
                "errors": [f"Manifest not found: {path}"],
            }
            per_manifest.append(record)
            total += 1
            failed += 1
            continue

        errors = spec["validator"](path)
        ok = len(errors) == 0
        record = {
            "path": rel,
            "passed": ok,
            "status": "PASS" if ok else "FAIL",
            "errors": errors,
        }
        per_manifest.append(record)
        total += 1
        if ok:
            passed += 1
        else:
            failed += 1

    return {
        "summary": {"total": total, "passed": passed, "failed": failed},
        "per_manifest": per_manifest,
    }


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    try:
        results = run_all()
    except Exception as exc:  # noqa: BLE001
        print(f"[manifest_schema_check] Invocation error: {exc}", file=sys.stderr)
        return 2

    output_path = CI_DIR / "manifest_schema_results.json"
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
    except OSError as exc:
        print(f"[manifest_schema_check] Could not write output: {exc}", file=sys.stderr)
        return 2

    summary = results["summary"]
    print(
        f"[manifest_schema_check] {summary['passed']}/{summary['total']} passed"
        f" ({summary['failed']} failed)"
    )
    for rec in results["per_manifest"]:
        status = rec["status"]
        print(f"  {status:7s}  {rec['path']}")
        for err in rec["errors"]:
            print(f"           ERROR: {err}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
