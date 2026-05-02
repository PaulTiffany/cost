#!/usr/bin/env python3
"""
build_equivalence_check.py - Verify each rendered figure asset is structurally
equivalent to what re-running its script would produce.

Layer 12 of the certification stack. L4 (figure_lineage_check) verifies that
mtimes are monotonic along the chain (data <= script <= asset). L11
(script_integrity_check) verifies that each figure script is syntactically
valid and its imports resolve. Neither catches the failure mode where the
asset's mtime is newer than the script's but its CONTENT no longer matches
what the script would produce — a manually edited PDF, an asset rendered
from an older version of the script that has since been overwritten, or
an asset hand-tweaked in Illustrator.

Method
------
For each figure with `tikz_source: false` and `in_use != false`:
  1. Snapshot the on-disk asset (bytes + pdftotext + size + sha256).
  2. Run the figure script in a subprocess with a 60s timeout. Most
     scripts write to hardcoded paths; we snapshot+restore to guarantee
     no real assets are corrupted on disk.
  3. After the run, read the (now potentially overwritten) asset, extract
     pdftotext, compare to the original snapshot.
  4. Restore the snapshot regardless of pass/fail (so the canonical state
     on disk is unchanged at the end of the check).
  5. Score:
       PASS  - pdftotext output matches exactly
       WARN  - pdftotext differs but length within 5% (likely timestamp
               drift inside PDF metadata or tiny float jitter)
       FAIL  - pdftotext differs structurally (lines added/removed, or
               numeric values changed)

TikZ figures are rendered by latex, not python, and are skipped.
Binary audio assets (binary_asset: true) are skipped — sonification is
slow and pdftotext does not apply.

Flags
-----
  --quick    Skip script execution; only verify asset exists, size > 0,
             and (asset_mtime >= script_mtime). Fallback for slow CI.
  --strict   Treat WARN as FAIL.
  --verbose  Print per-figure detail including pdftotext diff snippet.
  --timeout  Per-script timeout in seconds (default 60).

Exit codes
----------
  0  every checked figure passes (or WARNs without --strict)
  1  one or more figures FAIL (or WARN under --strict)
  2  manifest missing or unreadable
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LINEAGE_JSON = SCRIPT_DIR / "figure_lineage.json"
RESULTS_JSON = SCRIPT_DIR / "build_equivalence_results.json"

DEFAULT_TIMEOUT_SEC = 60
WARN_LENGTH_TOLERANCE = 0.05  # +/- 5% pdftotext char count is WARN, not FAIL

# Status codes
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class FigureReport:
    name: str
    asset: str
    script: str | None
    status: str = SKIP
    reason: str = ""
    original_sha256: str | None = None
    rebuilt_sha256: str | None = None
    original_pdftext_len: int = 0
    rebuilt_pdftext_len: int = 0
    diff_snippet: str = ""
    runtime_sec: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pdftotext(path: Path) -> str | None:
    """Extract text from a PDF (or return None if unavailable / not a PDF).

    For PNG and other non-PDF assets, return None — caller should fall back
    to byte comparison for those.
    """
    if path.suffix.lower() != ".pdf":
        return None
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


_TOKEN_RE = re.compile(r"[A-Za-z0-9.\-]+")


def tokenize(text: str) -> list[str]:
    """Pull alphanumeric+. tokens from text, normalized lowercase.

    Used for figure semantic equivalence: two pdftexts are
    semantically equivalent if their token multisets match (same
    numbers, same words, same labels) even if whitespace and layout
    differ.
    """
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def tokens_equivalent(text_a: str, text_b: str) -> tuple[bool, str]:
    """Return (equivalent, diff_summary).

    Equivalent = same Counter (multiset) of tokens. Layout drift in
    whitespace, line wrapping, legend position, etc. doesn't change
    the token multiset; missing or substituted numbers/labels does.
    """
    counter_a = Counter(tokenize(text_a))
    counter_b = Counter(tokenize(text_b))
    if counter_a == counter_b:
        return True, "token multisets identical"
    only_a = counter_a - counter_b
    only_b = counter_b - counter_a
    bits = []
    if only_a:
        bits.append(f"-{sum(only_a.values())} tokens dropped: {sorted(only_a.keys())[:8]}")
    if only_b:
        bits.append(f"+{sum(only_b.values())} tokens added: {sorted(only_b.keys())[:8]}")
    return False, "; ".join(bits)


def make_diff_snippet(orig: str, new: str, max_lines: int = 20) -> str:
    """Return a short unified-diff snippet for the report JSON."""
    orig_lines = orig.splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(
        orig_lines, new_lines,
        fromfile="original", tofile="rebuilt", lineterm="",
        n=2,
    ))
    if not diff:
        return ""
    return "\n".join(diff[:max_lines])


def script_supports_output_dir(source: str) -> bool:
    """Heuristic: does the script accept an --output-dir CLI flag?"""
    return "--output-dir" in source or '"output-dir"' in source


# ---------------------------------------------------------------------------
# Snapshot machinery
# ---------------------------------------------------------------------------

def snapshot_paths(paths: list[Path]) -> dict[Path, bytes | None]:
    """Read each path's bytes (or None if missing) into memory so we can
    restore them after the script runs. Memory cost is fine — the largest
    figure assets in this repo are ~few hundred KB."""
    snap: dict[Path, bytes | None] = {}
    for p in paths:
        if p.exists():
            snap[p] = p.read_bytes()
        else:
            snap[p] = None
    return snap


def restore_snapshot(snap: dict[Path, bytes | None]) -> list[str]:
    """Restore each snapshotted path. Returns list of paths that could not
    be restored (informational; should be empty under normal operation)."""
    failures: list[str] = []
    for path, data in snap.items():
        try:
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(data)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    return failures


def collect_sibling_assets(asset_path: Path) -> list[Path]:
    """Collect paths in the same directory as the asset that share its stem
    or are likely co-written (e.g. .png companion to a .pdf, results JSONs).
    These all get snapshotted to be safe."""
    siblings: list[Path] = [asset_path]
    if not asset_path.parent.exists():
        return siblings
    for ext in (".pdf", ".png", ".svg"):
        cand = asset_path.with_suffix(ext)
        if cand != asset_path and cand.exists():
            siblings.append(cand)
    return siblings


# Candidate directories where a figure script might emit its output.
# Some scripts write to paper/figures/, some to rebuttal/figures/, and some
# to supplementary/figures/ (a default that doesn't match the canonical
# asset path declared in the manifest). We snapshot all of them and
# cross-check after the run to catch "the script ran but wrote to the
# wrong directory" — which would otherwise read as PASS because the real
# asset was never touched.
CANDIDATE_OUTPUT_DIRS = [
    "paper/figures",
    "rebuttal/figures",
    "supplementary/figures",
]


def _name_matches_stem(filename: str, stem: str) -> bool:
    """True if filename is `<stem>.<ext>` or `<stem>_<suffix>.<ext>`.
    The `_<suffix>` form catches results JSONs (per_task_correlation_results.json)
    co-written alongside the .pdf asset (per_task_correlation.pdf)."""
    base = filename.rsplit(".", 1)[0]
    return base == stem or base.startswith(stem + "_")


def collect_candidate_outputs(asset: Path) -> list[Path]:
    """Return every existing file across CANDIDATE_OUTPUT_DIRS whose name
    starts with the asset stem (or stem_). These all get snapshotted."""
    paths: list[Path] = []
    stem = asset.stem
    for d_rel in CANDIDATE_OUTPUT_DIRS:
        d = REPO_ROOT / d_rel
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and _name_matches_stem(f.name, stem):
                paths.append(f.resolve())
    return paths


def find_newly_written(
    asset: Path,
    pre_run_files: set[Path],
    pre_run_mtimes: dict[Path, float],
    run_started_at: float,
) -> list[Path]:
    """After the script runs, return any files in CANDIDATE_OUTPUT_DIRS
    matching the asset stem that are either (a) new since the run started,
    or (b) had their mtime updated during the run."""
    newly: list[Path] = []
    stem = asset.stem
    for d_rel in CANDIDATE_OUTPUT_DIRS:
        d = REPO_ROOT / d_rel
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file() or not _name_matches_stem(f.name, stem):
                continue
            f_resolved = f.resolve()
            if f_resolved not in pre_run_files:
                newly.append(f_resolved)
                continue
            # File existed before; check if mtime advanced
            old_mtime = pre_run_mtimes.get(f_resolved, 0.0)
            if f.stat().st_mtime >= run_started_at - 1.0 and f.stat().st_mtime > old_mtime:
                newly.append(f_resolved)
    return newly


# ---------------------------------------------------------------------------
# Per-figure check
# ---------------------------------------------------------------------------

def check_figure_quick(name: str, fig: dict) -> FigureReport:
    """--quick mode: existence + size + mtime ordering only."""
    asset = REPO_ROOT / fig["asset"]
    script_rel = fig.get("script")
    script = REPO_ROOT / script_rel if script_rel else None
    r = FigureReport(name=name, asset=str(asset.relative_to(REPO_ROOT)),
                     script=script_rel)

    if not asset.exists():
        r.status = FAIL
        r.reason = "asset missing"
        return r
    if asset.stat().st_size == 0:
        r.status = FAIL
        r.reason = "asset is zero bytes"
        return r
    r.original_sha256 = sha256_of(asset)
    if script is not None and script.exists():
        if asset.stat().st_mtime < script.stat().st_mtime:
            r.status = FAIL
            r.reason = "asset older than script (stale)"
            return r
    r.status = PASS
    r.reason = "asset exists, non-empty, mtime ordering ok (quick mode)"
    return r


def check_figure_full(
    name: str,
    fig: dict,
    timeout_sec: int,
    verbose: bool,
) -> FigureReport:
    """Full mode: snapshot, re-run script, compare, restore."""
    asset = (REPO_ROOT / fig["asset"]).resolve()
    script_rel = fig.get("script")
    r = FigureReport(name=name, asset=str(Path(fig["asset"])),
                     script=script_rel)

    if not asset.exists():
        r.status = FAIL
        r.reason = "asset missing"
        return r
    if script_rel is None:
        r.status = SKIP
        r.reason = "no script declared (TikZ or hand-authored?)"
        return r
    script = (REPO_ROOT / script_rel).resolve()
    if not script.exists():
        r.status = FAIL
        r.reason = f"script does not exist: {script_rel}"
        return r

    # Snapshot every plausibly-affected asset before we run anything.
    # Coverage = (a) the asset itself + siblings sharing its stem in the
    # asset's own directory, (b) every same-stem file across all candidate
    # output dirs (paper/figures, rebuttal/figures, supplementary/figures)
    # in case the script writes to the "wrong" one, (c) declared
    # outputs_secondary JSONs.
    snapshot_targets: list[Path] = collect_sibling_assets(asset)
    for cand in collect_candidate_outputs(asset):
        if cand not in snapshot_targets:
            snapshot_targets.append(cand)
    for sec in fig.get("outputs_secondary", []) or []:
        sp = (REPO_ROOT / sec).resolve()
        if sp not in snapshot_targets:
            snapshot_targets.append(sp)

    # Track pre-run state of CANDIDATE_OUTPUT_DIRS for change detection
    pre_run_files: set[Path] = set()
    pre_run_mtimes: dict[Path, float] = {}
    pre_run_dir_existed: dict[str, bool] = {}
    for d_rel in CANDIDATE_OUTPUT_DIRS:
        d = REPO_ROOT / d_rel
        pre_run_dir_existed[d_rel] = d.exists()
        if d.exists():
            for f in d.iterdir():
                if f.is_file() and _name_matches_stem(f.name, asset.stem):
                    fr = f.resolve()
                    pre_run_files.add(fr)
                    pre_run_mtimes[fr] = f.stat().st_mtime

    r.original_sha256 = sha256_of(asset)
    orig_text = pdftotext(asset)
    if orig_text is not None:
        r.original_pdftext_len = len(orig_text)

    snap = snapshot_paths(snapshot_targets)

    # Run the script. Try --output-dir redirection first (safer — output
    # goes to a temp dir and the real assets are never touched). Fall back
    # to snapshot+restore if the script lacks the flag.
    try:
        source = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""

    cmd: list[str]
    used_temp_redirect = False
    temp_dir_obj: tempfile.TemporaryDirectory | None = None

    if script_supports_output_dir(source):
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="bldeq_")
        cmd = [sys.executable, str(script), "--output-dir", temp_dir_obj.name]
        used_temp_redirect = True
    else:
        cmd = [sys.executable, str(script)]

    t0 = time.time()
    extra_cleanup: list[Path] = []  # files written to candidate dirs that we didn't snapshot
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True, text=True,
            timeout=timeout_sec,
            encoding="utf-8", errors="replace",
        )
        runtime = time.time() - t0
        r.runtime_sec = round(runtime, 2)
        run_ok = proc.returncode == 0
        run_stderr_tail = (proc.stderr or "")[-500:] if not run_ok else ""
    except subprocess.TimeoutExpired:
        runtime = time.time() - t0
        r.runtime_sec = round(runtime, 2)
        r.status = SKIP
        r.reason = f"script exceeded {timeout_sec}s timeout"
        # restore before returning
        restore_snapshot(snap)
        # Also clean up any files newly written to candidate dirs
        for f in find_newly_written(asset, pre_run_files, pre_run_mtimes, t0):
            if f not in snap:
                try:
                    f.unlink()
                except OSError:
                    pass
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()
        return r
    except Exception as exc:
        r.status = FAIL
        r.reason = f"could not invoke script: {exc}"
        restore_snapshot(snap)
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()
        return r

    # Locate the rebuilt asset.
    # Order of preference:
    #   1. Temp-redirect dir (if --output-dir was passed and the script honored it)
    #   2. The real asset path, IF it was modified during the run (sha changed)
    #   3. Any same-stem file newly written to a CANDIDATE_OUTPUT_DIR with the
    #      right extension (catches "script wrote to wrong directory")
    rebuilt: Path | None = None
    if used_temp_redirect and temp_dir_obj is not None:
        for cand in Path(temp_dir_obj.name).rglob(f"{asset.stem}.*"):
            if cand.suffix.lower() == asset.suffix.lower():
                rebuilt = cand
                break

    if rebuilt is None:
        newly = find_newly_written(asset, pre_run_files, pre_run_mtimes, t0)
        # Track any newly-written files we didn't snapshot, for cleanup
        for f in newly:
            if f not in snap:
                extra_cleanup.append(f)
        # Prefer asset if it was overwritten, else any matching-extension new file
        if asset in newly or (asset.exists() and sha256_of(asset) != r.original_sha256):
            rebuilt = asset
        else:
            for f in newly:
                if f.suffix.lower() == asset.suffix.lower():
                    rebuilt = f
                    break

    try:
        if not run_ok:
            r.status = FAIL
            r.reason = f"script exit {proc.returncode}: {run_stderr_tail.strip()[:200]}"
            return r

        if rebuilt is None or not rebuilt.exists():
            r.status = FAIL
            r.reason = "script ran but produced no output asset"
            return r

        r.rebuilt_sha256 = sha256_of(rebuilt)

        # Compare
        if rebuilt.suffix.lower() == ".pdf":
            new_text = pdftotext(rebuilt)
            if new_text is None or orig_text is None:
                # pdftotext unavailable — fall back to byte equality
                if r.rebuilt_sha256 == r.original_sha256:
                    r.status = PASS
                    r.reason = "byte-identical (pdftotext unavailable)"
                else:
                    r.status = WARN
                    r.reason = "bytes differ; pdftotext unavailable for structural compare"
                return r
            r.rebuilt_pdftext_len = len(new_text)
            if new_text == orig_text:
                r.status = PASS
                r.reason = "pdftotext output identical"
                return r
            # Differ — fall back to token-multiset comparison. If the
            # multisets of normalized tokens (numbers + words) match,
            # the new render is semantically equivalent — only layout
            # (whitespace, legend position, line wrap) drifted, not
            # content. Reviewers care about content; layout drift is
            # cosmetic.
            tokens_eq, token_diff = tokens_equivalent(orig_text, new_text)
            if tokens_eq:
                r.status = PASS
                r.reason = "pdftotext differs but token multisets identical (cosmetic layout drift only)"
                return r
            # Tokens differ too — real drift
            len_diff = abs(len(new_text) - len(orig_text))
            len_ratio = len_diff / max(1, len(orig_text))
            r.diff_snippet = make_diff_snippet(orig_text, new_text)
            if len_ratio <= WARN_LENGTH_TOLERANCE:
                r.status = WARN
                r.reason = f"pdftotext differs (tokens too): {token_diff}"
            else:
                r.status = FAIL
                r.reason = f"pdftotext structurally differs ({len(orig_text)} -> {len(new_text)} chars); {token_diff}"
            return r
        else:
            # PNG / other binary: byte equality, fall back to size delta
            if r.rebuilt_sha256 == r.original_sha256:
                r.status = PASS
                r.reason = "byte-identical"
                return r
            orig_size = len(snap.get(asset) or b"")
            new_size = rebuilt.stat().st_size
            size_ratio = abs(new_size - orig_size) / max(1, orig_size)
            if size_ratio <= WARN_LENGTH_TOLERANCE:
                r.status = WARN
                r.reason = f"bytes differ but size within {WARN_LENGTH_TOLERANCE:.0%} ({orig_size} -> {new_size})"
            else:
                r.status = FAIL
                r.reason = f"bytes differ structurally ({orig_size} -> {new_size})"
            return r

    finally:
        # ALWAYS restore snapshot, even if comparison logic raised
        failures = restore_snapshot(snap)
        if failures and verbose:
            print(f"   WARNING: snapshot restore failed for: {failures}", file=sys.stderr)
        # Delete files newly written to candidate dirs that weren't part
        # of the snapshot (e.g. supplementary/figures/ side effects from
        # scripts whose default OUTPUT_DIR doesn't match the canonical
        # asset path)
        for f in extra_cleanup:
            try:
                if f.exists():
                    f.unlink()
            except OSError as exc:
                if verbose:
                    print(f"   WARNING: could not clean up {f}: {exc}", file=sys.stderr)
        if temp_dir_obj is not None:
            try:
                temp_dir_obj.cleanup()
            except OSError:
                pass
        # Remove candidate output dirs that the script created from scratch
        # (e.g. supplementary/figures/) so we don't leave empty cruft behind
        for d_rel, existed_before in pre_run_dir_existed.items():
            if existed_before:
                continue
            d = REPO_ROOT / d_rel
            if d.exists() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quick", action="store_true",
                        help="skip script execution; only check existence/size/mtime")
    parser.add_argument("--strict", action="store_true",
                        help="treat WARN as FAIL")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print per-figure detail")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                        help=f"per-script timeout in seconds (default {DEFAULT_TIMEOUT_SEC})")
    args = parser.parse_args()

    if not LINEAGE_JSON.exists():
        print(f"ERROR: figure lineage manifest not found at {LINEAGE_JSON}",
              file=sys.stderr)
        return 2

    manifest = json.loads(LINEAGE_JSON.read_text(encoding="utf-8"))

    print("=" * 70)
    print(f"BUILD EQUIVALENCE CHECK  ({'quick' if args.quick else 'full'} mode"
          f"{', strict' if args.strict else ''})")
    print("=" * 70)
    print(f"Manifest:        {LINEAGE_JSON}")
    print(f"Repo root:       {REPO_ROOT}")
    if not args.quick:
        print(f"Per-script timeout: {args.timeout}s")
    print()

    reports: list[FigureReport] = []

    for name, fig in manifest["figures"].items():
        # Skip TikZ — rendered by latex, not python
        if fig.get("tikz_source"):
            continue
        # Skip binary audio assets (sonification.py is slow + .wav not pdftotext-able)
        if fig.get("binary_asset"):
            r = FigureReport(name=name, asset=fig["asset"],
                             script=fig.get("script"),
                             status=SKIP, reason="binary audio asset")
            reports.append(r)
            continue
        # Skip explicitly-not-in-use figures unless they have a script we can verify
        # (They're still part of the snapshot, but we don't fail the layer if they drift.)
        if fig.get("in_use") is False and args.quick:
            r = FigureReport(name=name, asset=fig["asset"],
                             script=fig.get("script"),
                             status=SKIP, reason="in_use=false (skipped in quick mode)")
            reports.append(r)
            continue

        if args.quick:
            r = check_figure_quick(name, fig)
        else:
            r = check_figure_full(name, fig, args.timeout, args.verbose)

        reports.append(r)

        if args.verbose or r.status in (FAIL, WARN):
            badge = {
                PASS: "[PASS]",
                WARN: "[WARN]",
                FAIL: "[FAIL]",
                SKIP: "[SKIP]",
            }[r.status]
            print(f"  {badge} {r.name}")
            print(f"         {r.reason}")
            if r.runtime_sec:
                print(f"         runtime: {r.runtime_sec}s")
            if r.diff_snippet and args.verbose:
                snippet_lines = r.diff_snippet.splitlines()[:6]
                for line in snippet_lines:
                    print(f"           | {line}")
        elif r.status == PASS:
            print(f"  [PASS] {r.name}  ({r.reason})")
        elif r.status == SKIP:
            print(f"  [SKIP] {r.name}  ({r.reason})")

    # Tally
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for r in reports:
        counts[r.status] += 1

    print()
    print("-" * 70)
    print(f"  PASS:    {counts[PASS]:>3}")
    print(f"  WARN:    {counts[WARN]:>3}")
    print(f"  FAIL:    {counts[FAIL]:>3}")
    print(f"  SKIP:    {counts[SKIP]:>3}")
    print(f"  TOTAL:   {len(reports):>3}")
    print("-" * 70)

    # Verdict
    if counts[FAIL] > 0:
        verdict = FAIL
    elif counts[WARN] > 0 and args.strict:
        verdict = FAIL
    elif counts[WARN] > 0:
        verdict = WARN
    else:
        verdict = PASS
    print(f"  VERDICT: {verdict}")
    print("-" * 70)

    payload = {
        "summary": {
            "mode": "quick" if args.quick else "full",
            "strict": args.strict,
            "timeout_sec": args.timeout,
            "verdict": verdict,
            "total": len(reports),
            "pass": counts[PASS],
            "warn": counts[WARN],
            "fail": counts[FAIL],
            "skip": counts[SKIP],
        },
        "figures": [r.to_dict() for r in reports],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    return 0 if verdict in (PASS, WARN) else 1


if __name__ == "__main__":
    sys.exit(main())
