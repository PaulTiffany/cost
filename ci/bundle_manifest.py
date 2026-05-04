"""
bundle_manifest.py -- Generate ci/bundle_manifest.json listing every file the
certificate references, with sha256, size, mtime, role, and required/optional flag.

Exit codes:
  0 - PASS (manifest written)
  2 - invocation / unexpected error

Usage:
  python ci/bundle_manifest.py
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def classify_role(rel: str) -> str:
    p = rel.replace("\\", "/").lower()
    if p in ("paper/main.tex", "paper/main.pdf"):
        return "paper"
    if p.endswith(".tex") or p.endswith(".pdf") and "paper/" in p:
        return "paper"
    if "/figures/" in p and (p.endswith(".pdf") or p.endswith(".tex") or p.endswith(".png")):
        return "figure"
    if p.endswith(".py"):
        return "script"
    if p.endswith(".json") and ("result" in p or "output" in p or "results" in p):
        return "result"
    if p.endswith(".json") and ("lineage" in p or "ties" in p or "certificate" in p or "manifest" in p):
        return "manifest"
    if p.endswith(".json"):
        return "data"
    if p.endswith(".npy") or p.endswith(".pkl") or p.endswith(".csv"):
        return "data"
    if p.endswith(".bib") or p.endswith(".sty") or p.endswith(".bst"):
        return "paper"
    if "compliance" in p or "handbook" in p or "venue" in p:
        return "venue_doc"
    if "illustrations" in p or "draft" in p:
        return "illustration"
    if p.endswith(".txt"):
        return "data"
    return "result"


def is_required(rel: str, role: str) -> bool:
    p = rel.replace("\\", "/").lower()
    if p in ("paper/main.tex", "paper/main.pdf"):
        return True
    if role == "paper" and p.endswith(".tex"):
        return True
    if role in ("result", "data") and p.endswith(".json"):
        # intermediate result JSONs are optional at review time
        return False
    if role == "manifest":
        return True
    if role == "figure":
        return True
    if role == "script":
        return False
    if role == "venue_doc":
        return True
    return False


def collect_paths() -> set:
    """Collect all unique file paths referenced by the cert sources."""
    paths: set = set()

    # 1. claim_certificate.json: provenance + artifact_hashes
    cert_path = CI_DIR / "claim_certificate.json"
    if cert_path.exists():
        with cert_path.open("r", encoding="utf-8") as fh:
            cert = json.load(fh)

        prov = cert.get("provenance", {})
        for key in ("main_tex_path", "main_pdf_path", "claim_audit_md_path"):
            val = prov.get(key)
            if val:
                paths.add(val)

        for fpath in cert.get("artifact_hashes", {}).keys():
            paths.add(fpath)

        # layers reference scripts
        for layer in cert.get("layers", []):
            script = layer.get("script")
            if script:
                paths.add(script)

    # 2. claim_data_ties.json: every source_file
    ties_path = CI_DIR / "claim_data_ties.json"
    if ties_path.exists():
        with ties_path.open("r", encoding="utf-8") as fh:
            ties = json.load(fh)
        for claim_data in ties.get("claims", {}).values():
            sf = claim_data.get("source_file")
            if sf:
                paths.add(sf)

    # 3. figure_lineage.json: script, data entries, asset
    fig_lin_path = CI_DIR / "figure_lineage.json"
    if fig_lin_path.exists():
        with fig_lin_path.open("r", encoding="utf-8") as fh:
            fig_lin = json.load(fh)
        for fig_data in fig_lin.get("figures", {}).values():
            asset = fig_data.get("asset")
            if asset:
                paths.add(asset)
            script = fig_data.get("script")
            if script:
                paths.add(script)
            for d in fig_data.get("data", []):
                paths.add(d)
            for d in fig_data.get("outputs_secondary", []):
                paths.add(d)

    # 4. illustration_lineage.json: inputs[*].file, final_asset, venue_constraints[*].file
    ill_lin_path = CI_DIR / "illustration_lineage.json"
    if ill_lin_path.exists():
        with ill_lin_path.open("r", encoding="utf-8") as fh:
            ill_lin = json.load(fh)
        # venue_sources source files
        for vs in ill_lin.get("_meta", {}).get("venue_sources", {}).values():
            sf = vs.get("source_file")
            if sf:
                paths.add(sf)
            cf = vs.get("curated_constraint_file")
            if cf:
                paths.add(cf)
        for ill_data in ill_lin.get("illustrations", {}).values():
            for inp in ill_data.get("inputs", []):
                f = inp.get("file")
                if f:
                    paths.add(f)
            final = ill_data.get("final_asset")
            if final:
                paths.add(final)
            draft_img = ill_data.get("draft_image")
            if draft_img:
                paths.add(draft_img)
            for vc in ill_data.get("venue_constraints", []):
                f = vc.get("file")
                if f:
                    paths.add(f)

    # Add the source json files themselves
    for fname in ("claim_data_ties.json", "figure_lineage.json", "illustration_lineage.json",
                  "claim_certificate.json"):
        paths.add(str(Path("ci") / fname))

    return paths


def build_entry(rel_path: str) -> dict:
    # Normalise to forward slashes for canonical paths in JSON, but resolve
    # against repo root using OS path separators.
    rel_os = rel_path.replace("/", os.sep)
    abs_path = REPO_ROOT / rel_os

    role = classify_role(rel_path)
    required = is_required(rel_path, role)

    if not abs_path.exists():
        return {
            "path": rel_path.replace("\\", "/"),
            "absent": True,
            "role": role,
            "required": required,
        }

    stat = abs_path.stat()
    return {
        "path": rel_path.replace("\\", "/"),
        "sha256": sha256_file(abs_path),
        "size": stat.st_size,
        "mtime": mtime_iso(abs_path),
        "role": role,
        "required": required,
    }


def main() -> int:
    try:
        raw_paths = collect_paths()

        entries = []
        for rel in sorted(raw_paths):
            entries.append(build_entry(rel))

        present = [e for e in entries if not e.get("absent")]
        total_bytes = sum(e.get("size", 0) for e in present)

        manifest = {
            "_meta": {
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
                "n_files": len(entries),
                "n_present": len(present),
                "n_absent": len(entries) - len(present),
                "total_bytes": total_bytes,
            },
            "files": entries,
        }

        out_path = CI_DIR / "bundle_manifest.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

        print(f"PASS  bundle_manifest.json written: {len(entries)} files, "
              f"{len(present)} present, {len(entries) - len(present)} absent, "
              f"{total_bytes:,} bytes total")
        return 0

    except Exception as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
