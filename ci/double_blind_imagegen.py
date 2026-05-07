"""Mechanistic double-blind sender to GPT-image-1 via OpenRouter.

Loads both the historical and current paper-mutation static-analysis
results, mechanistically double-blinds them by:

  1. Stripping `_meta` (timestamps, paths, script identity)
  2. Replacing dataset-of-origin labels with neutral identifiers (A/B)
  3. Randomly assigning historical/current to A/B (seeded RNG; the seed
     is derived from a hash of both payloads, so the assignment is
     deterministic but cannot be predicted without the full payloads)
  4. Stripping per-mutation `rationale` strings (these contain cert-layer
     names that would identify the dataset to a model with prior context)
  5. Sorting categories alphabetically (no chronological hint)

The resulting anonymized payload is sent to OpenRouter's images/generations
endpoint targeting `openai/gpt-image-1`. The prompt asks for a comparison
visualization without revealing which dataset is "before" vs "after."

The double-blind unblinding key (which letter is which dataset) is
written separately to `ci/double_blind_key.json` so the operator can
reveal the mapping AFTER receiving the image. The key is NOT sent in the
request body.

Required environment:
    OPENROUTER_API_KEY  loaded from C:\\src\\cosmogenesis\\agi2026\\.env

Usage:
    python ci/double_blind_imagegen.py
    python ci/double_blind_imagegen.py --dry-run   # build prompt but skip API call
    python ci/double_blind_imagegen.py --model openai/gpt-image-1

Outputs:
    ci/double_blind_anonymized.json   the payload sent (no labels)
    ci/double_blind_key.json          the A/B -> historical/current map
    ci/double_blind_response.json     full API response
    ci/double_blind_image.png         decoded image (if base64 returned)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COSMIC_LAB = Path(r"C:\src\paper_cosmic_ray_lab")
HISTORICAL = COSMIC_LAB / "radiation_map_historical.json"
CURRENT = COSMIC_LAB / "radiation_map.json"
ENV_FILE = Path(r"C:\src\cosmogenesis\agi2026\.env")

OUT_ANONYMIZED = REPO / "ci" / "double_blind_anonymized.json"
OUT_KEY = REPO / "ci" / "double_blind_key.json"
OUT_RESPONSE = REPO / "ci" / "double_blind_response.json"
OUT_IMAGE = REPO / "ci" / "double_blind_image.png"

DEFAULT_MODEL = "openai/gpt-5.4-image-2"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"ERROR: env file not found at {path}")
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _load_payload(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _strip_for_blind(payload: dict) -> dict:
    """Strip identifying / chronological metadata from a mutation payload."""
    cleaned = {
        "summary": dict(payload["summary"]),
        "by_category": {},
        "mutations": [],
    }
    for cat in sorted(payload.get("by_category", {}).keys()):
        cleaned["by_category"][cat] = dict(payload["by_category"][cat])
    for m in payload.get("mutations", []):
        cleaned["mutations"].append({
            "category": m.get("category"),
            "killed_by": m.get("killed_by"),
            "survived": m.get("survived"),
            "in_paper": m.get("in_paper"),
        })
    cleaned["mutations"].sort(
        key=lambda m: (m.get("category") or "", str(m.get("killed_by") or ""),
                       str(m.get("survived"))),
    )
    return cleaned


def _seed_from_payloads(a: dict, b: dict) -> int:
    blob = json.dumps([a, b], sort_keys=True).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def _build_prompt(left: dict, right: dict, left_label: str, right_label: str) -> str:
    """Compose a request that does not reveal which dataset is which."""
    def cat_lines(d: dict) -> str:
        return "\n".join(
            f"    {cat}: killed={d['by_category'][cat].get('killed', 0)}, "
            f"survived={d['by_category'][cat].get('survived', 0)}"
            for cat in sorted(d["by_category"].keys())
        )

    return (
        f"Generate a single clean comparison chart for two paper-level mutation "
        f"static-analysis studies. Each study perturbs a research paper in "
        f"controlled ways and reports per-category kill counts (mutations the "
        f"verification chain caught) and survived counts (mutations that landed "
        f"undetected). The two studies are labelled neutrally as {left_label} and "
        f"{right_label}; do not assume an ordering or causality between them.\n\n"
        f"Dataset {left_label}:\n"
        f"  applicable: {left['summary']['applicable_mutations']}, "
        f"killed: {left['summary']['killed']}, "
        f"survived: {left['summary']['survived']}, "
        f"survival rate: {left['summary']['survival_rate']:.1%}\n"
        f"  per category:\n{cat_lines(left)}\n\n"
        f"Dataset {right_label}:\n"
        f"  applicable: {right['summary']['applicable_mutations']}, "
        f"killed: {right['summary']['killed']}, "
        f"survived: {right['summary']['survived']}, "
        f"survival rate: {right['summary']['survival_rate']:.1%}\n"
        f"  per category:\n{cat_lines(right)}\n\n"
        f"Render: side-by-side grouped bar chart, x-axis = mutation category, "
        f"y-axis = count, two bars per category (killed vs survived) for each "
        f"dataset. Use neutral colors. Title: 'Mutation kill/survive by category "
        f"({left_label} vs {right_label})'. Include legend. Render as a single "
        f"high-resolution PNG, white background, sans-serif labels."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Build prompt + anonymized payload but skip the API call")
    parser.add_argument("--size", default="1024x1024")
    args = parser.parse_args()

    historical = _load_payload(HISTORICAL)
    current = _load_payload(CURRENT)
    historical_clean = _strip_for_blind(historical)
    current_clean = _strip_for_blind(current)

    seed = _seed_from_payloads(historical_clean, current_clean)
    rng = random.Random(seed)
    assigned = [("historical", historical_clean), ("current", current_clean)]
    rng.shuffle(assigned)
    (left_origin, left_data), (right_origin, right_data) = assigned
    left_label, right_label = "A", "B"

    prompt = _build_prompt(left_data, right_data, left_label, right_label)

    anonymized_record = {
        "_meta": {
            "purpose": "Anonymized payload sent to image-gen for double-blind comparison.",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model_target": args.model,
            "seed": seed,
        },
        "left_label": left_label,
        "left_data": left_data,
        "right_label": right_label,
        "right_data": right_data,
        "prompt": prompt,
    }
    OUT_ANONYMIZED.write_text(json.dumps(anonymized_record, indent=2), encoding="utf-8")

    key_record = {
        "_meta": {
            "purpose": "Unblinding key: maps neutral A/B labels back to dataset origin.",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "warning": "Do NOT send this file to the image-gen model.",
        },
        "mapping": {
            left_label: left_origin,
            right_label: right_origin,
        },
        "seed": seed,
    }
    OUT_KEY.write_text(json.dumps(key_record, indent=2), encoding="utf-8")

    print("=" * 70)
    print("DOUBLE-BLIND IMAGE-GEN SENDER")
    print("=" * 70)
    print(f"  model:       {args.model}")
    print(f"  size:        {args.size}")
    print(f"  seed:        {seed}")
    print(f"  prompt size: {len(prompt)} chars")
    print(f"  anonymized:  {OUT_ANONYMIZED}")
    print(f"  key (local): {OUT_KEY}")

    if args.dry_run:
        print("\n  DRY-RUN: skipping API call. Review prompt and re-run without --dry-run.")
        return 0

    env = _load_env(ENV_FILE)
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not found in env file", file=sys.stderr)
        return 2

    body = {
        "model": args.model,
        "modalities": ["image", "text"],
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/anonymous/cacophony",
            "X-Title": "paper-mutation-double-blind",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=120)
        body_bytes = resp.read()
        status = resp.status
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        status = e.code
        try:
            err = json.loads(body_bytes)
        except Exception:
            err = {"raw": body_bytes.decode("utf-8", errors="replace")}
        print(f"  HTTP {status} from OpenRouter:")
        print(json.dumps(err, indent=2)[:2000])
        OUT_RESPONSE.write_text(json.dumps({"http_status": status, "error": err}, indent=2),
                                encoding="utf-8")
        return 1

    payload = json.loads(body_bytes)
    OUT_RESPONSE.write_text(json.dumps(
        {"http_status": status, "model": args.model, "response": payload},
        indent=2,
    ), encoding="utf-8")

    image_saved = False
    # Chat completions image responses appear under choices[].message.images
    # as a list of {type: "image_url", image_url: {url: "data:image/png;base64,..."}}.
    # We also fall back to scanning content blocks for image_url types.
    for choice in payload.get("choices", []) or []:
        message = choice.get("message", {}) or {}
        for img in (message.get("images") or []):
            url_obj = img.get("image_url") or {}
            url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
            if not url:
                continue
            if url.startswith("data:"):
                _, _, b64 = url.partition(",")
                OUT_IMAGE.write_bytes(base64.b64decode(b64))
                image_saved = True
                break
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    OUT_IMAGE.write_bytes(r.read())
                image_saved = True
                break
            except Exception as exc:
                print(f"  could not fetch image url: {exc}", file=sys.stderr)
        if image_saved:
            break
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("image_url", "image"):
                    url_obj = block.get("image_url") or block.get("source") or {}
                    url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
                    if not url:
                        continue
                    if url.startswith("data:"):
                        _, _, b64 = url.partition(",")
                        OUT_IMAGE.write_bytes(base64.b64decode(b64))
                        image_saved = True
                        break
        if image_saved:
            break

    print(f"  HTTP {status}")
    print(f"  response:    {OUT_RESPONSE}")
    if image_saved:
        print(f"  image:       {OUT_IMAGE}")
    else:
        print("  no image bytes returned; check response JSON")
    print(f"  Verdict: {'PASS' if status == 200 and image_saved else 'PARTIAL/FAIL'}")
    return 0 if status == 200 and image_saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
