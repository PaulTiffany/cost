#!/usr/bin/env python3
"""
refresh_pricing.py

Refreshes the OpenRouter section of ci/model_pricing.json from the live
OpenRouter API. Anthropic-direct prices are not pulled (no public API);
they are hand-edited and dated in the JSON.

Usage:
    python ci/refresh_pricing.py
    python ci/refresh_pricing.py --dry-run     # show what would change
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

PRICING_PATH = Path(__file__).resolve().parent / "model_pricing.json"
OPENROUTER_API = "https://openrouter.ai/api/v1/models"


def fetch_openrouter() -> dict:
    """Fetch the OpenRouter models list. Returns dict mapping model_id ->
    {input, output} prices in USD per million tokens.

    OpenRouter returns prices as USD per token; we multiply by 1e6.
    """
    req = urllib.request.Request(OPENROUTER_API, headers={"User-Agent": "neurips-cost-refresh/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    out: dict[str, dict] = {}
    for m in data.get("data", []):
        mid = m.get("id")
        pricing = m.get("pricing", {})
        try:
            inp = float(pricing.get("prompt", 0)) * 1_000_000.0
            outp = float(pricing.get("completion", 0)) * 1_000_000.0
        except (TypeError, ValueError):
            continue
        if mid and (inp > 0 or outp > 0):
            out[mid] = {"input": round(inp, 4), "output": round(outp, 4)}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change without writing")
    parser.add_argument("--keep-only-known", action="store_true",
                        help="restrict refresh to model IDs already in the pricing JSON (avoids 100+ unrelated entries)")
    args = parser.parse_args()

    if not PRICING_PATH.exists():
        print(f"Pricing file not found: {PRICING_PATH}", file=sys.stderr)
        return 1
    current = json.loads(PRICING_PATH.read_text(encoding="utf-8"))

    print(f"Fetching {OPENROUTER_API} ...", flush=True)
    try:
        live = fetch_openrouter()
    except Exception as e:
        print(f"Refresh failed: {e}", file=sys.stderr)
        return 1
    print(f"  retrieved {len(live)} models", flush=True)

    or_block = current.setdefault("openrouter", {})
    or_models = or_block.setdefault("models", {})
    known_ids = set(or_models.keys()) if args.keep_only_known else None

    changes = {"added": [], "updated": [], "unchanged": [], "removed": [], "skipped": 0}
    for mid, prices in live.items():
        if known_ids is not None and mid not in known_ids:
            changes["skipped"] += 1
            continue
        prev = or_models.get(mid)
        if prev is None:
            changes["added"].append(mid)
            or_models[mid] = prices
        elif (prev.get("input"), prev.get("output")) != (prices["input"], prices["output"]):
            changes["updated"].append({"model": mid,
                                        "from": (prev.get("input"), prev.get("output")),
                                        "to":   (prices["input"], prices["output"])})
            or_models[mid] = {**prev, **prices}
        else:
            changes["unchanged"].append(mid)

    if args.keep_only_known:
        for mid in list(or_models.keys()):
            if mid not in live:
                changes["removed"].append(mid)

    current["_meta"]["last_refreshed_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    or_block["_last_refreshed_iso"] = current["_meta"]["last_refreshed_iso"]

    print(f"  added:     {len(changes['added'])}")
    print(f"  updated:   {len(changes['updated'])}")
    print(f"  unchanged: {len(changes['unchanged'])}")
    print(f"  removed:   {len(changes['removed'])}")
    print(f"  skipped:   {changes['skipped']} (not in pricing JSON; --keep-only-known)")
    if changes["updated"]:
        print()
        print("Updated entries:")
        for u in changes["updated"][:10]:
            print(f"  {u['model']}: input {u['from'][0]}->{u['to'][0]}, output {u['from'][1]}->{u['to'][1]}")

    if args.dry_run:
        print("\n(dry run; no write)")
        return 0
    PRICING_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    print(f"\nWrote {PRICING_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
