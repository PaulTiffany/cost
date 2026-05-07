#!/usr/bin/env python3
"""
model_discovery.py

Discovers reachable models on Anthropic + OpenRouter for the unified
two-channel audit experiment (audit v4 unified).

  * Anthropic: GET https://api.anthropic.com/v1/models
      headers: x-api-key, anthropic-version
      free, no token cost.
  * OpenRouter: GET https://openrouter.ai/api/v1/models
      headers: Authorization: Bearer <key>
      free, no token cost.

Outputs:
  supplementary/experiments/outputs/audit_v4/model_discovery.json

Console:
  Prints recommended model lists for the orchestrator.

Secrets are loaded from the environment. Either export
ANTHROPIC_API_KEY / OPENROUTER_API_KEY directly, or point
AUDIT_ANTHROPIC_ENV_FILE / AUDIT_OPENROUTER_ENV_FILE at files containing
``KEY=value`` lines.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import urllib.request
import urllib.error

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "outputs" / "audit_v4"
OUTPUT_FILE = OUTPUT_DIR / "model_discovery.json"

import os as _os
ANTHROPIC_ENV = Path(_os.environ.get("AUDIT_ANTHROPIC_ENV_FILE", "")) if _os.environ.get("AUDIT_ANTHROPIC_ENV_FILE") else None
OPENROUTER_ENV = Path(_os.environ.get("AUDIT_OPENROUTER_ENV_FILE", "")) if _os.environ.get("AUDIT_OPENROUTER_ENV_FILE") else None

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/models"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/models"

ANTHROPIC_VERSION = "2023-06-01"

# Code-oriented open-weight model name fragments we want to surface.
CODE_MODEL_KEYWORDS = (
    "qwen", "deepseek", "codellama", "code-llama",
    "codestral", "mistral-coder", "coder",
)

# Preferred reachable code-generation open-weight models for the
# pivot/positive-control channel. Prefix-matched against discovered ids;
# the first match in this list is the recommendation.
RECOMMENDED_OPENROUTER_PREFERENCES = [
    "qwen/qwen-2.5-coder-32b-instruct",
    "qwen/qwen3-coder",
    "deepseek/deepseek-chat-v3.1",
    "mistralai/codestral-2508",
]


def _read_env_var(env_path, key: str) -> str:
    """Resolve ``key`` from os.environ first, then from ``env_path`` if set."""
    import os as _os
    val = _os.environ.get(key)
    if val:
        return val.strip().strip('"').strip("'")
    if env_path is None or not env_path.exists():
        raise RuntimeError(
            f"{key} not set in environment and no env file path provided"
        )
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"{key} not present in env file")


def _http_get_json(url: str, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def discover_anthropic() -> dict:
    api_key = _read_env_var(ANTHROPIC_ENV, "ANTHROPIC_API_KEY")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    payload = _http_get_json(ANTHROPIC_ENDPOINT, headers)
    raw_models = payload.get("data", []) or []
    all_ids: List[str] = [m.get("id", "") for m in raw_models if m.get("id")]
    claude_only = sorted([m for m in all_ids if m.startswith("claude-")])
    return {
        "available": claude_only,
        "all_returned": sorted(all_ids),
        "discovery_endpoint": ANTHROPIC_ENDPOINT,
        "queried_at_utc": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

def discover_openrouter() -> dict:
    api_key = _read_env_var(OPENROUTER_ENV, "OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = _http_get_json(OPENROUTER_ENDPOINT, headers)
    raw_models = payload.get("data", []) or []
    all_ids: List[str] = [m.get("id", "") for m in raw_models if m.get("id")]
    code_oriented = sorted([
        mid for mid in all_ids
        if any(kw in mid.lower() for kw in CODE_MODEL_KEYWORDS)
    ])
    available_set = set(code_oriented)
    recommended: List[str] = [
        m for m in RECOMMENDED_OPENROUTER_PREFERENCES if m in available_set
    ]
    return {
        "available_code_oriented": code_oriented,
        "recommended": recommended,
        "n_total_models": len(all_ids),
        "discovery_endpoint": OPENROUTER_ENDPOINT,
        "queried_at_utc": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("Model Discovery (audit v4 unified)")
    print("=" * 72)

    result = {"anthropic": None, "openrouter": None}
    rc = 0

    # Anthropic
    print("\n[Anthropic] Querying", ANTHROPIC_ENDPOINT, "...")
    try:
        anth = discover_anthropic()
        result["anthropic"] = anth
        n = len(anth["available"])
        print(f"  {n} Claude models found:")
        for mid in anth["available"]:
            print(f"    - {mid}")
        print(f"  >> Recommendation: all {n} for full-family coverage.")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        result["anthropic"] = {
            "available": [],
            "discovery_endpoint": ANTHROPIC_ENDPOINT,
            "queried_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(e).__name__}: {e}",
        }
        rc = 1

    # OpenRouter
    print("\n[OpenRouter] Querying", OPENROUTER_ENDPOINT, "...")
    try:
        orr = discover_openrouter()
        result["openrouter"] = orr
        n = len(orr["available_code_oriented"])
        print(f"  {orr['n_total_models']} total models on OpenRouter; "
              f"{n} match code-oriented filter.")
        recs = orr.get("recommended", [])
        print(f"  >> Recommendation: {len(recs)} reachable code model(s) "
              f"for the open-weight channel:")
        for mid in recs:
            print(f"    - {mid}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        result["openrouter"] = {
            "available_code_oriented": [],
            "discovery_endpoint": OPENROUTER_ENDPOINT,
            "queried_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(e).__name__}: {e}",
        }
        rc = 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nWrote {OUTPUT_FILE}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
