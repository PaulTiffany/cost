#!/usr/bin/env python3
"""
illustration_draft_loop.py - Run the L14 draft step.

Sends the source LaTeX block (and any extra input files) to an image
model. Each input is hashed for provenance. The model returns a draft
PNG. The human authors a deterministic TikZ asset from there; that
asset is what ships.

Usage:
  export OPENROUTER_API_KEY=...
  # Source block alone
  python illustration_draft_loop.py \\
    --start 96 --end 114 \\
    --target algorithm1_routing

  # Source block + extra input file(s)
  python illustration_draft_loop.py \\
    --start 349 --end 350 \\
    --target staging_vs_refine \\
    --extra-input supplementary/illustrations/staging_vs_refine_direction.txt
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


STYLE_SUFFIX = """

Style: minimal, sans-serif labels, rounded rectangles for nodes, arrows for flow,
white background, publication-quality. No artistic flourishes."""


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
    parser.add_argument("--extra-input", action="append", default=[],
                        help="OPTIONAL: path to an additional input file (text). Each --extra-input adds another constraint to the image model's joint input. Each file is hashed for provenance. Use when the source block alone needs more framing.")
    parser.add_argument("--image-model", default="openai/gpt-5.4-image-2",
                        help="OpenRouter image model id (default verified working)")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: set OPENROUTER_API_KEY", file=sys.stderr)
        return 2

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

    ILLUSTRATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Build the inputs list. The source LaTeX block is one input;
    # any --extra-input file is another. All hashed; all concatenated
    # for the image model prompt.
    inputs: list[dict] = []
    prompt_parts: list[str] = []

    block = extract_block(args.source_file, args.start, args.end)
    block_hash = sha256_of_text(block)
    inputs.append({
        "kind": "latex_block",
        "file": str(args.source_file.relative_to(REPO_ROOT)).replace("\\", "/"),
        "line_start": args.start,
        "line_end": args.end,
        "hash": block_hash,
    })
    prompt_parts.append(f"Source LaTeX block:\n{block}")
    print(f"=== input 0: latex_block from {args.source_file.name}:{args.start}-{args.end} (sha256={block_hash[:16]}...) ===")
    print(block[:240] + ("..." if len(block) > 240 else ""))
    print()

    for i, extra_path_str in enumerate(args.extra_input, start=1):
        extra_path = Path(extra_path_str)
        if not extra_path.is_absolute():
            extra_path = (REPO_ROOT / extra_path).resolve()
        if not extra_path.exists():
            print(f"ERROR: extra-input not found: {extra_path}", file=sys.stderr)
            return 2
        extra_text = extra_path.read_text(encoding="utf-8")
        extra_hash = sha256_of_text(extra_text)
        inputs.append({
            "kind": "text_file",
            "file": str(extra_path.relative_to(REPO_ROOT)).replace("\\", "/") if extra_path.is_relative_to(REPO_ROOT) else str(extra_path),
            "hash": extra_hash,
        })
        prompt_parts.append(f"Additional input ({extra_path.name}):\n{extra_text}")
        print(f"=== input {i}: text_file {extra_path.name} (sha256={extra_hash[:16]}...) ===")
        print(extra_text[:240] + ("..." if len(extra_text) > 240 else ""))
        print()

    image_prompt = "Generate a clean schematic diagram for a research paper.\n\n" + "\n\n".join(prompt_parts) + STYLE_SUFFIX

    draft_path = ILLUSTRATIONS_DIR / f"{args.target}_draft.png"
    print(f"=== generating draft via {args.image_model}... ===")
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
            "inputs": inputs,
            "draft_model": args.image_model,
            "draft_image": str(draft_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "draft_image_hash": draft_hash,
            "final_asset": f"paper/figures/{args.target}.tex",
            "final_asset_hash": "<populate after authoring deterministic TikZ>",
            "main_tex_ref": None,
            "in_use": False,
            "claim_scope": "schematic illustration only; not empirical evidence",
        }
    }
    print(json.dumps(manifest_stub, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
