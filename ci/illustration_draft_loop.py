#!/usr/bin/env python3
"""
illustration_draft_loop.py - Demonstrate the L14 framework's intended
draft-then-redraw workflow.

For a given source LaTeX block:
  1. Send the block to a TEXT model. Ask it to produce a visual_spec
     markdown describing the cleanest schematic. Save to disk.
  2. Send the visual_spec to an IMAGE model. Get a draft PNG. Save.
  3. Print hashes for both. The human (or Codex) then redraws the
     deterministic TikZ asset based on the draft direction. The
     manifest entry records all three hashes.

The certificate trusts only the deterministic redraw. The draft is
exploration; the spec is documentation; the asset is what ships.

Usage:
  export OPENROUTER_API_KEY=...
  python illustration_draft_loop.py \\
    --source-file paper/main.tex \\
    --start 96 --end 114 \\
    --target algorithm1_routing \\
    --text-model openai/gpt-4o \\
    --image-model openai/gpt-5.4-image-2
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: pip install openai", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
ILLUSTRATIONS_DIR = REPO_ROOT / "supplementary" / "illustrations"


SPEC_META_PROMPT = """You are helping author a clean schematic illustration of a formal LaTeX block from a research paper. The illustration is a *reviewer aid*: it must match what the source asserts in prose, not invent claims.

Read the source LaTeX block below. Output a markdown visual specification describing the cleanest schematic representation. Constraints:

- About 150 words total.
- Sections: TITLE, LAYOUT (top-level structure: nodes/boxes and connections), KEY ELEMENTS (what each node represents), EMPHASIS (what should be visually prominent), OMIT (what NOT to depict; common over-additions), CLAIM SCOPE (what the figure claims and does not claim).
- No invented numbers. Use the values present in the source block.
- Favor a horizontal dataflow OR a decision-tree shape, depending on what the source describes.
- Output ONLY the markdown spec. No preamble, no chat.

SOURCE LATEX BLOCK:
```latex
{source_block}
```
"""


IMAGE_PROMPT_TEMPLATE = """Generate a clean schematic diagram following this specification:

{spec}

Style: minimal, monochrome with one accent color, rounded rectangles for nodes, arrows for flow, sans-serif labels. The diagram should look like a publication-quality schematic from a peer-reviewed CS paper. White background. No artistic flourishes.
"""


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def extract_block(source_file: Path, start: int, end: int) -> str:
    lines = source_file.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[start - 1:end])


def call_text_model(client: OpenAI, model: str, prompt: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


def call_image_model(client: OpenAI, model: str, prompt: str) -> tuple[bytes | None, str]:
    """Return (image_bytes, response_text). Image models on OpenRouter
    return either an image URL or base64 in the message content."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        # OpenRouter's image gen uses the chat endpoint; output appears
        # in choices[0].message.content (text) or in an extra field.
        modalities=["image", "text"],
    )
    msg = resp.choices[0].message
    content = msg.content or ""
    # Try various shapes the response might take
    images = getattr(msg, "images", None)
    if images:
        first = images[0]
        # Common shape: {"image_url": {"url": "data:image/png;base64,..."}}
        url = first.get("image_url", {}).get("url") if isinstance(first, dict) else None
        if url and url.startswith("data:image"):
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64), str(content)
    # Fallback: scan the text for a data: URL
    if "data:image" in str(content):
        idx = str(content).find("data:image")
        chunk = str(content)[idx:]
        end = chunk.find('"')
        if end < 0:
            end = chunk.find("\n")
        url = chunk[:end] if end > 0 else chunk
        if "," in url:
            _, b64 = url.split(",", 1)
            try:
                return base64.b64decode(b64), str(content)
            except Exception:
                pass
    return None, str(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-file", type=Path, default=REPO_ROOT / "paper" / "main.tex")
    parser.add_argument("--start", type=int, required=True, help="source block line start (1-indexed)")
    parser.add_argument("--end", type=int, required=True, help="source block line end (inclusive)")
    parser.add_argument("--target", required=True, help="short slug for output filenames (e.g. algorithm1_routing)")
    parser.add_argument("--text-model", default="openai/gpt-4o",
                        help="OpenRouter text model id for spec generation (default verified working)")
    parser.add_argument("--image-model", default="openai/gpt-5.4-image-2",
                        help="OpenRouter image model id for draft (default verified working)")
    parser.add_argument("--skip-image", action="store_true", help="generate spec only, skip image gen")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: set OPENROUTER_API_KEY", file=sys.stderr)
        return 2

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

    block = extract_block(args.source_file, args.start, args.end)
    block_hash = sha256_of_text(block)
    print(f"=== source block (lines {args.start}-{args.end}, sha256={block_hash[:16]}...) ===")
    print(block[:300] + ("..." if len(block) > 300 else ""))
    print()

    ILLUSTRATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Text model -> visual spec
    spec_path = ILLUSTRATIONS_DIR / f"{args.target}_spec.md"
    print(f"=== generating spec via {args.text_model}... ===")
    spec = call_text_model(client, args.text_model, SPEC_META_PROMPT.format(source_block=block))
    spec_path.write_text(spec, encoding="utf-8")
    spec_hash = sha256_of_text(spec)
    print(f"spec written: {spec_path}")
    print(f"spec hash: {spec_hash}")
    print()
    print("--- spec preview (first 400 chars) ---")
    print(spec[:400])
    print("..." if len(spec) > 400 else "")
    print()

    # 2. Image model -> draft
    if args.skip_image:
        print("--skip-image set; not generating draft image")
        return 0

    draft_path = ILLUSTRATIONS_DIR / f"{args.target}_draft.png"
    print(f"=== generating draft image via {args.image_model}... ===")
    image_prompt = IMAGE_PROMPT_TEMPLATE.format(spec=spec)
    image_bytes, raw = call_image_model(client, args.image_model, image_prompt)
    if image_bytes is None:
        print("WARNING: no image returned. Raw response:")
        print(raw[:500])
        return 1
    draft_path.write_bytes(image_bytes)
    draft_hash = sha256_of_bytes(image_bytes)
    print(f"draft written: {draft_path} ({len(image_bytes):,} bytes)")
    print(f"draft hash: {draft_hash}")
    print()

    # Print manifest stub
    print("=== manifest stub for ci/illustration_lineage.json ===")
    manifest_stub = {
        f"{args.target}.tex": {
            "source_file": str(args.source_file.relative_to(REPO_ROOT)).replace("\\", "/"),
            "source_line_start": args.start,
            "source_line_end": args.end,
            "source_excerpt": block[:200].replace("\n", " ")[:200] + "...",
            "source_hash": block_hash,
            "visual_spec": str(spec_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "visual_spec_hash": spec_hash,
            "spec_model": args.text_model,
            "draft_model": args.image_model,
            "draft_image": str(draft_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "draft_image_hash": draft_hash,
            "final_asset": f"paper/figures/{args.target}.tex",
            "final_asset_hash": "<populate after authoring deterministic TikZ>",
            "main_tex_ref": None,
            "in_use": False,
            "claim_scope": "schematic illustration only; not empirical evidence",
            "notes": f"Draft generated by {args.image_model} via OpenRouter; final asset is hand-authored TikZ inspired by the draft.",
        }
    }
    print(json.dumps(manifest_stub, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
