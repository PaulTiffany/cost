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
    "L11_scripts": SCRIPT_DIR / "script_integrity_check.py",
    "L12_build_equiv": SCRIPT_DIR / "build_equivalence_check.py",
    "L13_cross_tree": SCRIPT_DIR / "cross_tree_consistency_check.py",
    "L14_illustrations": SCRIPT_DIR / "illustration_lineage_check.py",
    "L15_data_ties": SCRIPT_DIR / "claim_data_ties_check.py",
    "L16_author_claims": SCRIPT_DIR / "author_claims_check.py",
    # Suite expansion (added 2026-05-04 second pass; see CERTIFICATE_CHANGELOG)
    "L17_table_values":         SCRIPT_DIR / "table_value_check.py",                  # claim_text
    "L18_sample_size_adequacy": SCRIPT_DIR / "sample_size_adequacy_check.py",         # statistical_hygiene
    "L19_ci_coverage":          SCRIPT_DIR / "confidence_interval_coverage_check.py", # statistical_hygiene
    "L20_cross_source_recompute": SCRIPT_DIR / "cross_source_recomputation_check.py", # data_ties
    "L21_sbom":                 SCRIPT_DIR / "sbom_check.py",                         # provenance
    "L22_container_lineage":    SCRIPT_DIR / "container_lineage_check.py",            # artifact_lineage
    "L23_license_clearance":    SCRIPT_DIR / "license_clearance_check.py",            # submission_hygiene
    "L24_pdf_camera_ready":     SCRIPT_DIR / "pdf_camera_ready_check.py",             # submission_hygiene
    "L25_multi_seed_drift":     SCRIPT_DIR / "multi_seed_drift_check.py",             # statistical_hygiene
    "L26_reference_convention": SCRIPT_DIR / "reference_convention_check.py",          # submission_hygiene
    "L27_stat_algo_sanity":     SCRIPT_DIR / "statistical_and_algorithmic_sanity_check.py", # statistical_hygiene
    "L28_symbolic_algebra":     SCRIPT_DIR / "symbolic_algebra_check.py",                    # statistical_hygiene
    "L29_numerical_bounds":     SCRIPT_DIR / "numerical_bound_check.py",                     # statistical_hygiene
}

# L12 in default (quick) mode runs --quick (mtime + asset-hash only) so
# the certificate stays fast. Release mode strips --quick for full re-render
# equivalence checking. See _resolve_extra_args().
LAYER_EXTRA_ARGS_QUICK = {
    "L12_build_equiv": ["--quick"],
}
LAYER_EXTRA_ARGS_RELEASE = {
    # No --quick on L12 in release mode (full equivalence check)
}


def _resolve_extra_args(mode: str) -> dict:
    return LAYER_EXTRA_ARGS_RELEASE if mode == "release" else LAYER_EXTRA_ARGS_QUICK


# Profile-driven layer skipping. reviewer_quick prioritizes <10s runtime
# (skip the slow ones); artifact_eval and author_release run everything.
PROFILE_SKIP_LAYERS = {
    "standard": set(),
    "reviewer_quick": {"L11_scripts", "L12_build_equiv"},
    "artifact_eval": set(),
    "author_release": set(),
}


def _profile_skip(profile: str, layer_name: str) -> bool:
    return layer_name in PROFILE_SKIP_LAYERS.get(profile, set())


# Used as a module-level fallback by run_layer for backward-compat call sites.
LAYER_EXTRA_ARGS = LAYER_EXTRA_ARGS_QUICK


def get_git_state() -> dict:
    """Capture git provenance: commit, branch, dirty status, untracked count.

    Returns a dict suitable for embedding in cert provenance. Best-effort:
    if git is unavailable or this is not a repo, returns a minimal dict
    with status='unavailable'.
    """
    import subprocess
    def _run(cmd: list[str]) -> str | None:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None
    sha = _run(["git", "rev-parse", "HEAD"])
    if not sha:
        return {"status": "unavailable", "reason": "git command failed or not a repo"}
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "(detached)"
    porcelain = _run(["git", "status", "--porcelain"])
    if porcelain is None:
        porcelain = ""
    porcelain_lines = [l for l in porcelain.splitlines() if l.strip()]
    n_modified = sum(1 for l in porcelain_lines if l.startswith((" M", "M ", "MM", " D", "D ")))
    n_untracked = sum(1 for l in porcelain_lines if l.startswith("??"))
    n_staged = sum(1 for l in porcelain_lines if l.startswith(("A ", "M ", "D ", "R ", "C ")))
    return {
        "status": "ok",
        "commit": sha,
        "commit_short": sha[:8],
        "branch": branch,
        "dirty": bool(porcelain_lines),
        "n_modified_unstaged": n_modified,
        "n_staged": n_staged,
        "n_untracked": n_untracked,
    }


