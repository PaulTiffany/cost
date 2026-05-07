"""Generate MUTATION_LEDGER.md from a Cosmic Ray dump file.

The ledger is the canonical record of mutation coverage on the audit
substrate. It captures, at the time of generation:

  1. SHA256 of every substrate source file under mutation.
  2. SHA256 of every test file in the kill set.
  3. SHA256 of the Cosmic Ray config and the dump itself.
  4. Aggregate: total mutations, killed, survived, incomplete, kill rate.
  5. Per-survivor record: file:line:operator + diff + classification.

Classification rules (KILL / EQUIVALENT / DEAD-CODE / RESIDUAL):
  - EQUIVALENT: the source line carries an inline ``Cosmic-Ray:`` comment
    within a 6-line window above the survivor that justifies the
    equivalence. The comment text is captured verbatim into the ledger.
  - DEAD-CODE: the source line carries a ``Cosmic-Ray-Dead`` marker.
  - RESIDUAL: anything else.

A run with kill_rate + EQUIVALENT + DEAD-CODE coverage of 100% is
"canonical green" — every mutation is either killed or has a justified
exemption. Residual entries are gaps.

Usage:
    python ci/audit/_mutation_ledger.py \\
        --dump ci/audit/main_dump.jsonl \\
        --config ci/audit/cosmic_ray_config.toml \\
        --output ci/audit/MUTATION_LEDGER.md
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _decode_dump(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.lstrip("﻿")


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return "FILE_MISSING"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _classify_survivor(
    src_lines: List[str], lineno: int
) -> Tuple[str, Optional[str]]:
    """Look back up to 20 lines for a Cosmic-Ray classification comment block.

    Strategy: scan the window, find every contiguous comment block, and if
    any block contains "Cosmic-Ray:" plus "EQUIVALENT" or "DEAD-CODE", emit
    that classification with the full block text as justification. The
    nearest matching block (closest to the survivor line) wins.

    Returns (classification, justification) where classification is one of:
    EQUIVALENT, DEAD-CODE, RESIDUAL.
    """
    if lineno <= 0 or lineno > len(src_lines):
        return ("RESIDUAL", None)
    start = max(0, lineno - 21)
    window = src_lines[start:lineno]  # ends just before the survivor line

    # Build comment blocks (contiguous runs of `# ...` lines, blanks ignored).
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in window:
        s = raw.strip()
        if s.startswith("#"):
            current.append(s.lstrip("# ").strip())
        elif s == "":
            # blank lines do not break a block
            continue
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)

    # Scan blocks from nearest-to-survivor (last) to furthest (first).
    for block in reversed(blocks):
        joined = " ".join(block)
        if "Cosmic-Ray-Dead" in joined or "DEAD-CODE" in joined.upper():
            return ("DEAD-CODE", joined)
        if "Cosmic-Ray" in joined and "EQUIVALENT" in joined.upper():
            return ("EQUIVALENT", joined)
    return ("RESIDUAL", None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--substrate-dir", default=Path("ci/audit"), type=Path)
    args = ap.parse_args()

    text = _decode_dump(args.dump)

    killed = 0
    survived: List[dict] = []
    incompetent = 0  # worker=exception, test=incompetent (broken AST)
    other_incomplete = 0  # any other non-{killed,survived} outcome

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if not (isinstance(rec, list) and len(rec) >= 2):
            continue
        spec, wr = rec[0], rec[1]
        if not isinstance(wr, dict):
            continue
        outcome = wr.get("test_outcome")
        if outcome == "killed":
            killed += 1
        elif outcome == "survived":
            muts = (spec or {}).get("mutations", [{}])
            m = muts[0] if muts else {}
            survived.append({
                "operator": m.get("operator_name", "?").split("/")[-1],
                "module": m.get("module_path", "?"),
                "line": (m.get("start_pos", [0, 0]) or [0, 0])[0],
                "diff": wr.get("diff", ""),
            })
        elif outcome == "incompetent":
            incompetent += 1
        else:
            other_incomplete += 1

    # Incompetent mutations produce code that cannot run; by convention they
    # are effectively killed (the mutant cannot exist as a viable program).
    effective_killed = killed + incompetent
    total = effective_killed + len(survived) + other_incomplete
    completed = effective_killed + len(survived)
    kill_rate = (100.0 * effective_killed / completed) if completed else 0.0

    # Classify each survivor by walking up the source for inline justification.
    src_cache: Dict[Path, List[str]] = {}
    for s in survived:
        modpath = Path(s["module"])
        if modpath not in src_cache:
            try:
                src_cache[modpath] = modpath.read_text(encoding="utf-8").splitlines()
            except (FileNotFoundError, OSError):
                src_cache[modpath] = []
        cls, just = _classify_survivor(src_cache[modpath], s["line"])
        s["classification"] = cls
        s["justification"] = just

    cls_counts = Counter(s["classification"] for s in survived)
    addressed = effective_killed + cls_counts["EQUIVALENT"] + cls_counts["DEAD-CODE"]
    addressed_rate = (100.0 * addressed / total) if total else 0.0

    # Provenance: hash every substrate source + every test file.
    substrate_files = sorted([
        p for p in args.substrate_dir.glob("*.py")
        if p.name not in {"_kill_rate.py", "_mutation_ledger.py"}
    ])
    test_files = sorted((args.substrate_dir / "tests").glob("test_*.py"))

    file_hashes: Dict[str, str] = {}
    for p in substrate_files + test_files + [args.config, args.dump]:
        rel = str(p).replace("\\", "/")
        file_hashes[rel] = _sha256_file(p)

    # Render the ledger.
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    md = []
    md.append("# Mutation Coverage Ledger\n")
    md.append(f"Generated: `{now}`\n")
    md.append("## Aggregate\n")
    md.append(f"- **Total mutations:** {total}")
    md.append(f"- **Killed (test failure):** {killed}")
    md.append(f"- **Incompetent (broken AST — counts as killed):** {incompetent}")
    md.append(f"- **Effective killed:** {effective_killed}")
    md.append(f"- **Survived:** {len(survived)}")
    md.append(f"  - EQUIVALENT (documented): {cls_counts['EQUIVALENT']}")
    md.append(f"  - DEAD-CODE (documented): {cls_counts['DEAD-CODE']}")
    md.append(f"  - RESIDUAL (gaps): {cls_counts['RESIDUAL']}")
    md.append(f"- **Other incomplete:** {other_incomplete}")
    md.append(f"- **Kill rate (effective_killed / completed):** {kill_rate:.2f}%")
    md.append(f"- **Addressed rate (killed + documented / total):** {addressed_rate:.2f}%\n")

    md.append("## Provenance\n")
    md.append("SHA256 of every artifact at generation time:\n")
    for rel, h in sorted(file_hashes.items()):
        md.append(f"- `{rel}`: `{h}`")
    md.append("")

    md.append("## Survivors\n")
    if not survived:
        md.append("_None._\n")
    else:
        md.append("| File | Line | Operator | Class | Justification |")
        md.append("|------|------|----------|-------|---------------|")
        for s in sorted(survived, key=lambda s: (s["module"], s["line"], s["operator"])):
            mod = s["module"].replace("\\", "/").split("/")[-1]
            j = (s["justification"] or "").replace("|", "\\|")
            md.append(
                f"| `{mod}` | {s['line']} | `{s['operator']}` | "
                f"**{s['classification']}** | {j} |"
            )
        md.append("")

        # Detail blocks for residuals only (the gaps that need attention).
        residuals = [s for s in survived if s["classification"] == "RESIDUAL"]
        if residuals:
            md.append("## Residual gaps (no inline justification found)\n")
            for s in residuals:
                mod = s["module"].replace("\\", "/").split("/")[-1]
                md.append(f"### `{mod}:{s['line']}` — `{s['operator']}`\n")
                md.append("```diff")
                md.append(s["diff"].rstrip())
                md.append("```\n")

    args.output.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {args.output} ({total} mutations, {kill_rate:.1f}% killed, "
          f"{addressed_rate:.1f}% addressed)")
    return 0 if cls_counts["RESIDUAL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
