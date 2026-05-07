"""Print kill-rate from a Cosmic Ray dump file.

Usage:
    cosmic-ray dump <session.sqlite> > dump.jsonl
    python ci/audit/_kill_rate.py dump.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main(path: str) -> int:
    killed = 0
    survived = 0
    other = 0
    survivor_ops: Counter[str] = Counter()
    survivor_locs: list[tuple[str, int, str]] = []

    raw = Path(path).read_bytes()
    # PowerShell '>' produces UTF-16 LE with BOM; auto-detect.
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        text = raw.decode("utf-8", errors="replace")
    # Strip leading U+FEFF that survived decoding (BOM as character).
    text = text.lstrip("﻿")

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
            survived += 1
            muts = (spec or {}).get("mutations", [{}])
            m = muts[0] if muts else {}
            op = m.get("operator_name", "?").split("/")[-1]
            mod = (m.get("module_path", "?")).split("\\")[-1].split("/")[-1]
            lineno = (m.get("start_pos", [0, 0]) or [0, 0])[0]
            survivor_ops[op] += 1
            survivor_locs.append((mod, lineno, op))
        else:
            other += 1

    completed = killed + survived
    rate = (100.0 * killed / completed) if completed else 0.0
    print(f"killed={killed}  survived={survived}  incomplete={other}")
    print(f"kill_rate={rate:.1f}% (of completed)")
    if survivor_ops:
        print()
        print("Survivors by operator:")
        for op, n in survivor_ops.most_common():
            print(f"  {op}: {n}")
        print()
        print("All survivors (file:line:op):")
        for loc in sorted(survivor_locs):
            print(f"  {loc[0]}:{loc[1]}:{loc[2]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python _kill_rate.py <dump.jsonl>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
