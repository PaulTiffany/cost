#!/usr/bin/env python3
"""
figure_lineage_check.py - Verify the data -> script -> asset -> main.tex
chain for every figure in the paper.

Layer 4 of the claim certification stack. Today's stale-Figure-7 incident
(asset committed in one repo, paper rebuilt in another, "build clean"
reported on the wrong rendering) is exactly the failure mode this check
catches: the asset's mtime was OLDER than the script that produces it,
which would have been an immediate red flag.

Checks performed
----------------
A. Manifest <-> filesystem
   1. Every manifest entry's asset path exists
   2. Every manifest entry's script (if non-TikZ) exists
   3. Every declared data file exists

B. Manifest <-> main.tex
   4. Every \\includegraphics{...} / \\input{figures/...} in main.tex has
      a manifest entry (no orphan figures in the paper)
   5. Every manifest entry marked in_use!=false has a matching reference
      in main.tex (no orphan manifest entries)

C. mtime monotonicity (the meat of the check)
   6. For each non-TikZ figure with declared data:
        max(data mtimes) <= script mtime <= asset mtime
      A violation means: data was updated after the script ran, OR the
      script was edited after the asset was rendered. Either way, the
      asset is stale relative to its source.
   7. For non-TikZ figures with data_known=false:
        script mtime <= asset mtime
      (best-effort — we don't know the data dependencies)

Exit codes
----------
  0  all checks passed
  1  one or more checks failed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MANIFEST = SCRIPT_DIR / "figure_lineage.json"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"

INCLUDEGRAPHICS = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
INPUT_FIGURES = re.compile(r"\\input\{(figures/[^}]+)\}")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def resolve(p: str) -> Path:
    return (REPO_ROOT / p).resolve()


def fmt_mtime(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    import datetime
    ts = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# A. Manifest <-> filesystem
# ---------------------------------------------------------------------------
def check_assets_exist(manifest: dict) -> CheckResult:
    missing: list[str] = []
    for name, fig in manifest["figures"].items():
        path = resolve(fig["asset"])
        if not path.exists():
            missing.append(f"{name} -> {fig['asset']}")
    return CheckResult(
        f"A1. All {len(manifest['figures'])} manifest assets exist on disk",
        not missing,
        "\n         ".join(missing) if missing else "all assets resolved",
    )


def check_scripts_exist(manifest: dict) -> CheckResult:
    missing: list[str] = []
    n_checked = 0
    for name, fig in manifest["figures"].items():
        if fig.get("tikz_source"):
            continue
        n_checked += 1
        script_path = resolve(fig["script"])
        if not script_path.exists():
            missing.append(f"{name} -> {fig['script']}")
    return CheckResult(
        f"A2. All {n_checked} non-TikZ scripts exist on disk",
        not missing,
        "\n         ".join(missing) if missing else "all scripts resolved",
    )


def check_data_files_exist(manifest: dict) -> CheckResult:
    missing: list[str] = []
    n_checked = 0
    for name, fig in manifest["figures"].items():
        if fig.get("tikz_source"):
            continue
        for dpath in fig.get("data", []):
            n_checked += 1
            path = resolve(dpath)
            if not path.exists():
                missing.append(f"{name} -> {dpath}")
    return CheckResult(
        f"A3. All {n_checked} declared data files exist on disk",
        not missing,
        "\n         ".join(missing) if missing else "all data files resolved",
    )


def check_outputs_secondary_exist(manifest: dict) -> CheckResult:
    """Verify that any declared 'outputs_secondary' files exist on disk.

    These are non-figure artifacts that the script writes (typically
    JSON results files documenting computed values). They back textual
    claims and should be present in the supplementary package even
    though they're not loaded by the figure rendering itself.
    """
    missing: list[str] = []
    n_checked = 0
    for name, fig in manifest["figures"].items():
        for opath in fig.get("outputs_secondary", []):
            n_checked += 1
            path = resolve(opath)
            if not path.exists():
                missing.append(f"{name} -> {opath}")
    return CheckResult(
        f"A4. All {n_checked} declared output JSON artifacts exist on disk",
        not missing,
        "\n         ".join(missing) if missing else "all output artifacts resolved",
    )


def check_data_paths_in_script(manifest: dict) -> CheckResult:
    """Static-grep: every declared `data` path's filename should appear
    somewhere in its declaring script's source.

    Catches the failure mode where a script gets rewritten to read a
    different file but the manifest still claims the old dependency.
    Without this check, L4 only verifies file existence — the binding
    between script and data is unverified.

    Match strategy: the basename of each data path (e.g.
    'code_constraint_results.json') must appear textually in the
    script source. We use basename rather than full path because
    scripts often build paths via REPO_ROOT / 'subdir' / 'name.json',
    so the literal full path string isn't there.

    Skipped for figures with `data_known: false` (data is a script
    arg, not statically declarable).
    """
    bad: list[str] = []
    n_checked = 0
    for name, fig in manifest["figures"].items():
        if fig.get("tikz_source"):
            continue
        if not fig.get("data_known"):
            continue
        data_paths = fig.get("data", [])
        if not data_paths:
            continue
        script_path = resolve(fig["script"])
        if not script_path.exists():
            continue  # already reported by A2
        try:
            script_src = script_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            bad.append(f"{name}: could not read script source: {exc}")
            continue
        for dpath in data_paths:
            n_checked += 1
            basename = Path(dpath).name
            if basename not in script_src:
                bad.append(f"{name}: declared data '{dpath}' (basename '{basename}') not referenced in {fig['script']}")
    return CheckResult(
        f"A5. All {n_checked} declared data dependencies are referenced in their script's source",
        not bad,
        "\n         ".join(bad) if bad else "every declared data file is grep-bound to its script",
    )


# ---------------------------------------------------------------------------
# B. Manifest <-> main.tex
# ---------------------------------------------------------------------------
def extract_main_tex_figure_refs() -> set[str]:
    """Return the set of figure paths referenced in main.tex
    (\\includegraphics + \\input{figures/...})."""
    refs: set[str] = set()
    text = MAIN_TEX.read_text(encoding="utf-8", errors="replace")
    for m in INCLUDEGRAPHICS.finditer(text):
        refs.add(m.group(1))
    for m in INPUT_FIGURES.finditer(text):
        refs.add(m.group(1))
    return refs


def check_no_orphan_paper_refs(manifest: dict, paper_refs: set[str]) -> CheckResult:
    manifest_refs = {fig["main_tex_ref"] for fig in manifest["figures"].values() if fig.get("main_tex_ref")}
    orphans = sorted(paper_refs - manifest_refs)
    return CheckResult(
        f"B1. Every \\includegraphics/\\input in main.tex has a manifest entry",
        not orphans,
        "orphans (in paper but not in manifest):\n         " + "\n         ".join(orphans) if orphans else f"all {len(paper_refs)} references covered",
    )


def check_no_orphan_manifest(manifest: dict, paper_refs: set[str]) -> CheckResult:
    orphans: list[str] = []
    for name, fig in manifest["figures"].items():
        if fig.get("in_use") is False:
            continue  # explicitly marked as not in use
        ref = fig.get("main_tex_ref")
        if ref and ref not in paper_refs:
            orphans.append(f"{name} (manifest claims main_tex_ref={ref})")
    return CheckResult(
        "B2. Every in-use manifest entry is referenced in main.tex",
        not orphans,
        "\n         ".join(orphans) if orphans else "no orphan manifest entries",
    )


# ---------------------------------------------------------------------------
# C. mtime monotonicity
# ---------------------------------------------------------------------------
def check_mtime_freshness(manifest: dict, verbose: bool = False) -> CheckResult:
    """For each non-TikZ figure: asset must be at least as new as max(script, data).

    The relative ordering of script vs data doesn't matter on its own
    (data can be regenerated, script can be edited, in either order).
    What matters is whether the asset captures both — if the asset is
    older than EITHER the script or any data file, it's stale and must
    be re-rendered.
    """
    failures: list[str] = []
    n_checked = 0
    for name, fig in manifest["figures"].items():
        if fig.get("tikz_source"):
            continue
        if fig.get("in_use") is False:
            continue
        n_checked += 1

        asset = resolve(fig["asset"])
        script = resolve(fig["script"])
        if not asset.exists() or not script.exists():
            continue  # already reported by A1/A2

        asset_mtime = asset.stat().st_mtime
        script_mtime = script.stat().st_mtime

        # script <= asset
        if script_mtime > asset_mtime:
            failures.append(
                f"{name}: SCRIPT NEWER THAN ASSET\n"
                f"           script: {fmt_mtime(script)}  ({fig['script']})\n"
                f"           asset:  {fmt_mtime(asset)}  ({fig['asset']})\n"
                f"           => re-render the figure to pick up script changes"
            )
            continue

        # data <= asset (each data file must be older than the asset)
        if fig.get("data_known"):
            for dpath_str in fig.get("data", []):
                dpath = resolve(dpath_str)
                if not dpath.exists():
                    continue
                d_mtime = dpath.stat().st_mtime
                if d_mtime > asset_mtime:
                    failures.append(
                        f"{name}: DATA NEWER THAN ASSET (re-render needed)\n"
                        f"           data:  {fmt_mtime(dpath)}  ({dpath_str})\n"
                        f"           asset: {fmt_mtime(asset)}  ({fig['asset']})\n"
                        f"           => data updated, asset not re-rendered"
                    )

        if verbose:
            print(f"  [OK] {name}: asset {fmt_mtime(asset)} captures script + data")

    return CheckResult(
        f"C1. Asset freshness: asset >= max(script, data) for all {n_checked} rendered figures",
        not failures,
        "\n         ".join(failures) if failures else f"all {n_checked} figures fresh relative to their sources",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run_all(verbose: bool = False) -> list[CheckResult]:
    if not MANIFEST.exists():
        return [CheckResult("manifest exists", False, f"{MANIFEST} not found")]
    if not MAIN_TEX.exists():
        return [CheckResult("main.tex exists", False, f"{MAIN_TEX} not found")]

    manifest = load_manifest()
    paper_refs = extract_main_tex_figure_refs()

    return [
        check_assets_exist(manifest),
        check_scripts_exist(manifest),
        check_data_files_exist(manifest),
        check_outputs_secondary_exist(manifest),
        check_data_paths_in_script(manifest),
        check_no_orphan_paper_refs(manifest, paper_refs),
        check_no_orphan_manifest(manifest, paper_refs),
        check_mtime_freshness(manifest, verbose=verbose),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print per-figure mtime status")
    args = parser.parse_args()

    results = run_all(verbose=args.verbose)
    n_pass = sum(1 for r in results if r.passed)
    n_fail = len(results) - n_pass

    print("=" * 70)
    print("FIGURE LINEAGE CHECK  (data -> script -> asset -> main.tex)")
    print("=" * 70)
    print(f"Manifest: {MANIFEST}")
    print(f"Paper:    {MAIN_TEX}")
    print()
    for r in results:
        badge = "[OK]  " if r.passed else "[FAIL]"
        print(f"  {badge} {r.name}")
        if r.detail:
            print(f"         {r.detail}")
    print()
    print("-" * 70)
    print(f"  Passed: {n_pass:>2} / {len(results)}")
    print(f"  Failed: {n_fail:>2} / {len(results)}")
    print("-" * 70)

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
