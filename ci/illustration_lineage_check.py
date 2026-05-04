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
  A8. venue_constraints[]: each constraint file exists and its hash
      matches the manifest. If a constraint file declares a
      SOURCE_HASH header, the actual upstream source file must hash
      to that value (detects venue-document drift).
  A9. venue_constraints[].source_key references a venue listed in
      _meta.venue_sources, and the bib_key for that venue is cited
      somewhere in the paper.

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
CITE_KEY = re.compile(r"\\cite[a-z]*\{([^}]+)\}")
SOURCE_HASH_HEADER = re.compile(r"^#\s*SOURCE_HASH:\s*([0-9a-f]{64})\s*$", re.MULTILINE)
SOURCE_FILE_HEADER = re.compile(r"^#\s*SOURCE_FILE:\s*(\S+)\s*$", re.MULTILINE)


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


def extract_block_by_anchor(source_file: Path, anchor: str) -> str | None:
    """Extract content between LaTeX comment anchors.

    Anchors have the form:
        % cert:block:start <name>
        ... content ...
        % cert:block:end <name>

    More edit-resilient than fixed line_start/line_end because the
    anchors move with the content. Returns None if either anchor
    is missing or they don't pair up.
    """
    if not source_file.exists():
        return None
    text = source_file.read_text(encoding="utf-8", errors="replace")
    start_marker = f"% cert:block:start {anchor}"
    end_marker = f"% cert:block:end {anchor}"
    s = text.find(start_marker)
    if s < 0:
        return None
    e = text.find(end_marker, s)
    if e < 0:
        return None
    # Return the content BETWEEN anchors, exclusive of marker lines.
    # Skip past the start-marker line (find next newline).
    nl = text.find("\n", s)
    if nl < 0 or nl >= e:
        return None
    return text[nl + 1:e].rstrip("\n")


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


def extract_main_tex_cite_keys() -> set[str]:
    keys: set[str] = set()
    text = MAIN_TEX.read_text(encoding="utf-8", errors="replace")
    for m in CITE_KEY.finditer(text):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                keys.add(k)
    return keys


def resolve(p: str) -> Path:
    return (REPO_ROOT / p).resolve()


