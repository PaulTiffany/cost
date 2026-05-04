#!/usr/bin/env python3
"""
cost_report.py

Walks every result JSON under supplementary/experiments/outputs/ (and a
fixed list of older sibling files), extracts token usage per model, and
multiplies by the current per-million-token prices in
ci/model_pricing.json. Writes a per-experiment + per-model cost
breakdown to ci/cost_report.json plus a human-readable summary to stdout.

Token sources (in priority order):
  1. _meta.tokens_used.{input, output}      (preferred; exact)
  2. Per-result entry tokens fields:
        usage.input_tokens / usage.output_tokens
        input_tokens / output_tokens
        tagger_tokens + generator_tokens (split known)
        tokens_used (total only; we split 50/50 with a flag)
  3. Estimate from response text length:    ~0.25 tokens per character
                                             (very rough; flagged 'estimated')

Usage:
    python ci/cost_report.py
    python ci/cost_report.py --by-model        # extra breakdown by model
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = Path(__file__).resolve().parent / "model_pricing.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "cost_report.json"

# Result-JSON locations to walk. Order matters only for stable output.
RESULT_LOCATIONS = [
    "supplementary/experiments/outputs",
    "supplementary/experiments",  # the older fixed_point_*_addition.json siblings
]
# Files in supplementary/experiments to include despite not being in outputs/
TOP_LEVEL_RESULT_GLOBS = [
    "fixed_point_claude_family*.json",
    "fixed_point_model_family*.json",
    "openrouter_*.json",
]
SKIP_FILE_SUFFIXES = (
    # Older versioned runs (kept for audit, not for cost rollup)
    "_v1_lenient.json", "_v1_4tier.json", "_v1_4models.json",
    "_v2_8tier_full.json", "_v2_t8_rerun.json", "_v2_7models.json",
)
# Skip merged / aggregate files: they contain canonical + addition results
# both, which would double-count if both the merged and the source files
# are walked. We attribute cost to the source files only.
SKIP_FILE_SUBSTRINGS = (
    "_full10.json",
    "_with_opus47.json", "_with_opus46.json", "_with_sonnet46.json",
    # high_k merged 2-model artifacts (contain canonical + addition both)
    "high_k_opus_with_opus47_results.json",
    "high_k_opus_with_opus46_results.json",
    "high_k_opus_with_sonnet46_results.json",
)


def load_pricing() -> dict:
    return json.loads(PRICING_PATH.read_text(encoding="utf-8"))


def price_for(model_id: str, pricing: dict) -> tuple[dict, str]:
    """Returns ({input, output}, source_label). Source label is one of
    'anthropic_direct', 'openrouter', or 'fallback'."""
    if not model_id:
        return (pricing["fallback"], "fallback_no_model_id")
    a = pricing.get("anthropic_direct", {}).get("models", {})
    o = pricing.get("openrouter", {}).get("models", {})
    if model_id in a:
        return (a[model_id], "anthropic_direct")
    if model_id in o:
        return (o[model_id], "openrouter")
    return (pricing["fallback"], f"fallback_unknown_id:{model_id}")


def estimate_tokens_from_text(text: str) -> int:
    """Crude estimator: ~4 chars per token for English text. Used only
    when no usage field is present anywhere in the result JSON."""
    if not isinstance(text, str):
        return 0
    return max(1, len(text) // 4)


def extract_per_model_tokens(payload) -> dict:
    """Returns {model_id: {input_tokens, output_tokens, n_calls, source}}.

    Walks the payload looking for token usage at multiple known locations.
    Marks entries 'measured' if any explicit token field was found, else
    'estimated' if it had to fall back to text length."""
    by_model: dict[str, dict] = {}
    if not isinstance(payload, dict):
        return by_model

    def bump(model_id: str, inp: int, out: int, source: str):
        d = by_model.setdefault(model_id, {
            "input_tokens": 0, "output_tokens": 0, "n_calls": 0, "sources": set(),
        })
        d["input_tokens"] += int(inp)
        d["output_tokens"] += int(out)
        d["n_calls"] += 1
        d["sources"].add(source)

    # 1. _meta-level totals (some experiments record per-model rollups here)
    meta = payload.get("_meta", payload.get("meta", {}))
    if isinstance(meta, dict):
        per_model_tokens = meta.get("per_model_tokens")
        if isinstance(per_model_tokens, dict):
            for mid, v in per_model_tokens.items():
                if isinstance(v, dict):
                    bump(mid, v.get("input", 0), v.get("output", 0) or v.get("output_tokens", 0),
                         "meta.per_model_tokens")
            # Don't return yet; some payloads have BOTH per-model totals AND results[]

    # 2. Per-result entries
    results = payload.get("results")
    if isinstance(results, list):
        for r in results:
            if not isinstance(r, dict):
                continue
            mid = r.get("model_id") or r.get("model") or meta.get("model_id") or meta.get("model")
            usage = r.get("usage") or {}
            inp = usage.get("input_tokens") or r.get("input_tokens") or 0
            out = usage.get("output_tokens") or r.get("output_tokens") or 0
            if inp or out:
                bump(mid, inp, out, "results[].usage_split")
                continue
            # tagger + generator split (implicit_k harness)
            t_tok = r.get("tagger_tokens", 0)
            g_tok = r.get("generator_tokens", 0)
            if t_tok or g_tok:
                # Tagger and generator are both completions; split 50/50 input/output
                total = t_tok + g_tok
                bump(mid, total // 2, total - total // 2, "results[].tagger_plus_generator")
                continue
            # tokens_used total only
            tu = r.get("tokens_used", 0)
            if tu:
                # Split 50/50 with a 'total_only' flag
                bump(mid, tu // 2, tu - tu // 2, "results[].tokens_used_total")
                continue
            # Last resort: estimate from response text length
            text = r.get("rewritten_text") or r.get("response") or r.get("output_text") or ""
            if text:
                est = estimate_tokens_from_text(text)
                bump(mid, est // 2, est - est // 2, "results[].text_estimate")

    # Convert sources sets to sorted lists for JSON
    for d in by_model.values():
        d["sources"] = sorted(d["sources"])
    return by_model


def cost_for(model_id: str, input_tokens: int, output_tokens: int, pricing: dict) -> tuple[float, str]:
    p, src = price_for(model_id, pricing)
    cost = (input_tokens / 1_000_000.0) * p["input"] + (output_tokens / 1_000_000.0) * p["output"]
    return (cost, src)


def _should_skip(p: Path) -> bool:
    if any(p.name.endswith(s) for s in SKIP_FILE_SUFFIXES):
        return True
    if any(s in p.name for s in SKIP_FILE_SUBSTRINGS):
        return True
    return False


def discover_result_files() -> list[Path]:
    files: list[Path] = []
    out_dir = REPO_ROOT / RESULT_LOCATIONS[0]
    if out_dir.exists():
        for p in out_dir.rglob("*.json"):
            if _should_skip(p):
                continue
            files.append(p)
    top_dir = REPO_ROOT / RESULT_LOCATIONS[1]
    if top_dir.exists():
        for pat in TOP_LEVEL_RESULT_GLOBS:
            for p in top_dir.glob(pat):
                if _should_skip(p):
                    continue
                files.append(p)
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--by-model", action="store_true",
                        help="print extra breakdown by model_id across all experiments")
    args = parser.parse_args()

    if not PRICING_PATH.exists():
        print(f"Pricing file not found: {PRICING_PATH}", file=sys.stderr)
        return 1
    pricing = load_pricing()

    last_refreshed = pricing.get("_meta", {}).get("last_refreshed_iso", "unknown")
    print(f"Pricing source: {PRICING_PATH.name} (last refreshed {last_refreshed})")

    files = discover_result_files()
    print(f"Walking {len(files)} result JSONs ...\n")

    per_experiment: list[dict] = []
    grand_total = 0.0
    grand_input = 0
    grand_output = 0
    by_model_totals: dict[str, dict] = {}

    for p in files:
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        token_breakdown = extract_per_model_tokens(payload)
        if not token_breakdown:
            continue
        exp_total = 0.0
        exp_input = 0
        exp_output = 0
        per_model_costs: list[dict] = []
        for mid, t in token_breakdown.items():
            cost, src = cost_for(mid, t["input_tokens"], t["output_tokens"], pricing)
            exp_total += cost
            exp_input += t["input_tokens"]
            exp_output += t["output_tokens"]
            per_model_costs.append({
                "model_id": mid,
                "input_tokens": t["input_tokens"],
                "output_tokens": t["output_tokens"],
                "n_calls": t["n_calls"],
                "cost_usd": round(cost, 4),
                "pricing_source": src,
                "token_sources": t["sources"],
            })
            bm = by_model_totals.setdefault(mid or "unknown", {
                "input_tokens": 0, "output_tokens": 0, "n_calls": 0, "cost_usd": 0.0,
            })
            bm["input_tokens"] += t["input_tokens"]
            bm["output_tokens"] += t["output_tokens"]
            bm["n_calls"] += t["n_calls"]
            bm["cost_usd"] += cost
        rel = str(p.relative_to(REPO_ROOT))
        per_experiment.append({
            "file": rel,
            "input_tokens": exp_input,
            "output_tokens": exp_output,
            "cost_usd": round(exp_total, 4),
            "per_model": per_model_costs,
        })
        grand_total += exp_total
        grand_input += exp_input
        grand_output += exp_output

    payload_out = {
        "_meta": {
            "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pricing_last_refreshed": last_refreshed,
            "n_files_scanned": len(files),
            "n_files_with_token_data": len(per_experiment),
            "grand_total_usd": round(grand_total, 4),
            "grand_input_tokens": grand_input,
            "grand_output_tokens": grand_output,
            "note": ("Costs computed from recorded token usage where available, "
                     "estimated from response text length where not. See "
                     "per-experiment 'token_sources' field; entries containing "
                     "'text_estimate' are estimated, all others are measured."),
        },
        "by_experiment": sorted(per_experiment, key=lambda e: e["cost_usd"], reverse=True),
        "by_model": {
            mid: {**v, "cost_usd": round(v["cost_usd"], 4)}
            for mid, v in sorted(by_model_totals.items(),
                                 key=lambda x: x[1]["cost_usd"], reverse=True)
        },
    }
    OUTPUT_PATH.write_text(json.dumps(payload_out, indent=2), encoding="utf-8")

    print("=" * 78)
    print(f"COST REPORT  (USD)")
    print("=" * 78)
    print(f"Files with token data: {len(per_experiment)} of {len(files)} scanned")
    print(f"Grand total: ${grand_total:,.2f}  "
          f"({grand_input:,} input + {grand_output:,} output tokens)")
    print()
    print(f"{'cost_usd':>9}  {'in_tok':>10}  {'out_tok':>10}  file")
    for e in payload_out["by_experiment"][:20]:
        print(f"  ${e['cost_usd']:>7,.2f}  {e['input_tokens']:>9,}  {e['output_tokens']:>9,}  {e['file']}")
    if len(payload_out["by_experiment"]) > 20:
        print(f"  ... ({len(payload_out['by_experiment']) - 20} more, see {OUTPUT_PATH.name})")

    if args.by_model:
        print()
        print(f"{'cost_usd':>9}  {'n_calls':>7}  {'in_tok':>10}  {'out_tok':>10}  model_id")
        for mid, v in payload_out["by_model"].items():
            print(f"  ${v['cost_usd']:>7,.2f}  {v['n_calls']:>6,}  {v['input_tokens']:>9,}  {v['output_tokens']:>9,}  {mid}")

    print()
    print(f"JSON: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