def detect_venue(default: str = "neurips") -> str:
    """Auto-detect submission venue from main.tex preamble.

    Returns one of: "arxiv" (preprint mode), "camera_ready" (final mode),
    or default (NeurIPS double-blind submission).

    Ignores LaTeX comments (lines starting with %) so example invocations
    in preamble docs do not trigger false positives.
    """
    if not MAIN_TEX.exists():
        return default
    import re as _re
    head_lines = MAIN_TEX.read_text(encoding="utf-8", errors="replace").splitlines()[:80]
    for raw in head_lines:
        if raw.lstrip().startswith("%"):
            continue
        m = _re.search(r"\\usepackage\[([^\]]*)\]\{neurips_\d+\}", raw)
        if not m:
            continue
        opts = {o.strip() for o in m.group(1).split(",")}
        if "preprint" in opts:
            return "arxiv"
        if "final" in opts:
            return "camera_ready"
        return default
    return default

LAYER_RESULT_JSONS = {
    "L1_audit": SCRIPT_DIR / "claim_audit_results.json",
    "L3_sweep": SCRIPT_DIR / "claim_coverage_uncovered.json",
    "L5_figure_values": SCRIPT_DIR / "figure_value_check_results.json",
    "L7_citations": SCRIPT_DIR / "citation_integrity_results.json",
    "L8_links": SCRIPT_DIR / "link_integrity_results.json",
    "L9_consistency": SCRIPT_DIR / "cross_claim_consistency_results.json",
    "L10_bib": SCRIPT_DIR / "bib_entry_check_results.json",
    "L11_scripts": SCRIPT_DIR / "script_integrity_results.json",
    "L12_build_equiv": SCRIPT_DIR / "build_equivalence_results.json",
    "L13_cross_tree": SCRIPT_DIR / "cross_tree_consistency_results.json",
    "L14_illustrations": SCRIPT_DIR / "illustration_lineage_results.json",
    "L15_data_ties": SCRIPT_DIR / "claim_data_ties_results.json",
    "L16_author_claims": SCRIPT_DIR / "author_claims_results.json",
    "L17_table_values":         SCRIPT_DIR / "table_value_results.json",
    "L18_sample_size_adequacy": SCRIPT_DIR / "sample_size_adequacy_results.json",
    "L19_ci_coverage":          SCRIPT_DIR / "confidence_interval_coverage_results.json",
    "L20_cross_source_recompute": SCRIPT_DIR / "cross_source_recomputation_results.json",
    "L21_sbom":                 SCRIPT_DIR / "sbom_check_results.json",
    "L22_container_lineage":    SCRIPT_DIR / "container_lineage_results.json",
    "L23_license_clearance":    SCRIPT_DIR / "license_clearance_results.json",
    "L24_pdf_camera_ready":     SCRIPT_DIR / "pdf_camera_ready_results.json",
    "L25_multi_seed_drift":     SCRIPT_DIR / "multi_seed_drift_results.json",
    "L26_reference_convention": SCRIPT_DIR / "reference_convention_results.json",
    "L27_stat_algo_sanity":     SCRIPT_DIR / "statistical_and_algorithmic_sanity_results.json",
    "L28_symbolic_algebra":     SCRIPT_DIR / "symbolic_algebra_results.json",
    "L29_numerical_bounds":     SCRIPT_DIR / "numerical_bound_results.json",
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
        if self.summary.get("skipped"):
            return "SKIP"
        if self.return_code == 0:
            return "PASS"
        return "FAIL"


# ---------------------------------------------------------------------------
# Suite registry roll-up (additive; preserves layers[] for backward compat)
# ---------------------------------------------------------------------------
def _load_suite_registry() -> dict:
    """Load ci/suite_registry.json. The registry maps each blocking layer
    and L9 sub-check into a named suite (claim_text, data_ties, etc.).
    The cert payload emits both the legacy layers[] array (for downstream
    tooling) and a suites[] roll-up (for human-friendly reading).
    Migration is additive; this function is the only new orchestrator
    surface introduced by the suite reorganization."""
    here = Path(__file__).resolve().parent
    p = here / "suite_registry.json"
    if not p.exists():
        return {"suites": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _roll_up_suites(outcomes) -> list[dict]:
    """Group LayerOutcome objects by suite as defined in suite_registry.json.
    Layer outcomes not listed in the registry land in 'unmapped'. Each
    suite reports passed / failed / skipped counts and a derived status.
    Sub-check (subproc__) outcomes are inferred from the L9 consistency
    output if present (we surface the layer-level status here; sub-check
    detail lives in cross_claim_consistency_results.json)."""
    reg = _load_suite_registry()
    by_name = {o.name: o for o in outcomes}
    rolled: list[dict] = []
    seen: set[str] = set()
    for suite_name, suite_def in reg.get("suites", {}).items():
        members_status: list[dict] = []
        passed = failed = skipped = 0
        for member in suite_def.get("members", []):
            check_name = member["check"]
            seen.add(check_name)
            outcome = by_name.get(check_name)
            if outcome is None:
                # Sub-check or layer not surfaced as its own LayerOutcome.
                # We mark it 'inherited' so the reader sees it's tracked
                # by the suite even though its detail is inside L9.
                members_status.append({
                    "check": check_name,
                    "script": member.get("script"),
                    "severity": member.get("severity"),
                    "status": "INHERITED_FROM_L9",
                })
                continue
            members_status.append({
                "check": check_name,
                "script": outcome.script,
                "severity": member.get("severity"),
                "status": outcome.status,
            })
            if outcome.status == "PASS":
                passed += 1
            elif outcome.status == "SKIP":
                skipped += 1
            else:
                failed += 1
        # Suite status: PASS if all blocker members passed (or skipped);
        # FAIL if any blocker failed; advisory-only failures do not flip
        # the suite, but they're visible in members_status.
        any_blocker_fail = any(
            m["status"] not in {"PASS", "SKIP", "INHERITED_FROM_L9"}
            and m.get("severity") == "blocker"
            for m in members_status
        )
        suite_status = "FAIL" if any_blocker_fail else "PASS"
        rolled.append({
            "suite": suite_name,
            "purpose": suite_def.get("purpose", ""),
            "status": suite_status,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "members": members_status,
        })
    # Append an 'unmapped' bucket if any LayerOutcome wasn't claimed by a suite.
    unmapped = [o for o in outcomes if o.name not in seen]
    if unmapped:
        rolled.append({
            "suite": "unmapped",
            "purpose": "Layer outcomes not yet assigned to a named suite (legacy or new).",
            "status": "ADVISORY",
            "passed": sum(1 for o in unmapped if o.status == "PASS"),
            "failed": sum(1 for o in unmapped if o.status not in {"PASS", "SKIP"}),
            "skipped": sum(1 for o in unmapped if o.status == "SKIP"),
            "members": [
                {"check": o.name, "script": o.script, "severity": "unassigned", "status": o.status}
                for o in unmapped
            ],
        })
    return rolled


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
    extra = LAYER_EXTRA_ARGS.get(name, []) if 'LAYER_EXTRA_ARGS' in globals() else []
    cmd = [sys.executable, str(script)] + (args or []) + extra
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


def attach_summary_l11(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L11_scripts"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l12(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L12_build_equiv"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l13(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L13_cross_tree"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l14(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L14_illustrations"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l15(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L15_data_ties"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l16(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L16_author_claims"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l27(outcome: LayerOutcome) -> None:
    p = LAYER_RESULT_JSONS["L27_stat_algo_sanity"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    outcome.summary = payload.get("summary", {})


def attach_summary_l28(outcome: LayerOutcome) -> None:
    """L28 symbolic algebra: surface PASS/FAIL counts and the names of any
    failing identities so the cert payload tells reviewers which math
    relation broke without forcing them to open the result JSON."""
    p = LAYER_RESULT_JSONS["L28_symbolic_algebra"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary", {}))
    failing = [i["name"] for i in payload.get("identities", [])
               if i.get("status") in ("FAIL", "ERROR")]
    if failing:
        summary["failing_identities"] = failing
    outcome.summary = summary


def attach_summary_l29(outcome: LayerOutcome) -> None:
    """L29 numerical bound check: surface per-bound PASS/FAIL counts and
    flag any bound whose claimed lower bound was numerically violated.
    The H1 demo entry (``thm:gram_ORIGINAL_buggy_demo'') is gated
    inverted -- it ``passes'' only when counter-examples are found, so
    a normal payload includes both gate types coexisting."""
    p = LAYER_RESULT_JSONS["L29_numerical_bounds"]
    if not p.exists():
        outcome.notes = "no results JSON found"
        return
    payload = json.loads(p.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary", {}))
    failing = [b["name"] for b in payload.get("bounds", []) if not b.get("passed")]
    if failing:
        summary["failing_bounds"] = failing
    # Surface the H1 demo's witness count so reviewers see that PAT's
    # original critique remains mechanically reproducible.
    h1 = next((b for b in payload.get("bounds", [])
               if b["name"] == "thm:gram_ORIGINAL_buggy_demo"), None)
    if h1:
        summary["h1_demo_violations_found"] = h1.get("n_violations", 0)
        summary["h1_demo_max_relative_violation"] = h1.get("max_relative_violation", 0.0)
    outcome.summary = summary


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
    structural = {"L1_audit", "L2_validator", "L4_lineage", "L7_citations", "L8_links", "L9_consistency", "L10_bib", "L11_scripts", "L12_build_equiv", "L13_cross_tree", "L14_illustrations", "L15_data_ties", "L27_stat_algo_sanity", "L28_symbolic_algebra", "L29_numerical_bounds"}
    structurally_failed = [o for o in outcomes if o.return_code != 0 and o.name in structural]
    if structurally_failed:
        names = ", ".join(o.name for o in structurally_failed)
        return ("FAIL", f"Structural layers failed: {names}")
    return ("PASS", "all structural checks clean (L1+L2+L4+L7+L8+L9+L10+L11+L12+L13+L14+L15+L27+L28+L29); L3+L5+L16 coverage is advisory, see triage JSONs")


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------
def render_markdown(payload: dict) -> str:
    p = payload["provenance"]
    v = payload["verdict"]
    lines: list[str] = []

    # ------------------------------------------------------------------
    # Reviewer one-pager (top of file). Designed for a reviewer who has
    # minutes, not hours: verdict + scope + spot-check recipe + limits.
    # ------------------------------------------------------------------
    lines.append(f"# Claim Certificate")
    lines.append("")
    lines.append("> **For reviewers:** this certificate proves the paper says what it says without numerical, citation, or figure-asset drift. It does NOT prove the experiments are well-designed or the conclusions follow. See **Scope** below.")
    lines.append("")
    lines.append(f"**Paper:** {p['paper_title']}")
    lines.append(f"**Venue:** {p['venue']}")
    lines.append(f"**Mode:** `{p.get('mode','quick')}` ({p.get('mode_note','')})")
    lines.append(f"**Generated:** {p['generated_at']}")
    lines.append(f"**Verdict:** **{v['verdict']}** — {v['rationale']}")
    lines.append("")
    lines.append("## Scope of certification")
    lines.append("")
    lines.append("| Label | What this certificate guarantees |")
    lines.append("|---|---|")
    lines.append("| **Mechanically verified** | Every numeric/citation claim with a ✓ row in the layer table below has been recomputed from source data and matches the paper text. |")
    lines.append("| **Consistency-checked** | Cross-claim arithmetic relations hold (e.g., decompositions sum correctly, formulas evaluate to tabulated values). |")
    lines.append("| **Advisory** | Coverage scans (L3, L5, L16) report uncovered numerics for human review. They do not block. |")
    lines.append("| **NOT certified** | Theorem proofs (math correctness beyond the encoded relations), experimental design, scientific judgment, the underlying truth of the claims about the world. |")
    lines.append("")
    lines.append("## Spot-check recipes")
    lines.append("")
    lines.append("Verify any flagship claim with a single command. Examples:")
    lines.append("")
    lines.append("```")
    lines.append("# Verify 0/4,272 smooth-regime refutations:")
    lines.append("python ci/claim_data_ties_check.py     # find smooth_regime_total_4272")
    lines.append("# Source: rebuttal/figures/unconditional_pivot_results.json (full_paper_claim.smooth_total)")
    lines.append("")
    lines.append("# Verify cross-model figure counts (9 models, 6 providers, N=3,120):")
    lines.append("python ci/cross_model_metadata_check.py")
    lines.append("# Source: supplementary/experiments/code_constraint_results.json + rebuttal/figures/cross_model_results.json")
    lines.append("")
    lines.append("# Re-run full cert:")
    lines.append("python ci/claim_certificate.py")
    lines.append("# Re-run release-mode cert (full L12 build equivalence + artifact hashes):")
    lines.append("python ci/claim_certificate.py --release")
    lines.append("```")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- main.tex sha256: `{p['main_tex_sha256'][:16]}...` (full hash in JSON)")
    lines.append(f"- main.tex mtime: {p['main_tex_mtime']}")
    lines.append(f"- main.pdf size: {p['main_pdf_size']:,} bytes")
    lines.append(f"- main.pdf mtime: {p['main_pdf_mtime']}")
    lines.append(f"- registry sha256: `{p['claim_audit_md_sha256'][:16]}...`")
    if "artifact_hashes" in payload:
        n_artifacts = len(payload["artifact_hashes"])
        lines.append(f"- artifact hashes: {n_artifacts} layer-result JSONs + manifests recorded in JSON payload")
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
        elif layer["name"] == "L11_scripts":
            blurb = f"{s.get('passed','?')}/{s.get('total_scripts','?')} figure scripts pass smoke test"
        elif layer["name"] == "L12_build_equiv":
            blurb = f"--quick mode: {s.get('passed','?')}/{s.get('total','?')} figures fresh; full mode optional"
        elif layer["name"] == "L13_cross_tree":
            blurb = f"{s.get('match','?')}/{s.get('total','?')} cross-tree files match (incl. expected divergences)"
        elif layer["name"] == "L14_illustrations":
            blurb = f"{s.get('passed','?')}/{s.get('total','?')} illustration provenance checks pass"
        elif layer["name"] == "L15_data_ties":
            blurb = f"{s.get('passed','?')}/{s.get('total','?')} numerical claims tied to source data"
        elif layer["name"] == "L16_author_claims":
            tied = s.get('tied','?'); total = s.get('total','?')
            ratio = s.get('tied_ratio')
            ratio_str = f"{100*ratio:.0f}%" if isinstance(ratio,(int,float)) else "?"
            blurb = f"{tied}/{total} judgment claims have data anchors ({ratio_str}; advisory)"
        elif layer["name"] == "L27_stat_algo_sanity":
            blurb = f"{s.get('total_blocker','?')} blocker / {s.get('total_warn','?')} warn (impossible p, algo guards, ref types, headline drift)"
        elif layer["name"] == "L28_symbolic_algebra":
            blurb = f"{s.get('passed','?')}/{s.get('total','?')} algebraic identities verified by SymPy"
            if s.get("failing_identities"):
                blurb += f"; FAIL: {', '.join(s['failing_identities'])}"
        elif layer["name"] == "L29_numerical_bounds":
            blurb = (f"{s.get('passed','?')}/{s.get('total_bounds','?')} bounds numerically verified "
                     f"(SLSQP counter-example search; H1 demo: "
                     f"{s.get('h1_demo_violations_found','?')} violations of original m=min_i m_i form)")
            if s.get("failing_bounds"):
                blurb += f"; FAIL: {', '.join(s['failing_bounds'])}"
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
    parser.add_argument("--release", action="store_true",
                        help="release mode: full L12 build equivalence (no --quick), embed artifact hashes in payload, fail on dirty git tree")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="allow --release to proceed even with a dirty working tree (records dirty=true in provenance)")
    parser.add_argument("--venue", choices=["auto", "neurips", "arxiv", "camera_ready"], default="neurips",
                        help="record submission venue in cert provenance. Default 'neurips' protects double-blind: the cert ships with the supplementary and must never accidentally record 'arxiv'. Use --venue auto only for non-anonymous local builds, --venue arxiv only when explicitly producing an arXiv-attached cert.")
    parser.add_argument("--profile", choices=["standard", "reviewer_quick", "artifact_eval", "author_release"], default="standard",
                        help="time-budgeted verification profile. standard (default): all layers, quick L12. reviewer_quick: skip L12+L11. artifact_eval: standard + tighter gates. author_release: same as --release.")
    args = parser.parse_args()

    if not MAIN_TEX.exists():
        print(f"ERROR: main.tex not found at {MAIN_TEX}", file=sys.stderr)
        return 2

    # Resolve mode + venue once, used by L12 invocation and provenance.
    # author_release profile implies --release.
    if args.profile == "author_release":
        args.release = True
    mode = "release" if args.release else "quick"
    venue = detect_venue() if args.venue == "auto" else args.venue
    profile = args.profile
    # Propagate profile to subprocess-wrapped sub-checks via env (used by
    # cross_claim_consistency_check.py to promote advisory → blocking).
    import os as _os
    _os.environ["CERT_PROFILE"] = profile
    # Make the resolved extra-args visible to run_layer's module-level lookup.
    global LAYER_EXTRA_ARGS
    LAYER_EXTRA_ARGS = _resolve_extra_args(mode)

    # Capture git state up-front; release mode rejects dirty trees unless allowed.
    git_state = get_git_state()

    # Dependency fingerprint (best-effort; non-fatal if module unavailable).
    dep_fingerprint = {}
    try:
        import importlib.util as _ilu
        dep_path = SCRIPT_DIR / "dependency_fingerprint.py"
        if dep_path.exists():
            spec = _ilu.spec_from_file_location("_dep_fp", dep_path)
            mod = _ilu.module_from_spec(spec)
            sys.modules["_dep_fp"] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "collect_fingerprint"):
                dep_fingerprint = mod.collect_fingerprint()
    except Exception:
        dep_fingerprint = {"status": "unavailable"}
    if mode == "release" and git_state.get("dirty") and not args.allow_dirty:
        print("ERROR: --release on dirty working tree.", file=sys.stderr)
        print(f"  modified={git_state['n_modified_unstaged']} staged={git_state['n_staged']} untracked={git_state['n_untracked']}", file=sys.stderr)
        print("  Either commit/stash changes or pass --allow-dirty.", file=sys.stderr)
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

    if _profile_skip(profile, "L11_scripts"):
        if not args.quiet: print("  L11 script integrity... [SKIPPED by profile]")
        o = LayerOutcome(name="L11_scripts", script=str(LAYER_SCRIPTS["L11_scripts"]),
                          return_code=0, summary={"skipped": True, "skip_reason": f"profile={profile}"})
    else:
        if not args.quiet: print("  L11 script integrity...")
        o = run_layer("L11_scripts", LAYER_SCRIPTS["L11_scripts"])
        attach_summary_l11(o)
    outcomes.append(o)

    if _profile_skip(profile, "L12_build_equiv"):
        if not args.quiet: print("  L12 build equivalence... [SKIPPED by profile]")
        o = LayerOutcome(name="L12_build_equiv", script=str(LAYER_SCRIPTS["L12_build_equiv"]),
                          return_code=0, summary={"skipped": True, "skip_reason": f"profile={profile}"})
    else:
        if not args.quiet:
            print("  L12 build equivalence (full)..." if mode == "release" else "  L12 build equivalence (--quick)...")
        o = run_layer("L12_build_equiv", LAYER_SCRIPTS["L12_build_equiv"])
        attach_summary_l12(o)
    outcomes.append(o)

    if not args.quiet: print("  L13 cross-tree consistency...")
    o = run_layer("L13_cross_tree", LAYER_SCRIPTS["L13_cross_tree"])
    attach_summary_l13(o)
    outcomes.append(o)

    if not args.quiet: print("  L14 illustration lineage...")
    o = run_layer("L14_illustrations", LAYER_SCRIPTS["L14_illustrations"])
    attach_summary_l14(o)
    outcomes.append(o)

    if not args.quiet: print("  L15 data-tied claims...")
    o = run_layer("L15_data_ties", LAYER_SCRIPTS["L15_data_ties"])
    attach_summary_l15(o)
    outcomes.append(o)

    if not args.quiet: print("  L16 author-judgment claims (advisory)...")
    o = run_layer("L16_author_claims", LAYER_SCRIPTS["L16_author_claims"])
    attach_summary_l16(o)
    outcomes.append(o)

    # Suite expansion (L17-L24): added 2026-05-04 to fill the BIS gaps Gemini's
    # taxonomy review surfaced. Each is a small standalone check that returns 0
    # for pass; advisory layers do not block the verdict. See suite_registry.json
    # for the per-suite assignment.
    for name in (
        "L17_table_values", "L18_sample_size_adequacy", "L19_ci_coverage",
        "L20_cross_source_recompute", "L21_sbom", "L22_container_lineage",
        "L23_license_clearance", "L24_pdf_camera_ready", "L25_multi_seed_drift",
        "L26_reference_convention",
    ):
        if not args.quiet: print(f"  {name}...")
        o = run_layer(name, LAYER_SCRIPTS[name])
        outcomes.append(o)

    # L27: statistical & algorithmic sanity (added 2026-05-04 in response
    # to PAT auto-feedback). Structural blocker; attaches summary so the
    # cert payload surfaces the blocker / warn counts directly.
    if not args.quiet: print("  L27_stat_algo_sanity...")
    o = run_layer("L27_stat_algo_sanity", LAYER_SCRIPTS["L27_stat_algo_sanity"])
    attach_summary_l27(o)
    outcomes.append(o)

    # L28: symbolic algebra. Mechanical SymPy verification of paper identities.
    # Counter-correlated witness against LLM-judge cert reviewers: SymPy fails
    # in a different way than LLMs do, so a SymPy PASS strengthens the cert
    # against shared-hallucination failure modes.
    if not args.quiet: print("  L28_symbolic_algebra...")
    o = run_layer("L28_symbolic_algebra", LAYER_SCRIPTS["L28_symbolic_algebra"])
    attach_summary_l28(o)
    outcomes.append(o)

    # L29: numerical bound check. Counter-example search via SLSQP for the
    # paper's stated lower bounds. Permanently demonstrates PAT's heterogeneous-m
    # critique via the inverted-gate H1 entry, alongside the post-fix forms.
    if not args.quiet: print("  L29_numerical_bounds...")
    o = run_layer("L29_numerical_bounds", LAYER_SCRIPTS["L29_numerical_bounds"])
    attach_summary_l29(o)
    outcomes.append(o)

    verdict, rationale = aggregate_verdict(outcomes)

    # Artifact hashes: every layer-result JSON + key manifests.
    # Lets verify_certificate.py detect post-cert tampering.
    artifact_hashes: dict[str, str] = {}
    for layer_name, json_path in LAYER_RESULT_JSONS.items():
        if json_path.exists():
            artifact_hashes[str(json_path.relative_to(REPO_ROOT))] = sha256_of(json_path)
    for manifest in (
        SCRIPT_DIR / "claim_data_ties.json",
        SCRIPT_DIR / "figure_lineage.json",
        SCRIPT_DIR / "illustration_lineage.json",
    ):
        if manifest.exists():
            artifact_hashes[str(manifest.relative_to(REPO_ROOT))] = sha256_of(manifest)

    # Venue label for the provenance block. Both NeurIPS and arXiv builds
    # use this same cert; the venue field records which one it covers.
    venue_labels = {
        "neurips": "NeurIPS 2026 (anonymous double-blind submission)",
        "arxiv": "arXiv preprint (preprint mode)",
        "camera_ready": "NeurIPS 2026 camera-ready (final)",
    }

    # Zero-work guard: in release mode, fail if a layer reports zero
    # checks performed. Catches "vacuously passed" failures.
    if mode == "release":
        zero_work = []
        for o in outcomes:
            s = o.summary or {}
            counts = [s.get(k) for k in ("total", "checks_total", "n_urls", "n_cites_in_paper", "n_refs", "total_scripts")]
            if any(c == 0 for c in counts if c is not None):
                zero_work.append(o.name)
        if zero_work:
            print(f"WARNING (release mode): zero-work layers detected: {zero_work}", file=sys.stderr)

    payload = {
        "schema_version": 3,
        "provenance": {
            "paper_title": "The Cost of Cacophony: Geometric Limits on Multi-Constraint Alignment",
            "venue": venue_labels.get(venue, venue),
            "venue_key": venue,
            "mode": mode,
            "mode_note": (
                "release: full L12 build equivalence + artifact hashes embedded; "
                "quick: --quick L12 (mtime+asset-hash only), faster local checks"
            ),
            "profile": profile,
            "git": git_state,
            "dependencies": dep_fingerprint,
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
        "artifact_hashes": artifact_hashes,
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
        "suites": _roll_up_suites(outcomes),
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
