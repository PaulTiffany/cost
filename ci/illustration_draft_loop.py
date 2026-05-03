#!/usr/bin/env python3
"""
illustration_draft_loop.py - Run the L14 draft step.

For a given source LaTeX block + a human-authored direction prompt,
call an image model to produce a visual draft. The draft is for
exploration only; the certificate trusts only the deterministic
TikZ redraw the human authors next.

Why no text-model layer? Earlier versions of this script had a
text model translate the source LaTeX block into a "spec" before
calling the image model. That layer was an LLM judge in disguise:
it interpreted what the human already knows and removed the human's
ability to say "compare this to X" or "highlight Y". The image
model is a TOOL for visual exploration; the human writes the
direction directly. The source LaTeX hash provides the provenance
anchor; the human's direction provides the creative intent.

Usage:
  export OPENROUTER_API_KEY=...
  python illustration_draft_loop.py \\
    --source-file paper/main.tex \\
    --start 349 --end 350 \\
    --target staging_vs_refine \\
    --direction "3-panel comparison: Self-Refine (loop around output) | \\
                 Decompose (split->solve->combine) | Staged (sequential \\
                 per-constraint with anchor). Token costs: 640 / parallel / 512." \\
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


IMAGE_PROMPT_TEMPLATE = """Generate a clean schematic diagram for a research paper.

Source context (LaTeX block being illustrated, for grounding only):
{source_block}

Visualization direction (what to actually draw):
{direction}

Style: minimal, sans-serif labels, rounded rectangles for nodes, arrows for flow,
white background, publication-quality. No artistic flourishes.
"""


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def extract_block(source_file: Path, start: int, end: int) -> str:
    lines = source_file.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[start - 1:end])


def call_image_model(client: OpenAI, model: str, prompt: str) -> tuple[bytes | None, str]:
    """Return (image_bytes, response_text). Image models on OpenRouter
    return image data via the message.images field."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        modalities=["image", "text"],
    )
    msg = resp.choices[0].message
    content = msg.content or ""
    images = getattr(msg, "images", None)
    if images:
        first = images[0]
        url = first.get("image_url", {}).get("url") if isinstance(first, dict) else None
        if url and url.startswith("data:image"):
            _, b64 = url.split(",", 1)
            return base64.b64decode(b64), str(content)
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
    parser.add_argument("--target", required=True, help="short slug for output filenames")
    parser.add_argument("--direction", required=True,
                        help="Human-authored direction for what to draw. This is the exploration intent — say what you want, not what the source says. Hashed and stored in the manifest as direction_hash.")
    parser.add_argument("--image-model", default="openai/gpt-5.4-image-2",
                        help="OpenRouter image model id (default verified working)")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: set OPENROUTER_API_KEY", file=sys.stderr)
        return 2

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

    block = extract_block(args.source_file, args.start, args.end)
    block_hash = sha256_of_text(block)
    direction_hash = sha256_of_text(args.direction)

    print(f"=== source block (lines {args.start}-{args.end}, sha256={block_hash[:16]}...) ===")
    print(block[:240] + ("..." if len(block) > 240 else ""))
    print()
    print(f"=== direction (sha256={direction_hash[:16]}...) ===")
    print(args.direction[:300] + ("..." if len(args.direction) > 300 else ""))
    print()

    ILLUSTRATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Save the direction prompt to a file for human inspection (and so
    # the hash check has something to recompute against later).
    direction_path = ILLUSTRATIONS_DIR / f"{args.target}_direction.txt"
    direction_path.write_text(args.direction, encoding="utf-8")
    print(f"direction written: {direction_path}")
    print()

    draft_path = ILLUSTRATIONS_DIR / f"{args.target}_draft.png"
    print(f"=== generating draft via {args.image_model}... ===")
    image_prompt = IMAGE_PROMPT_TEMPLATE.format(source_block=block, direction=args.direction)
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

    print("=== manifest stub for ci/illustration_lineage.json ===")
    manifest_stub = {
        f"{args.target}.tex": {
            "source_file": str(args.source_file.relative_to(REPO_ROOT)).replace("\\", "/"),
            "source_line_start": args.start,
            "source_line_end": args.end,
            "source_excerpt": block[:200].replace("\n", " ")[:200] + "...",
            "source_hash": block_hash,
            "direction_prompt": args.direction,
            "direction_hash": direction_hash,
            "draft_model": args.image_model,
            "draft_image": str(draft_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "draft_image_hash": draft_hash,
            "final_asset": f"paper/figures/{args.target}.tex",
            "final_asset_hash": "<populate after authoring deterministic TikZ>",
            "main_tex_ref": None,
            "in_use": False,
            "claim_scope": "schematic illustration only; not empirical evidence",
            "notes": f"Direction prompt human-authored; image draft via {args.image_model}; deterministic TikZ is the shipping asset.",
        }
    }
    print(json.dumps(manifest_stub, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