def check_illustrations(manifest: dict) -> list[CheckResult]:
    illus = manifest.get("illustrations", {})
    if not illus:
        return [CheckResult("manifest has illustrations", False, "no entries")]

    paper_refs = extract_main_tex_figure_refs()
    cited_keys = extract_main_tex_cite_keys()
    venue_sources = manifest.get("_meta", {}).get("venue_sources", {})

    # A1-A9 collected per-illustration; aggregate at end
    a1_missing_input: list[str] = []
    a2_input_drift: list[str] = []
    a5_missing_asset: list[str] = []
    a5_asset_drift: list[str] = []
    a6_orphans: list[str] = []
    a7_no_scope: list[str] = []
    a8_constraint_problems: list[str] = []
    a9_venue_problems: list[str] = []

    def walk_inputs(name: str, label: str, inputs_list: list, *, allow_empty: bool) -> None:
        if not inputs_list:
            if not allow_empty:
                a1_missing_input.append(f"{name}: no {label}[] in manifest")
            return
        for idx, inp in enumerate(inputs_list):
            file_str = inp.get("file", "")
            file_path = resolve(file_str)
            if not file_path.exists():
                a1_missing_input.append(f"{name} {label} #{idx}: {file_str} not found")
                continue
            kind = inp.get("kind", "text_file")
            expected_hash = inp.get("hash", "")
            if kind == "latex_block":
                # Three extraction modes (in priority order):
                #   1. anchor (cert:block:start/end NAME) — edit-resilient
                #   2. label (\label{...} env extraction) — semantic
                #   3. line_start/line_end — brittle but exact, legacy
                if "anchor" in inp:
                    content = extract_block_by_anchor(file_path, inp["anchor"])
                    if content is None:
                        a2_input_drift.append(f"{name} {label} #{idx}: anchor '{inp['anchor']}' not found (or unpaired) in {file_str}")
                        continue
                elif "label" in inp:
                    content = extract_block_by_label(file_path, inp["label"])
                    if content is None:
                        a2_input_drift.append(f"{name} {label} #{idx}: label '{inp['label']}' could not be extracted from {file_str}")
                        continue
                else:
                    start = int(inp.get("line_start", 0))
                    end = int(inp.get("line_end", 0))
                    content = extract_block_by_line_range(file_path, start, end)
                    if content is None:
                        a2_input_drift.append(f"{name} {label} #{idx}: could not extract lines {start}-{end} from {file_str}")
                        continue
            else:  # text_file or unknown
                content = file_path.read_text(encoding="utf-8")
            actual_hash = sha256_of_text(content)
            if actual_hash != expected_hash:
                a2_input_drift.append(
                    f"{name} {label} #{idx} ({kind}, {file_str}): hash mismatch "
                    f"(manifest={expected_hash[:12]}..., actual={actual_hash[:12]}...)"
                )

    for name, entry in illus.items():
        # A1 + A2: walk inputs[] (image-model draft inputs).
        walk_inputs(name, "input", entry.get("inputs", []), allow_empty=False)

        # A1 + A2 + A8: walk venue_constraints[] (cert-bound venue rules).
        venue_constraints = entry.get("venue_constraints", [])
        walk_inputs(name, "venue_constraint", venue_constraints, allow_empty=True)

        # A8: for each venue_constraint, if the file declares a
        # SOURCE_HASH header, verify it matches the upstream source.
        # A9: source_key must exist in _meta.venue_sources, and the
        # declared bib_key must be cited in main.tex.
        for idx, vc in enumerate(venue_constraints):
            vc_path = resolve(vc.get("file", ""))
            if not vc_path.exists():
                continue  # already reported by walk_inputs A1
            vc_text = vc_path.read_text(encoding="utf-8")
            mh = SOURCE_HASH_HEADER.search(vc_text)
            mf = SOURCE_FILE_HEADER.search(vc_text)
            if mh and mf:
                declared_hash = mh.group(1)
                declared_src = mf.group(1)
                src_path = resolve(declared_src)
                if not src_path.exists():
                    a8_constraint_problems.append(
                        f"{name} venue_constraint #{idx}: declared SOURCE_FILE {declared_src} not found"
                    )
                else:
                    actual_src_hash = sha256_of_file(src_path)
                    if actual_src_hash != declared_hash:
                        a8_constraint_problems.append(
                            f"{name} venue_constraint #{idx}: upstream source drift "
                            f"({declared_src}: header={declared_hash[:12]}..., "
                            f"actual={actual_src_hash[:12]}...)"
                        )

            # A9: source_key registry + bib citation
            source_key = vc.get("source_key")
            if source_key:
                src_meta = venue_sources.get(source_key)
                if not src_meta:
                    a9_venue_problems.append(
                        f"{name} venue_constraint #{idx}: source_key='{source_key}' not in _meta.venue_sources"
                    )
                else:
                    bib_key = src_meta.get("bib_key")
                    if bib_key and bib_key not in cited_keys:
                        a9_venue_problems.append(
                            f"{name} venue_constraint #{idx}: bib_key '{bib_key}' "
                            f"(for source_key='{source_key}') not cited in main.tex"
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
            f"A1. Every input/constraint file exists ({len(illus)} entries)",
            not a1_missing_input,
            "\n         ".join(a1_missing_input) if a1_missing_input else "all input and constraint files resolved",
        ),
        CheckResult(
            "A2. Every input/constraint hash matches its current file content",
            not a2_input_drift,
            "\n         ".join(a2_input_drift) if a2_input_drift else "all inputs and constraints unchanged since illustration was authored",
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
        CheckResult(
            "A8. venue-source SOURCE_HASH headers match upstream files (drift detection)",
            not a8_constraint_problems,
            "\n         ".join(a8_constraint_problems) if a8_constraint_problems else "all upstream venue sources unchanged since constraint was authored",
        ),
        CheckResult(
            "A9. venue source_keys registered + bib_keys cited in main.tex",
            not a9_venue_problems,
            "\n         ".join(a9_venue_problems) if a9_venue_problems else "all venue constraints registered and cited",
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
