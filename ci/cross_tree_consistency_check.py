#!/usr/bin/env python3
"""
cross_tree_consistency_check.py - Detect drift between recovered NeurIPS
files and their ICML originals.

Layer 13 of the certification stack. Earlier in the project's history,
~24 files were recovered from the ICML 2026 template tree into the
NeurIPS tree (commits da2fb61, d2bad83, 8c87ac2, c6e2f4d). The
existing certificate confirms each recovered file *exists* in the
NeurIPS tree. It does NOT confirm the file still *matches* its ICML
original — silent corruption, partial overwrite, or unintentional
edits could leave the NeurIPS-side copy out of sync with the upstream
archive.

L13 closes that gap. For each declared pair (NeurIPS path <-> ICML
path), SHA-256 hash both sides and compare:

  MATCH               content identical
  DIVERGED            content differs (record line counts on each side)
  MISSING_NEURIPS     file gone from NeurIPS  (FAIL)
  MISSING_ICML        file gone from ICML upstream archive (rare; FAIL)

Some files were intentionally edited post-recovery (e.g.
GRADED_METRICS_SPEC.md was relabelled "ICML 2026" -> "NeurIPS 2026"
in transit). These are declared in the manifest with
expected_divergence: True and a reason string. They count toward
PASS even when divergent — the divergence is documented.

Out of scope: recursive directory diffing. The manifest is hand-curated
to the load-bearing recovered files; broad tree comparison would
swamp signal with noise (cache directories, build artifacts,
intentionally-different submission packaging).

Exit codes
----------
  0  every pair MATCH or expected_divergence
  1  any pair DIVERGED unexpectedly, or any MISSING_*
  2  ICML root directory not found
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
NEURIPS_ROOT = SCRIPT_DIR.parent
ICML_ROOT = Path(r"C:\src\ICML_2026_Template")
RESULTS_JSON = SCRIPT_DIR / "cross_tree_consistency_results.json"

STATUS_MATCH = "MATCH"
STATUS_DIVERGED = "DIVERGED"
STATUS_MISSING_NEURIPS = "MISSING_NEURIPS"
STATUS_MISSING_ICML = "MISSING_ICML"


# Hand-curated manifest of recovered files. Each entry's `path` is
# interpreted relative to BOTH tree roots — NeurIPS and ICML use the
# same internal layout for these artifacts.
#
# Sources for the recovery commits:
#   da2fb61 — 5 ICML rebuttal evidence JSONs
#   d2bad83 — compatibility-analysis evidence + supplementary/bridges/README.md
#   8c87ac2 — in_the_wild_compound.py + compatibility_certificates.py + GRADED_METRICS_SPEC.md
#   c6e2f4d — registry-side documentation only (no file recovery; not seeded here)
MANIFEST: list[dict] = [
    # da2fb61: rebuttal evidence JSONs
    {"path": "rebuttal/figures/cross_model_results.json"},
    {"path": "rebuttal/figures/gram_eigendecomposition_results.json"},
    {"path": "rebuttal/figures/per_task_correlation_results.json"},
    {"path": "rebuttal/figures/proxy_ablation_results.json"},
    {"path": "rebuttal/figures/soft_constraint_results.json"},
    {"path": "rebuttal/figures/unconditional_pivot_results.json"},
    # 8c87ac2: load-bearing experiment scripts
    {"path": "supplementary/experiments/in_the_wild_compound.py"},
    {"path": "supplementary/experiments/compatibility_certificates.py"},
    # d2bad83: compatibility-certificate evidence + bridges README
    {"path": "supplementary/experiments/outputs/compatibility_analysis/certificates.txt"},
    {"path": "supplementary/experiments/outputs/compatibility_analysis/compatibility_table.tex"},
    {"path": "supplementary/bridges/README.md"},
    # 8c87ac2: spec doc — relabelled ICML -> NeurIPS in transit
    {
        "path": "supplementary/GRADED_METRICS_SPEC.md",
        "expected_divergence": True,
        "reason": "ICML 2026 -> NeurIPS 2026 relabel applied post-recovery",
    },
]


@dataclass
class PairReport:
    path: str
    neurips_path: str
    icml_path: str
    status: str
    expected_divergence: bool = False
    reason: str | None = None
    neurips_sha256: str | None = None
    icml_sha256: str | None = None
    neurips_lines: int | None = None
    icml_lines: int | None = None

    @property
    def is_ok(self) -> bool:
        # An OK pair is either a literal MATCH or a DIVERGED that was
        # expected. Anything else (unexpected DIVERGED, MISSING_*) is
        # a real signal.
        if self.status == STATUS_MATCH:
            return True
        if self.status == STATUS_DIVERGED and self.expected_divergence:
            return True
        return False

    @property
    def display_status(self) -> str:
        if self.status == STATUS_DIVERGED and self.expected_divergence:
            return "MATCH (expected divergence)"
        return self.status

    def to_dict(self) -> dict:
        d = asdict(self)
        d["display_status"] = self.display_status
        d["is_ok"] = self.is_ok
        return d


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def line_count(path: Path) -> int:
    # Used only for display when files diverge; counts physical lines
    # on a best-effort basis (binary files would still be counted by
    # newline bytes, but every file in the manifest is text).
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return -1


def check_pair(entry: dict) -> PairReport:
    rel = entry["path"]
    expected = bool(entry.get("expected_divergence", False))
    reason = entry.get("reason")
    neurips_path = NEURIPS_ROOT / rel
    icml_path = ICML_ROOT / rel

    r = PairReport(
        path=rel,
        neurips_path=str(neurips_path),
        icml_path=str(icml_path),
        status="",
        expected_divergence=expected,
        reason=reason,
    )

    n_exists = neurips_path.is_file()
    i_exists = icml_path.is_file()

    if not n_exists and not i_exists:
        r.status = STATUS_MISSING_NEURIPS  # treat dual-missing as NeurIPS-side gap (paper tree)
        return r
    if not n_exists:
        r.status = STATUS_MISSING_NEURIPS
        r.icml_sha256 = sha256_of(icml_path)
        r.icml_lines = line_count(icml_path)
        return r
    if not i_exists:
        r.status = STATUS_MISSING_ICML
        r.neurips_sha256 = sha256_of(neurips_path)
        r.neurips_lines = line_count(neurips_path)
        return r

    n_hash = sha256_of(neurips_path)
    i_hash = sha256_of(icml_path)
    r.neurips_sha256 = n_hash
    r.icml_sha256 = i_hash

    if n_hash == i_hash:
        r.status = STATUS_MATCH
    else:
        r.status = STATUS_DIVERGED
        r.neurips_lines = line_count(neurips_path)
        r.icml_lines = line_count(icml_path)

    return r


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="print every pair's status (default: only divergences + summary)",
    )
    args = parser.parse_args()

    if not ICML_ROOT.is_dir():
        print(f"ERROR: ICML root not found at {ICML_ROOT}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("CROSS-TREE CONSISTENCY CHECK  (NeurIPS <-> ICML recovered files)")
    print("=" * 70)
    print(f"NeurIPS root:  {NEURIPS_ROOT}")
    print(f"ICML root:     {ICML_ROOT}")
    print(f"Pairs:         {len(MANIFEST)}")
    print()

    reports: list[PairReport] = [check_pair(e) for e in MANIFEST]

    n_match = sum(1 for r in reports if r.status == STATUS_MATCH)
    n_diverged_expected = sum(1 for r in reports if r.status == STATUS_DIVERGED and r.expected_divergence)
    n_diverged_unexpected = sum(1 for r in reports if r.status == STATUS_DIVERGED and not r.expected_divergence)
    n_missing_neurips = sum(1 for r in reports if r.status == STATUS_MISSING_NEURIPS)
    n_missing_icml = sum(1 for r in reports if r.status == STATUS_MISSING_ICML)
    n_ok = sum(1 for r in reports if r.is_ok)
    n_fail = len(reports) - n_ok

    for r in reports:
        if args.verbose or not r.is_ok:
            badge = "[OK]  " if r.is_ok else "[FAIL]"
            print(f"  {badge} {r.display_status}: {r.path}")
            if r.status == STATUS_DIVERGED:
                print(f"         NeurIPS sha: {r.neurips_sha256}")
                print(f"         ICML    sha: {r.icml_sha256}")
                print(f"         lines NeurIPS={r.neurips_lines}  ICML={r.icml_lines}")
                if r.reason:
                    print(f"         reason: {r.reason}")
            elif r.status == STATUS_MISSING_NEURIPS:
                print(f"         NeurIPS path missing: {r.neurips_path}")
            elif r.status == STATUS_MISSING_ICML:
                print(f"         ICML    path missing: {r.icml_path}")

    print()
    print("-" * 70)
    print(f"  MATCH:                     {n_match:>3} / {len(reports)}")
    print(f"  DIVERGED (expected):       {n_diverged_expected:>3} / {len(reports)}")
    print(f"  DIVERGED (UNEXPECTED):     {n_diverged_unexpected:>3} / {len(reports)}")
    print(f"  MISSING_NEURIPS:           {n_missing_neurips:>3} / {len(reports)}")
    print(f"  MISSING_ICML:              {n_missing_icml:>3} / {len(reports)}")
    print(f"  -> OK:                     {n_ok:>3} / {len(reports)}")
    print(f"  -> FAIL:                   {n_fail:>3} / {len(reports)}")
    print("-" * 70)

    payload = {
        "summary": {
            "total_pairs": len(reports),
            "match": n_match,
            "diverged_expected": n_diverged_expected,
            "diverged_unexpected": n_diverged_unexpected,
            "missing_neurips": n_missing_neurips,
            "missing_icml": n_missing_icml,
            "ok": n_ok,
            "fail": n_fail,
        },
        "neurips_root": str(NEURIPS_ROOT),
        "icml_root": str(ICML_ROOT),
        "pairs": [r.to_dict() for r in reports],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
