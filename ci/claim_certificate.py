#!/usr/bin/env python3
"""
claim_certificate.py - Generate the unified claim certificate.

Layer 6 of the certification stack: runs all five preceding layers,
aggregates their outputs, and emits a single structured artifact
suitable for inclusion in the paper supplementary or as a standalone
reproducibility certificate.

Outputs
-------
  claim_certificate.json      - structured payload (machine-readable)
  claim_certificate.md        - human-readable summary

The certificate documents:
  - Paper provenance (path, sha256 of main.tex, build timestamp)
  - Layer 1 (audit): how many claims appear verbatim in the paper
  - Layer 2 (validator): registry self-consistency
  - Layer 3 (body sweep): empirical coverage of body prose
  - Layer 4 (figure lineage): freshness of every rendered figure
  - Layer 5 (figure values): coverage of in-figure numerics
  - Aggregate verdict: PASS (all five clean) / WARN (degradation)
  - Triage links: where to find uncovered-claim lists for transparency

Exit codes
----------
  0  certificate generated; aggregate verdict is PASS or WARN
  1  one or more layers failed structurally
  2  invocation error (missing file, etc.)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
MAIN_PDF = REPO_ROOT / "paper" / "main.pdf"
CLAIMS_MD = REPO_ROOT / "CLAIM_AUDIT.md"

CERT_JSON = SCRIPT_DIR / "claim_certificate.json"
CERT_MD = SCRIPT_DIR / "claim_certificate.md"

LAYER_SCRIPTS = {
    "L1_audit": SCRIPT_DIR / "claim_audit.py",
    "L2_validator": SCRIPT_DIR / "claim_audit_validator.py",
    "L3_sweep": SCRIPT_DIR / "claim_coverage_sweep.py",
    "L4_lineage": SCRIPT_DIR / "figure_lineage_check.py",
    "L5_figure_values": SCRIPT_DIR / "figure_value_check.py",
    "L7_citations": SCRIPT_DIR / "citation_integrity_check.py",
    "L8_links": SCRIPT_DIR / "link_integrity_check.py",
    "L9_consistency": SCRIPT_DIR / "cross_claim_consistency_check.py",
    "L10_bib": SCRIPT_DIR / "bib_entry_check.py",
}

LAYER_RESULT_JSONS = {
    "L1_audit": SCRIPT_DIR / "claim_audit_results.json",
    "L3_sweep": SCRIPT_DIR / "claim_coverage_uncovered.json",
    "L5_figure_values": SCRIPT_DIR / "figure_value_check_results.json",
    "L7_citations": SCRIPT_DIR / "citation_integrity_results.json",
    "L8_links": SCRIPT_DIR / "link_integrity_results.json",
    "L9_consistency": SCRIPT_DIR / "cross_claim_consistency_results.json",
    "L10_bib": SCRIPT_DIR / "bib_entry_check_results.json",
}


@dataclass
class LayerOutcome:
    name: str
    script: str
    return_code: int
    summary: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def status(self) -> str:
        if self.return_code == 0:
            return "PASS"
        return "FAIL"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
def _strip_self_hash(payload: dict) -> dict:
    """Return a shallow copy of payload with the self-hash field removed.

    Used both at generation time (to compute the hash over everything
    BUT the hash field) and at verification time (a reader hashes the
    payload-minus-self-hash and compares).
    """
    return {k: v for k, v in payload.items() if k != "certificate_self_hash"}


def sha256_of(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def file_mtime_iso(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Layer runners
# ---------------------------------------------------------------------------
def run_layer(name: str, script: Path, args: list[str] | None = None) -> LayerOutcome:
    cmd = [sys.executable, str(script)] + (args or [])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return LayerOutcome(name=name, script=str(script.relative_to(REPO_ROOT)), return_code=proc.returncode)


def attach_summary_l1(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L1_audit"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    s = payload.get("summary", {})
    outcome.summary = {
        "tier": s.get("tier"),
        "total": s.get("total"),
        "found_verbatim": s.get("found_verbatim"),
        "drift": s.get("drift"),
        "missing": s.get("missing"),
    }


def attach_summary_l2(outcome: LayerOutcome) -> None:
    # Validator doesn't write a JSON — re-import and run inline to capture.
    spec = importlib.util.spec_from_file_location("claim_audit_validator", LAYER_SCRIPTS["L2_validator"])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claim_audit_validator"] = mod
    spec.loader.exec_module(mod)
    results = mod.run_all()
    n_pass = sum(1 for r in results if r.passed)
    outcome.summary = {
        "checks_total": len(results),
        "checks_passed": n_pass,
        "checks_failed": len(results) - n_pass,
        "first_failure": next((r.name for r in results if not r.passed), None),
    }


def attach_summary_l3(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L3_sweep"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l4(outcome: LayerOutcome) -> None:
    spec = importlib.util.spec_from_file_location("figure_lineage_check", LAYER_SCRIPTS["L4_lineage"])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["figure_lineage_check"] = mod
    spec.loader.exec_module(mod)
    results = mod.run_all()
    n_pass = sum(1 for r in results if r.passed)
    manifest = mod.load_manifest()
    n_figs_in_use = sum(1 for f in manifest["figures"].values() if f.get("in_use") is not False)
    outcome.summary = {
        "checks_total": len(results),
        "checks_passed": n_pass,
        "checks_failed": len(results) - n_pass,
        "figures_in_use": n_figs_in_use,
        "first_failure": next((r.name for r in results if not r.passed), None),
    }


def attach_summary_l5(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L5_figure_values"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})
    # Add per-figure coverage
    outcome.summary["per_figure"] = [
        {"name": f["name"], "coverage_percent": (f["covered"] / f["raw_numerics"] * 100) if f["raw_numerics"] else 0, "uncovered_count": f["uncovered_count"]}
        for f in payload.get("figures", [])
    ]


def attach_summary_l7(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L7_citations"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l8(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L8_links"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l9(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L9_consistency"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l10(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L10_bib"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


# ---------------------------------------------------------------------------
# Aggregate verdict
# ---------------------------------------------------------------------------
def aggregate_verdict(outcomes: list[LayerOutcome]) -> tuple[str, str]:
    """Return (verdict, rationale) where verdict is PASS or FAIL.

    Structural layers (L1, L2, L4, L7) gate the verdict. L3 and L5
    are advisory — their coverage % is reported on the certificate
    but does not move the verdict, because a fixed threshold is
    decorative (a real regression that adds 100 new uncovered numerics
    can stay above any chosen %, while genuine improvements that
    surface previously-hidden noise can drop below it). The triage
    JSONs are the actionable signal, not the percentage.
    """
    structural = {"L1_audit", "L2_validator", "L4_lineage", "L7_citations", "L8_links", "L9_consistency", "L10_bib"}
    structurally_failed = [o for o in outcomes if o.return_code != 0 and o.name in structural]
    if structurally_failed:
        names = ", ".join(o.name for o in structurally_failed)
        return ("FAIL", f"Structural layers failed: {names}")
    return ("PASS", "all structural checks clean (L1+L2+L4+L7+L8+L9+L10); L3+L5 coverage is advisory, see triage JSONs")


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def render_markdown(payload: dict) -> str:
    p = payload["provenance"]
    v = payload["verdict"]
    lines: list[str] = []
    lines.append(f"# Claim Certificate")
    lines.append("")
    lines.append(f"**Paper:** {p['paper_title']}")
    lines.append(f"**Generated:** {p['generated_at']}")
    lines.append(f"**Verdict:** **{v['verdict']}** — {v['rationale']}")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- main.tex sha256: `{p['main_tex_sha256'][:16]}...` (full hash in JSON)")
    lines.append(f"- main.tex mtime: {p['main_tex_mtime']}")
    lines.append(f"- main.pdf size: {p['main_pdf_size']:,} bytes")
    lines.append(f"- main.pdf mtime: {p['main_pdf_mtime']}")
    lines.append(f"- registry sha256: `{p['claim_audit_md_sha256'][:16]}...`")
    cert_hash = payload.get("certificate_self_hash", "<not-yet-computed>")
    if cert_hash != "<not-yet-computed>":
        lines.append(f"- certificate self-hash: `{cert_hash[:16]}...` (sha256 of this payload minus the hash field; recompute to verify integrity)")
    lines.append("")
    lines.append("## Layer-by-layer Results")
    lines.append("")
    lines.append("| Layer | Script | Status | Summary |")
    lines.append("|---|---|---|---|")
    for layer in payload["layers"]:
        status = layer["status"]
        s = layer["summary"]
        # Compose a short summary string per layer
        if layer["name"] == "L1_audit":
            blurb = f"{s.get('found_verbatim','?')}/{s.get('total','?')} verbatim"
            if s.get("missing", 0):
                blurb += f", {s['missing']} MISSING"
        elif layer["name"] == "L2_validator":
            blurb = f"{s.get('checks_passed','?')}/{s.get('checks_total','?')} checks pass"
        elif layer["name"] == "L3_sweep":
            blurb = f"{s.get('coverage_percent','?')}% coverage, {s.get('uncovered_deduped','?')} uncovered"
        elif layer["name"] == "L4_lineage":
            blurb = f"{s.get('checks_passed','?')}/{s.get('checks_total','?')} checks pass, {s.get('figures_in_use','?')} figures fresh"
        elif layer["name"] == "L5_figure_values":
            blurb = f"{s.get('coverage_percent','?')}% overall figure coverage, {s.get('total_uncovered','?')} uncovered"
        elif layer["name"] == "L7_citations":
            blurb = f"{s.get('n_cites_in_paper','?')} cites, {s.get('n_bib_entries','?')} bib entries, {s.get('n_unresolved','?')} unresolved, {s.get('n_dead','?')} dead"
        elif layer["name"] == "L8_links":
            blurb = f"{s.get('n_urls','?')} URLs, {s.get('n_refs','?')} refs / {s.get('n_labels','?')} labels, {len(s.get('unresolved_refs',[]))} unresolved, {s.get('dead_labels_count','?')} dead labels"
        elif layer["name"] == "L9_consistency":
            blurb = f"{s.get('passed','?')}/{s.get('total','?')} consistency relations hold"
        elif layer["name"] == "L10_bib":
            blurb = f"{s.get('ok','?')}/{s.get('total','?')} bib entries well-formed"
        else:
            blurb = "(no summary)"
        lines.append(f"| {layer['name']} | `{layer['script']}` | {status} | {blurb} |")
    lines.append("")

    # L5 per-figure breakdown
    l5 = next((l for l in payload["layers"] if l["name"] == "L5_figure_values"), None)
    if l5 and l5["summary"].get("per_figure"):
        lines.append("### Per-figure coverage (L5)")
        lines.append("")
        lines.append("| Figure | Coverage | Uncovered |")
        lines.append("|---|---|---|")
        for f in l5["summary"]["per_figure"]:
            lines.append(f"| {f['name']} | {f['coverage_percent']:.1f}% | {f['uncovered_count']} |")
        lines.append("")

    lines.append("## Triage Pointers")
    lines.append("")
    lines.append("Uncovered numerics (per-layer JSON, for human review):")
    lines.append("")
    lines.append(f"- L3 body sweep: `ci/claim_coverage_uncovered.json`")
    lines.append(f"- L5 figure values: `ci/figure_value_check_results.json`")
    lines.append("")
    lines.append("## Reproducing this Certificate")
    lines.append("")
    lines.append("```")
    lines.append("python ci/claim_certificate.py")
    lines.append("```")
    lines.append("")
    lines.append("Each layer can also be run independently:")
    lines.append("```")
    for name, path in LAYER_SCRIPTS.items():
        rel = path.relative_to(REPO_ROOT)
        lines.append(f"python {rel}    # {name}")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quiet", action="store_true", help="suppress per-layer console output during execution")
    args = parser.parse_args()

    if not MAIN_TEX.exists():
        print(f"ERROR: main.tex not found at {MAIN_TEX}", file=sys.stderr)
        return 2

    # Run each layer in sequence.
    if not args.quiet:
        print("Running 5-layer certification...")

    outcomes: list[LayerOutcome] = []

    # L1: audit Tier 1+2 (combined). Use --tier all so the JSON reflects the
    # full audited surface, not just Tier 1.
    if not args.quiet: print("  L1 audit (Tiers 1+2)...")
    o = run_layer("L1_audit", LAYER_SCRIPTS["L1_audit"], ["--tier", "all"])
    attach_summary_l1(o)
    outcomes.append(o)

    if not args.quiet: print("  L2 validator...")
    o = run_layer("L2_validator", LAYER_SCRIPTS["L2_validator"])
    attach_summary_l2(o)
    outcomes.append(o)

    if not args.quiet: print("  L3 body sweep...")
    o = run_layer("L3_sweep", LAYER_SCRIPTS["L3_sweep"], ["--max-print", "0"])
    attach_summary_l3(o)
    outcomes.append(o)

    if not args.quiet: print("  L4 figure lineage...")
    o = run_layer("L4_lineage", LAYER_SCRIPTS["L4_lineage"])
    attach_summary_l4(o)
    outcomes.append(o)

    if not args.quiet: print("  L5 figure values...")
    o = run_layer("L5_figure_values", LAYER_SCRIPTS["L5_figure_values"])
    attach_summary_l5(o)
    outcomes.append(o)

    if not args.quiet: print("  L7 citation integrity...")
    o = run_layer("L7_citations", LAYER_SCRIPTS["L7_citations"])
    attach_summary_l7(o)
    outcomes.append(o)

    if not args.quiet: print("  L8 link integrity...")
    o = run_layer("L8_links", LAYER_SCRIPTS["L8_links"])
    attach_summary_l8(o)
    outcomes.append(o)

    if not args.quiet: print("  L9 cross-claim consistency...")
    o = run_layer("L9_consistency", LAYER_SCRIPTS["L9_consistency"])
    attach_summary_l9(o)
    outcomes.append(o)

    if not args.quiet: print("  L10 bib entry check...")
    o = run_layer("L10_bib", LAYER_SCRIPTS["L10_bib"])
    attach_summary_l10(o)
    outcomes.append(o)

    verdict, rationale = aggregate_verdict(outcomes)

    payload = {
        "schema_version": 1,
        "provenance": {
            "paper_title": "The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment",
            "venue": "NeurIPS 2026",
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "main_tex_path": str(MAIN_TEX.relative_to(REPO_ROOT)),
            "main_tex_sha256": sha256_of(MAIN_TEX),
            "main_tex_mtime": file_mtime_iso(MAIN_TEX),
            "main_pdf_path": str(MAIN_PDF.relative_to(REPO_ROOT)),
            "main_pdf_size": file_size(MAIN_PDF),
            "main_pdf_mtime": file_mtime_iso(MAIN_PDF),
            "claim_audit_md_path": str(CLAIMS_MD.relative_to(REPO_ROOT)),
            "claim_audit_md_sha256": sha256_of(CLAIMS_MD),
        },
        "verdict": {
            "verdict": verdict,
            "rationale": rationale,
        },
        "layers": [
            {
                "name": o.name,
                "script": o.script,
                "return_code": o.return_code,
                "status": o.status,
                "summary": o.summary,
                "notes": o.notes,
            }
            for o in outcomes
        ],
    }

    # Self-tamper-evident hash: sha256 of the payload with the
    # certificate_self_hash field excluded. Compute BEFORE rendering
    # markdown so the .md output can include the hash too.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["certificate_self_hash"] = hashlib.sha256(canonical).hexdigest()

    CERT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    CERT_MD.write_text(render_markdown(payload), encoding="utf-8")

    print()
    print("=" * 70)
    print(f"CLAIM CERTIFICATE  -  Verdict: {verdict}")
    print("=" * 70)
    print(f"Rationale: {rationale}")
    print()
    for o in outcomes:
        print(f"  [{o.status:<4}] {o.name:<20}  {o.script}")
    print()
    print(f"  JSON: {CERT_JSON}")
    print(f"  MD:   {CERT_MD}")
    print()

    # Return 0 for PASS/WARN, 1 for FAIL (structural).
    return 0 if verdict in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    sys.exit(main())
