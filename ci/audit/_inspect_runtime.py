"""Quick inspection of audit_runtime_results.json — categorizes cells."""
import json
from collections import Counter

with open("ci/audit_runtime_results.json", encoding="utf-8") as f:
    r = json.load(f)

print("Top-level keys:", sorted(r.keys()))
print()

# Stream decision section
sd = r.get("stream_decision") or r.get("audit_streams") or {}
print(f"stream_decision: {sd if isinstance(sd, str) else type(sd).__name__}")

# Per-observer headlines
for obs in ("B_claude", "B_openweight"):
    h = r.get(obs) or r.get(f"per_observer_{obs}") or {}
    if h:
        print(f"\n{obs}:")
        for k, v in h.items():
            if isinstance(v, list):
                print(f"  {k}: [{len(v)} items]")
            else:
                print(f"  {k}: {v}")

# Cells (if present)
cells = r.get("cells") or r.get("audit_results") or []
if cells:
    classes = Counter(c.get("relation_class") for c in cells)
    print(f"\nCells ({len(cells)}):")
    for cls, n in classes.most_common():
        print(f"  {cls}: {n}")
    print("\nFirst 3 INSUFFICIENT reasons:")
    insuf = [c for c in cells if c.get("relation_class") == "INSUFFICIENT_OBSERVABILITY"]
    for c in insuf[:3]:
        reason = c.get("reason", "")
        print(f"  {c.get('task')}/{c.get('tier')}: {reason[:90]}")

# H_B verdicts
for hid in ("H_B1", "H_B2", "H_B3"):
    v = r.get(hid)
    if v:
        print(f"\n{hid}: {v.get('verdict')}")
        ev = v.get("evidence", {})
        if "per_observer" in ev:
            print(f"  per_observer: {ev['per_observer']}")
        if "reason" in ev:
            print(f"  reason: {ev['reason']}")
