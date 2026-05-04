#!/usr/bin/env python3
"""
One-shot helper that edits ci/claim_data_ties.json in place to attach a
`metadata.cross_source_peers` field to a small number of claims that recompute
the same underlying quantity from different source JSONs.

Idempotent: running it twice leaves the manifest unchanged.

Configured pair:
  constitution_n_principles  (n_principles=20, from constitution_analysis.json)
  gram_n_eigenvalues         (n=20,           from gram_eigendecomposition_results.json)

Both encode "number of Constitution principles". They were chosen because
they read different fields from different JSON files but should always agree.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "claim_data_ties.json"

PEERS = {
    "constitution_n_principles": ["gram_n_eigenvalues"],
    "gram_n_eigenvalues": ["constitution_n_principles"],
}


def _ensure_peers(claim: dict, peers: list[str]) -> bool:
    meta = claim.get("metadata")
    if meta is None:
        meta = {}
        claim["metadata"] = meta
    existing = list(meta.get("cross_source_peers") or [])
    merged = sorted(set(existing) | set(peers))
    if merged == sorted(existing):
        return False
    meta["cross_source_peers"] = merged
    return True


def main() -> int:
    with MANIFEST.open(encoding="utf-8") as fh:
        manifest = json.load(fh)
    claims = manifest.get("claims", {})

    changed = False
    for claim_id, peers in PEERS.items():
        if claim_id not in claims:
            print(f"WARN: claim {claim_id!r} not present in manifest; skipping")
            continue
        if _ensure_peers(claims[claim_id], peers):
            print(f"updated {claim_id} cross_source_peers -> {peers}")
            changed = True
        else:
            print(f"unchanged: {claim_id} already has expected peers")

    if changed:
        with MANIFEST.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")
        print("manifest written")
    else:
        print("no changes; manifest left untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
