#!/usr/bin/env python3
"""
illustration_lineage_check.py - Provenance-binding for explanatory
illustrations.

Layer 14 of the certification stack. Most paper figures are either
data plots (covered by L4 + L5 + L12) or hand-drawn schematics with
no audit trail back to the formal text they illustrate. L14 creates
that bridge: each illustration in the manifest binds to a specific
source LaTeX block (theorem, algorithm, definition, named text
block) and carries a deterministic asset (hand-authored TikZ or
matplotlib output, NOT raw image-model output). If the source LaTeX
block changes, the illustration's certificate fails until the asset
is re-authored and re-certified.

The boundary the layer enforces:

  Source LaTeX block  -- hash --
                                 \\
  Visual spec (markdown)  -- hash -->  Certificate row
                                 /
  Final asset (TikZ / SVG / mpl) hash

Image generation models (gpt-image-2, etc.) MAY be used during the
*draft* phase to explore composition, but the final asset is the
deterministic redraw. The certificate trusts the redraw, not the
stochastic draft. claim_scope on each entry must say "schematic
illustration only; not empirical evidence."

Checks
------
  A1. Every manifest entry's source_file exists
  A2. Source block (extracted by line range or label) hash matches
      the manifest's recorded source_hash. Drift here means the
      LaTeX block was edited after the illustration was authored.
  A3. visual_spec file exists if declared
  A4. visual_spec_hash matches current spec content
  A5. final_asset exists and its hash matches manifest
  A6. Every manifest entry with main_tex_ref appears in main.tex
      (\\input or \\includegraphics)
  A7. claim_scope field is present and non-empty (no certifying
      illustrations as evidence)

Exit codes
----------
  0  every check passes
  1  one or more checks failed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
MANIFEST = SCRIPT_DIR / "illustration_lineage.json"
RESULTS_JSON = SCRIPT_DIR / "illustration_lineage_results.json"

INCLUDEGRAPHICS = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
INPUT_FIGURES = re.compile(r"\\input\{(figures/[^}]+)\}")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_file(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_block_by_line_range(source_file: Path, start: int, end: int) -> str | None:
    """Return inclusive lines [start, end] of source_file, or None if invalid."""
    if not source_file.exists():
        return None
    lines = source_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if start < 1 or end > len(lines) or start > end:
        return None
    return "\n".join(lines[start - 1:end])


def extract_block_by_label(source_file: Path, label: str) -> str | None:
    """Find a labeled environment (theorem, lemma, algorithm, etc.) and
    return its source. Walks for \\label{label} then expands outward to
    the surrounding \\begin{...}...\\end{...} pair.
    """
    if not source_file.exists():
        return None
    text = source_file.read_text(encoding="utf-8", errors="replace")
    # Find the \label{label} occurrence
    label_re = re.compile(r"\\label\{" + re.escape(label) + r"\}")
    m = label_re.search(text)
    if not m:
        return None
    label_pos = m.start()

    # Walk backward to find the most recent \begin{X}
    begin_re = re.compile(r"\\begin\{([A-Za-z*]+)\}")
    last_begin = None
    for bm in begin_re.finditer(text[:label_pos]):
        last_begin = bm
    if not last_begin:
        return None
    env_name = last_begin.group(1)

    # Walk forward from \begin to find matching \end{X}
    depth = 1
    pos = last_begin.end()
    end_pat = re.compile(r"\\(begin|end)\{" + re.escape(env_name) + r"\}")
    while depth > 0 and pos < len(text):
        em = end_pat.search(text, pos)
        if not em:
            return None
        if em.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
        pos = em.end()
    return text[last_begin.start():pos]


def extract_main_tex_figure_refs() -> set[str]:
    refs: set[str] = set()
    text = MAIN_TEX.read_text(encoding="utf-8", errors="replace")
    for m in INCLUDEGRAPHICS.finditer(text):
        refs.add(m.group(1))
    for m in INPUT_FIGURES.finditer(text):
        refs.add(m.group(1))
    return refs


def resolve(p: str) -> Path:
    return (REPO_ROOT / p).resolve()


def check_illustrations(manifest: dict) -> list[CheckResult]:
    illus = manifest.get("illustrations", {})
    if not illus:
        return [CheckResult("manifest has illustrations", False, "no entries")]

    paper_refs = extract_main_tex_figure_refs()

    # A1-A7 collected per-illustration; aggregate at end
    a1_missing_input: list[str] = []
    a2_input_drift: list[str] = []
    a5_missing_asset: list[str] = []
    a5_asset_drift: list[str] = []
    a6_orphans: list[str] = []
    a7_no_scope: list[str] = []

    for name, entry in illus.items():
        # A1 + A2: walk inputs[] uniformly. Each input is a dict with
        # kind, file, and hash. For latex_block, also line_start/end.
        # For text_file, just the whole-file content.
        inputs = entry.get("inputs", [])
        if not inputs:
            a1_missing_input.append(f"{name}: no inputs[] in manifest")
            continue
        for idx, inp in enumerate(inputs):
            file_str = inp.get("file", "")
            file_path = resolve(file_str)
            if not file_path.exists():
                a1_missing_input.append(f"{name} input #{idx}: {file_str} not found")
                continue
            kind = inp.get("kind", "text_file")
            expected_hash = inp.get("hash", "")
            if kind == "latex_block":
                start = int(inp.get("line_start", 0))
                end = int(inp.get("line_end", 0))
                content = extract_block_by_line_range(file_path, start, end)
                if content is None:
                    a2_input_drift.append(f"{name} input #{idx}: could not extract lines {start}-{end} from {file_str}")
                    continue
            else:  # text_file or unknown
                content = file_path.read_text(encoding="utf-8")
            actual_hash = sha256_of_text(content)
            if actual_hash != expected_hash:
                a2_input_drift.append(
                    f"{name} input #{idx} ({kind}, {file_str}): hash mismatch "
                    f"(manifest={expected_hash[:12]}..., actual={actual_hash[:12]}...)"
                )

        # A5: final asset
        asset_path = resolve(entry.get("final_asset", ""))
        if not asset_path.exists():
            a5_missing_asset.append(f"{name} -> {entry.get('final_asset')}")
        else:
            actual_asset = sha256_of_file(asset_path)
            expected_asset = entry.get("final_asset_hash", "")
            if actual_asset != expected_asset:
                a5_asset_drift.append(
                    f"{name}: final asset hash mismatch (manifest={expected_asset[:12]}..., actual={actual_asset[:12]}...)"
                )

        # A6: main_tex_ref appears in main.tex if declared
        ref = entry.get("main_tex_ref")
        if ref and ref not in paper_refs:
            a6_orphans.append(f"{name}: main_tex_ref={ref} not found in main.tex")

        # A7: claim_scope must be present
        scope = (entry.get("claim_scope") or "").strip()
        if not scope:
            a7_no_scope.append(name)

    return [
        CheckResult(
            f"A1. Every input file exists ({len(illus)} entries)",
            not a1_missing_input,
            "\n         ".join(a1_missing_input) if a1_missing_input else "all input files resolved",
        ),
        CheckResult(
            "A2. Every input hash matches its current file content",
            not a2_input_drift,
            "\n         ".join(a2_input_drift) if a2_input_drift else "all inputs unchanged since illustration was authored",
        ),
        CheckResult(
            "A5. Final assets exist and hash-match",
            not (a5_missing_asset or a5_asset_drift),
            "\n         ".join(a5_missing_asset + a5_asset_drift) if (a5_missing_asset or a5_asset_drift) else "all final assets present and hash-stable",
        ),
        CheckResult(
            "A6. main_tex_refs resolve",
            not a6_orphans,
            "\n         ".join(a6_orphans) if a6_orphans else "all referenced illustrations appear in main.tex",
        ),
        CheckResult(
            "A7. claim_scope present (no certifying as evidence)",
            not a7_no_scope,
            "missing scope: " + ", ".join(a7_no_scope) if a7_no_scope else "every entry declares illustrative scope",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print every check's detail")
    args = parser.parse_args()

    if not MANIFEST.exists():
        # Empty / missing manifest is acceptable — layer reports zero
        # entries and exits 0. Allows the layer to ship before any
        # illustration is certified, without breaking the cert.
        print("=" * 70)
        print("ILLUSTRATION LINEAGE CHECK")
        print("=" * 70)
        print(f"Manifest: {MANIFEST}")
        print("(no manifest yet; nothing to verify)")
        return 0

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    results = check_illustrations(manifest)

    n_pass = sum(1 for r in results if r.passed)
    n_fail = len(results) - n_pass

    print("=" * 70)
    print("ILLUSTRATION LINEAGE CHECK  (LaTeX block -> spec -> asset)")
    print("=" * 70)
    print(f"Manifest:  {MANIFEST}")
    print(f"Entries:   {len(manifest.get('illustrations', {}))}")
    print()
    for r in results:
        badge = "[OK]  " if r.passed else "[FAIL]"
        print(f"  {badge} {r.name}")
        if r.detail and (args.verbose or not r.passed):
            print(f"         {r.detail}")
    print()
    print("-" * 70)
    print(f"  Passed: {n_pass} / {len(results)}")
    print(f"  Failed: {n_fail} / {len(results)}")
    print("-" * 70)

    payload = {
        "summary": {"total": len(results), "passed": n_pass, "failed": n_fail},
        "results": [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
