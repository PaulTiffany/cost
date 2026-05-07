"""Reviewer path-and-chain integrity check.

Walks reviewer-facing markdown under the supplementary tree and verifies that
every path token, every line-range reference, every named cert layer, and
every cross-doc pointer resolves to a real file at the current state of the
repo.

Sub-checks (4):
  1. file_path_resolution     - backtick paths, table paths, link targets
  2. line_range_resolution    - path with line N or line range N to M
  3. cert_layer_reachability  - named layers L1, L30, L40 mapped to scripts
  4. doc_cross_references     - sibling md files and ci output json refs

Exit 0 on PASS, 1 on FAIL. Output goes to reviewer_path_chain_results.json
beside this script.

Pure stdlib. No anonymity-sensitive tokens are referenced.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPP_ROOT = REPO_ROOT / "supplementary"
SELF_PATH = Path(__file__).resolve()
RESULTS_PATH = REPO_ROOT / "ci" / "reviewer_path_chain_results.json"

REVIEWER_DOCS = [
    "REVIEWER_INDEX.md",
    "REVIEWER_QUICKSTART.md",
    "CERTIFICATE_ARCHITECTURE.md",
    "CLAIM_TO_ARTIFACT_MAP.md",
    "REPRODUCIBILITY_SMOKE_TEST.md",
    "PRE_REGISTRATION_AUDIT_OBSERVER_v4.md",
    "AUDIT_OBSERVER_INVARIANT_MAP.md",
    "GRADED_METRICS_SPEC.md",
    "ALT_TEXT.md",
]

# Cert layer name -> script that should exist for the layer to be reachable.
# Pulled from REVIEWER_INDEX.md and CERTIFICATE_ARCHITECTURE.md.
CERT_LAYER_SCRIPTS = {
    "L1_audit": "ci/claim_audit.py",
    "L2_validator": "ci/claim_audit_validator.py",
    "L3_sweep": "ci/claim_coverage_sweep.py",
    "L4_lineage": "ci/figure_lineage_check.py",
    "L5_figure_vals": "ci/figure_value_check.py",
    "L7_citations": "ci/citation_integrity_check.py",
    "L8_links": "ci/link_integrity_check.py",
    "L9_consistency": "ci/cross_claim_consistency_check.py",
    "L10_bib": "ci/bib_entry_check.py",
    "L11_scripts": "ci/script_integrity_check.py",
    "L12_build_equiv": "ci/build_equivalence_check.py",
    "L13_cross_tree": "ci/cross_tree_consistency_check.py",
    "L14_illustr": "ci/illustration_lineage_check.py",
    "L15_data_ties": "ci/claim_data_ties_check.py",
    "L16_author": "ci/author_claims_check.py",
    "L17_table_values": "ci/table_value_check.py",
    "L18_sample_size": "ci/sample_size_adequacy_check.py",
    "L19_ci_coverage": "ci/confidence_interval_coverage_check.py",
    "L20_cross_source": "ci/cross_source_recomputation_check.py",
    "L21_sbom": "ci/sbom_check.py",
    "L22_container": "ci/container_lineage_check.py",
    "L23_license": "ci/license_clearance_check.py",
    "L24_pdf_camera": "ci/pdf_camera_ready_check.py",
    "L25_multi_seed": "ci/multi_seed_drift_check.py",
    "L30_audit_observer_purity": "ci/audit_observer_purity_check.py",
    "L31_audit_observer_runtime": "ci/audit_observer_runtime_check.py",
    "L40_canonicality": "ci/audit/_canonicality_cert.py",
}

# Heuristic context tokens used by the line-range content sanity check.
CONTEXT_KEYWORDS = (
    "calibration", "band", "threshold", "ledger", "kill", "mutation",
    "verifier", "predicate", "relationclass", "decision", "audit",
    "observer", "smooth", "pivot", "pass", "fail", "hash", "sha",
    "import", "manifest", "claim", "router", "regret", "metric",
)

# Path-extraction patterns. Conservative; we want few false positives.
# Backtick form must contain a slash to avoid catching dotted method calls
# such as `module.attr` or `result.field`.
RX_BACKTICK_PATH = re.compile(
    r"`([A-Za-z0-9_./\\-]*[/\\][A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]{1,8})"
    r"(?::(\d+)(?:-(\d+))?)?`"
)
RX_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
RX_BARE_TABLE_PATH = re.compile(
    r"(?:(?<=\s)|(?<=\|)|(?<=^))"
    r"((?:ci|paper|supplementary|rebuttal)/[^\s|`)]+\.[A-Za-z0-9_]{1,8})"
)

# Patterns that look like paths but are not on-disk files.
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "tel:")

# Glob-y path tokens are skipped from the existence check. A glob is a
# coverage hint, not a single concrete file.
GLOB_CHARS = ("*", "?", "[")

# Extension allowlist. Tokens whose final dotted suffix is not in this set
# are treated as non-path (avoids catching `module.method_name` dotted refs).
KNOWN_EXTS = {
    "py", "tex", "md", "json", "jsonl", "yaml", "yml", "toml", "txt",
    "csv", "tsv", "bib", "sty", "bst", "cls", "cfg", "ini", "lock",
    "pdf", "png", "jpg", "jpeg", "gif", "svg", "tiff", "bmp",
    "wav", "mp3", "ogg", "flac",
    "ipynb", "html", "xml", "sh", "ps1", "bat", "zip", "tar", "gz",
    "npy", "npz", "pt", "pth", "ckpt", "bin", "h5", "log", "sqlite",
}


@dataclass
class CheckResult:
    name: str
    status: str = "PASS"
    blockers: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _looks_like_path(token: str) -> bool:
    if not token or token.startswith(SKIP_PREFIXES):
        return False
    if any(ch.isspace() for ch in token):
        return False
    # Require a slash so dotted attribute access like `module.attr` is not
    # mistaken for a path.
    if "/" not in token and "\\" not in token:
        return False
    return True


def _is_glob(token: str) -> bool:
    return any(ch in token for ch in GLOB_CHARS)


def _has_known_ext(token: str) -> bool:
    tail = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in tail:
        return False
    ext = tail.rsplit(".", 1)[-1].lower()
    return ext in KNOWN_EXTS


def extract_references(text: str) -> tuple[list[tuple[int, str]],
                                           list[tuple[int, str, int, int]]]:
    """Return (path_refs, line_refs) found in the given text.

    path_refs is a list of (line_number, path_string).
    line_refs is a list of (line_number, path_string, start_line, end_line).
    """
    paths: list[tuple[int, str]] = []
    lines: list[tuple[int, str, int, int]] = []
    seen_paths_per_line: set[tuple[int, str]] = set()

    for lineno, raw in enumerate(text.splitlines(), start=1):
        for m in RX_BACKTICK_PATH.finditer(raw):
            tok = m.group(1)
            if not _looks_like_path(tok) or not _has_known_ext(tok):
                continue
            start = m.group(2)
            end = m.group(3)
            if start:
                s = int(start)
                e = int(end) if end else s
                lines.append((lineno, tok, s, e))
            else:
                key = (lineno, tok)
                if key not in seen_paths_per_line:
                    paths.append(key)
                    seen_paths_per_line.add(key)
        for m in RX_LINK_TARGET.finditer(raw):
            tok = m.group(1).split("#", 1)[0]
            if not _looks_like_path(tok) or not _has_known_ext(tok):
                continue
            key = (lineno, tok)
            if key not in seen_paths_per_line:
                paths.append(key)
                seen_paths_per_line.add(key)
        for m in RX_BARE_TABLE_PATH.finditer(raw):
            tok = m.group(1).rstrip(".,;:")
            if not _looks_like_path(tok) or not _has_known_ext(tok):
                continue
            key = (lineno, tok)
            if key not in seen_paths_per_line:
                paths.append(key)
                seen_paths_per_line.add(key)

    return paths, lines


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _resolve(path_token: str, doc_dir: Path | None = None) -> Path:
    """Resolve a path token against the repo root or a given doc directory.

    A token starting with one of the canonical top-level prefixes is treated
    as repo-rooted. Anything else is resolved relative to doc_dir when one is
    supplied, falling back to the repo root.
    """
    cleaned = path_token.replace("\\", "/").lstrip("./")
    top = cleaned.split("/", 1)[0]
    if top in {"ci", "paper", "supplementary", "rebuttal", "tests",
              "conductor", "neurips_template", "neurips", "launch",
              "camera_ready", "bib", "archive", "audio_demos"}:
        return (REPO_ROOT / cleaned).resolve()
    if doc_dir is not None:
        return (doc_dir / cleaned).resolve()
    return (REPO_ROOT / cleaned).resolve()


def _file_line_count(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _file_lines(path: Path, start: int, end: int) -> list[str]:
    """Return lines [start, end] inclusive, 1-indexed. Best effort."""
    out: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if i < start:
                    continue
                if i > end:
                    break
                out.append(line)
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# Sub-checks
# ---------------------------------------------------------------------------

def check_file_paths(scans: dict[str, dict]) -> CheckResult:
    res = CheckResult(name="file_path_resolution")
    for doc, info in scans.items():
        doc_dir = info["doc_dir"]
        for lineno, tok in info["paths"]:
            if _is_glob(tok):
                continue
            target = _resolve(tok, doc_dir)
            if not target.exists():
                res.blockers.append(f"{doc}:{lineno}: path does not resolve `{tok}`")
    if res.blockers:
        res.status = "FAIL"
    return res


def check_line_ranges(scans: dict[str, dict]) -> CheckResult:
    res = CheckResult(name="line_range_resolution")
    for doc, info in scans.items():
        doc_dir = info["doc_dir"]
        for lineno, tok, s, e in info["lines"]:
            target = _resolve(tok, doc_dir)
            if not target.exists():
                res.blockers.append(
                    f"{doc}:{lineno}: line ref target missing `{tok}:{s}-{e}`"
                )
                continue
            if not target.is_file():
                res.blockers.append(
                    f"{doc}:{lineno}: line ref target is not a file `{tok}`"
                )
                continue
            count = _file_line_count(target)
            if s < 1 or e < s or e > count:
                res.blockers.append(
                    f"{doc}:{lineno}: line range out of bounds "
                    f"`{tok}:{s}-{e}` (file has {count} lines)"
                )
                continue
            # Heuristic content sanity check (advisory only).
            content = " ".join(_file_lines(target, s, e)).lower()
            if content and not any(k in content for k in CONTEXT_KEYWORDS):
                res.advisories.append(
                    f"{doc}:{lineno}: line range `{tok}:{s}-{e}` has no "
                    f"contextual keyword match (drift suspected)"
                )
    if res.blockers:
        res.status = "FAIL"
    return res


def check_cert_layers(scans: dict[str, dict]) -> CheckResult:
    res = CheckResult(name="cert_layer_reachability")
    layer_pattern = re.compile(r"\bL(\d+)\b")
    mentioned: set[int] = set()
    sources: dict[int, list[str]] = {}
    for doc in ("REVIEWER_INDEX.md", "CERTIFICATE_ARCHITECTURE.md",
                "REVIEWER_QUICKSTART.md"):
        if doc not in scans:
            continue
        text = scans[doc]["text"]
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in layer_pattern.finditer(line):
                num = int(m.group(1))
                mentioned.add(num)
                sources.setdefault(num, []).append(f"{doc}:{lineno}")

    # Build {layer_number: script_path} from CERT_LAYER_SCRIPTS keys.
    by_num: dict[int, tuple[str, str]] = {}
    for name, script in CERT_LAYER_SCRIPTS.items():
        try:
            num = int(name.split("_")[0][1:])
        except ValueError:
            continue
        by_num[num] = (name, script)

    for num in sorted(mentioned):
        if num not in by_num:
            # Layer mentioned in docs but unknown to our mapping. Advisory.
            first = sources[num][0] if sources.get(num) else "unknown"
            res.advisories.append(
                f"{first}: cert layer L{num} mentioned but not in known mapping"
            )
            continue
        _name, script = by_num[num]
        target = REPO_ROOT / script
        if not target.exists():
            first = sources[num][0] if sources.get(num) else "unknown"
            res.blockers.append(
                f"{first}: cert layer L{num} script missing `{script}`"
            )
    if res.blockers:
        res.status = "FAIL"
    return res


def check_cross_refs(scans: dict[str, dict]) -> CheckResult:
    res = CheckResult(name="doc_cross_references")
    # Catches mentions of sibling supp md files and ci output json files even
    # when they appear inside prose rather than table cells.
    rx_md = re.compile(r"`(supplementary/[A-Za-z0-9_./-]+\.md)`")
    rx_json = re.compile(r"`(ci/[A-Za-z0-9_./-]+\.json)`")
    for doc, info in scans.items():
        text = info["text"]
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in rx_md.finditer(line):
                tok = m.group(1)
                if not _resolve(tok).exists():
                    res.blockers.append(
                        f"{doc}:{lineno}: doc cross-ref missing `{tok}`"
                    )
            for m in rx_json.finditer(line):
                tok = m.group(1)
                if not _resolve(tok).exists():
                    res.blockers.append(
                        f"{doc}:{lineno}: ci output cross-ref missing `{tok}`"
                    )
    if res.blockers:
        res.status = "FAIL"
    return res


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def _load_scans() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in REVIEWER_DOCS:
        p = SUPP_ROOT / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        paths, lines = extract_references(text)
        out[name] = {
            "text": text,
            "paths": paths,
            "lines": lines,
            "doc_dir": p.parent,
        }
    return out


def _self_hash() -> str:
    h = hashlib.sha256()
    h.update(SELF_PATH.read_bytes())
    return h.hexdigest()


def _self_path_self_check(scans: dict[str, dict]) -> None:
    """Self-include: any path referenced in this script's own docstring or
    comments must resolve. We scan the script's source the same way we scan
    any reviewer doc."""
    text = SELF_PATH.read_text(encoding="utf-8", errors="replace")
    paths, lines = extract_references(text)
    scans["ci/reviewer_path_chain_check.py"] = {
        "text": text,
        "paths": paths,
        "lines": lines,
        "doc_dir": SELF_PATH.parent,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="suppress per-blocker stdout")
    args = parser.parse_args(argv)

    scans = _load_scans()
    _self_path_self_check(scans)

    checks = [
        check_file_paths(scans),
        check_line_ranges(scans),
        check_cert_layers(scans),
        check_cross_refs(scans),
    ]

    blockers_total = sum(len(c.blockers) for c in checks)
    advisories_total = sum(len(c.advisories) for c in checks)
    files_scanned = len(scans)
    paths_extracted = sum(len(s["paths"]) for s in scans.values())
    line_refs_extracted = sum(len(s["lines"]) for s in scans.values())

    overall = "FAIL" if blockers_total else "PASS"

    payload = {
        "_meta": {
            "script": "ci/reviewer_path_chain_check.py",
            "script_hash": _self_hash(),
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "status": overall,
        "summary": {
            "status": overall,
            "blockers": blockers_total,
            "advisories": advisories_total,
            "checks_run": len(checks),
            "files_scanned": files_scanned,
            "paths_extracted": paths_extracted,
            "line_refs_extracted": line_refs_extracted,
        },
        "checks": [
            {
                "name": c.name,
                "status": c.status,
                "blockers": list(c.blockers),
                "advisories": list(c.advisories),
            }
            for c in checks
        ],
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if not args.quiet:
        print("=" * 72)
        print("Reviewer Path-and-Chain Integrity Check")
        print("=" * 72)
        print(f"Files scanned:        {files_scanned}")
        print(f"Paths extracted:      {paths_extracted}")
        print(f"Line refs extracted:  {line_refs_extracted}")
        print(f"Blockers:             {blockers_total}")
        print(f"Advisories:           {advisories_total}")
        print(f"Verdict:              {overall}")
        print()
        for c in checks:
            print(f"  [{c.status}] {c.name}  "
                  f"(blockers={len(c.blockers)}, advisories={len(c.advisories)})")
        if blockers_total:
            print()
            print("-" * 72)
            print("BLOCKERS")
            print("-" * 72)
            for c in checks:
                for b in c.blockers:
                    print(f"  {c.name}  {b}")
        if advisories_total:
            print()
            print("-" * 72)
            print("ADVISORIES")
            print("-" * 72)
            for c in checks:
                for a in c.advisories:
                    print(f"  {c.name}  {a}")
        print()
        print(f"JSON report: {RESULTS_PATH}")

    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
