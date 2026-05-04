#!/usr/bin/env python3
"""
implicit_k_decompression_harness.py

Closes the "Implicit k" loop a reviewer flagged: how does the geometric router
get its constraint set when the user types "production-grade code" or "be
professional," not an explicit list?

Pipeline (Gemini collaborator design, 2026-05):

  implicit prompt
        │
        │  (1) Tagger: LLM-as-classifier (zeroth-order)
        ▼
  selected library IDs   ← Static Constraint Library (deterministic verifiers)
        │
        │  (2) Implicit k = |selected|; one_shot vs staged decided by k threshold
        ▼
  generator call(s)
        │
        │  (3) Linter: run each verifier in Python (no LLM judge)
        ▼
  pass/fail (deterministic)

The Library is the existing policy_density rule bank: 34 atomic rules, each a
(semantic_description, python_verifier) pair. The tagger never invents
constraints; it only selects from this fixed bank. The generator never decides
correctness; the linter does.

This is the "no-judge" stack: prompt-gen LLM, tagger LLM, generator LLM, but
the verdict is Python.

Usage:
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    python implicit_k_decompression_harness.py
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_density_compliance_harness import (
    Rule, build_all_rules, score, extract_email,
    prompt_one_shot, prompt_staged_first, prompt_staged_next, stage_chunks,
    DRAFT_EMAIL, CLAUDE_FAMILY, LEGACY_TEMPERATURE, MAX_TOKENS, call_anthropic,
)

N_WORKERS = 4
ROUTER_THRESHOLD_K = 8  # k > threshold → staged; else one_shot

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = "supplementary/experiments/implicit_k_decompression_harness.py"
OUTPUT_DIR = REPO_ROOT / "supplementary/experiments/outputs/implicit_k"
OUTPUT_PATH = OUTPUT_DIR / "implicit_k_results.json"


# =============================================================================
# IMPLICIT PROMPTS (the "buzzword" inputs the user actually types)
# =============================================================================

IMPLICIT_PROMPTS = [
    {"id": "P1_basic_email", "text": "Write a basic email announcing a new product."},
    {"id": "P2_professional", "text": "Write a professional, polished customer-facing announcement."},
    {"id": "P3_compliant", "text": "Write a fully GDPR/SEC-compliant marketing email for a new financial product."},
    {"id": "P4_executive", "text": "Write an executive-level Q4 results briefing for shareholders."},
    {"id": "P5_enterprise", "text": "Write an enterprise-grade product launch email that meets corporate brand, legal, and compliance standards."},
    {"id": "P6_concise_legal", "text": "Write a concise, legally airtight notice to subscribers about a service change."},
    {"id": "P7_brand_voice", "text": "Write a tightly on-brand corporate update; follow our style guide strictly."},
    {"id": "P8_regulatory_finance", "text": "Write a regulator-facing compliance memo for a financial product disclosure."},
]


# =============================================================================
# TAGGER (LLM-as-classifier; never invents rules)
# =============================================================================

def format_library_for_tagger(rules: List[Rule]) -> str:
    return "\n".join(f"  [{r.name}] {r.description}" for r in rules)


def prompt_tagger(implicit_prompt: str, library: List[Rule]) -> str:
    return f"""You are a compliance classifier. Given a user request, select which rules from a fixed library are implicitly required.

USER REQUEST:
\"\"\"{implicit_prompt}\"\"\"

RULE LIBRARY (each rule has an [id] and a description):
{format_library_for_tagger(library)}

Task: return ONLY the rule IDs that this request implicitly requires. Be conservative: include a rule only if the request clearly implies it. Do not invent rules outside this library.

Output format (JSON only, no preamble):
{{"selected_ids": ["id1", "id2", ...]}}"""


def parse_tagger_response(text: str, library: List[Rule]) -> List[Rule]:
    valid_names = {r.name for r in library}
    by_name = {r.name: r for r in library}
    m = re.search(r"\{[^{}]*\"selected_ids\"\s*:\s*\[(.*?)\][^{}]*\}", text, re.DOTALL)
    if not m:
        m = re.search(r"\[(.*?)\]", text, re.DOTALL)
        if not m:
            return []
        ids_blob = m.group(1)
    else:
        ids_blob = m.group(1)
    raw = re.findall(r"\"([^\"]+)\"", ids_blob)
    seen = set()
    selected: List[Rule] = []
    for name in raw:
        if name in valid_names and name not in seen:
            selected.append(by_name[name])
            seen.add(name)
    return selected


# =============================================================================
# ROUTER (geometric stand-in: pure k-threshold for this demo)
# =============================================================================

def route_decision(k: int, threshold: int = ROUTER_THRESHOLD_K) -> str:
    return "staged" if k > threshold else "one_shot"


# =============================================================================
# PIPELINE
# =============================================================================

@dataclass
class ImplicitKResult:
    prompt_id: str
    implicit_text: str
    model_name: str
    model_id: str
    extracted_ids: List[str]
    extracted_k: int
    routing: str
    rewritten_text: str
    n_pass: int
    n_total: int
    all_pass: bool
    rule_results: dict
    tagger_tokens: int
    generator_tokens: int
    elapsed_sec: float


def run_one(client, model_name: str, prompt_obj: dict, library: List[Rule]) -> ImplicitKResult:
    model_id = CLAUDE_FAMILY[model_name]
    t0 = time.time()

    # 1. Tagger
    tagger_text, tagger_toks = call_anthropic(
        client, model_id, prompt_tagger(prompt_obj["text"], library), max_tokens=512
    )
    selected = parse_tagger_response(tagger_text, library)
    k = len(selected)

    # 2. Router
    routing = route_decision(k)

    # 3. Generator
    gen_toks = 0
    if k == 0:
        # Tagger returned nothing usable; degenerate case
        final_text = ""
    elif routing == "one_shot":
        text, toks = call_anthropic(client, model_id, prompt_one_shot(selected), MAX_TOKENS)
        gen_toks += toks
        final_text = extract_email(text)
    else:
        chunks = stage_chunks(selected, n_stages=min(3, len(selected)))
        text, toks = call_anthropic(client, model_id, prompt_staged_first(chunks[0], 0, len(chunks)), MAX_TOKENS)
        gen_toks += toks
        current = extract_email(text)
        applied: List[Rule] = list(chunks[0])
        for i in range(1, len(chunks)):
            text, toks = call_anthropic(
                client, model_id, prompt_staged_next(current, applied, chunks[i], i, len(chunks)), MAX_TOKENS
            )
            gen_toks += toks
            current = extract_email(text)
            applied.extend(chunks[i])
        final_text = current

    # 4. Linter (deterministic)
    sc = score(final_text, selected) if selected else {"n_pass": 0, "n_total": 0, "all_pass": False, "rule_results": {}}

    return ImplicitKResult(
        prompt_id=prompt_obj["id"],
        implicit_text=prompt_obj["text"],
        model_name=model_name,
        model_id=model_id,
        extracted_ids=[r.name for r in selected],
        extracted_k=k,
        routing=routing,
        rewritten_text=final_text,
        n_pass=sc["n_pass"],
        n_total=sc["n_total"],
        all_pass=sc["all_pass"],
        rule_results=sc["rule_results"],
        tagger_tokens=tagger_toks,
        generator_tokens=gen_toks,
        elapsed_sec=time.time() - t0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=str, default="haiku-4.5,sonnet-4.5,opus-4.5,opus-4.7")
    parser.add_argument("--router-threshold", type=int, default=ROUTER_THRESHOLD_K)
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip() in CLAUDE_FAMILY]

    try:
        import anthropic
    except ImportError:
        print("anthropic package not found", file=sys.stderr); return 1
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY env var required", file=sys.stderr); return 1
    client = anthropic.Anthropic(api_key=api_key)

    library = build_all_rules()
    print(f"Library size: {len(library)} atomic rules (each with deterministic verifier)")
    print(f"Implicit prompts: {len(IMPLICIT_PROMPTS)}")
    print(f"Models: {models}")
    print(f"Router threshold: k > {args.router_threshold} -> staged, else one_shot")
    print()

    cells = [(m, p) for m in models for p in IMPLICIT_PROMPTS]
    print(f"Running {len(cells)} cells (parallel N_WORKERS={N_WORKERS})", flush=True)
    t_start = time.time()

    results: List[ImplicitKResult] = []
    print_lock = threading.Lock()

    def task(args_tuple):
        m, p = args_tuple
        try:
            r = run_one(client, m, p, library)
            with print_lock:
                tag = "PASS" if r.all_pass else f"{r.n_pass}/{r.n_total}"
                print(f"  [done] {m:<12} {p['id']:<22} k={r.extracted_k:>2} -> {r.routing:<8} {tag} ({r.elapsed_sec:.1f}s)", flush=True)
            return r
        except Exception as e:
            with print_lock:
                print(f"  [FAIL] {m} {p['id']}: {e}", file=sys.stderr, flush=True)
            return None

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = [ex.submit(task, c) for c in cells]
        for fut in as_completed(futures):
            r = fut.result()
            if r is not None:
                results.append(r)

    elapsed = time.time() - t_start

    # Summary table
    by_prompt: dict[str, list[ImplicitKResult]] = {}
    for r in results:
        by_prompt.setdefault(r.prompt_id, []).append(r)

    print("\n=== Per-prompt summary (k extracted, routing, pass-rate across models) ===")
    print(f"{'prompt_id':<22} {'mean_k':<7} {'route':<10} {'pass_rate':<10}")
    summary = []
    for pid, rs in by_prompt.items():
        ks = [r.extracted_k for r in rs]
        mean_k = sum(ks) / len(ks)
        routes = [r.routing for r in rs]
        majority_route = max(set(routes), key=routes.count)
        pass_rate = sum(1 for r in rs if r.all_pass) / len(rs)
        summary.append({
            "prompt_id": pid,
            "implicit_text": rs[0].implicit_text,
            "mean_extracted_k": mean_k,
            "min_extracted_k": min(ks),
            "max_extracted_k": max(ks),
            "majority_route": majority_route,
            "all_pass_rate": pass_rate,
            "n_models": len(rs),
        })
        print(f"  {pid:<20} {mean_k:>5.1f}  {majority_route:<10} {pass_rate*100:>5.0f}%")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = {
        "_meta": {
            "experiment": "implicit_k_decompression",
            "via": "Anthropic SDK",
            "generator_script": SCRIPT_PATH,
            "script_hash": script_hash,
            "library_source": "policy_density_compliance_harness.py (build_all_rules)",
            "library_size": len(library),
            "library_rule_ids": [r.name for r in library],
            "models": models,
            "router_threshold_k": args.router_threshold,
            "implicit_prompts": IMPLICIT_PROMPTS,
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "finished_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": elapsed,
            "n_cells": len(results),
            "design_note": "Constraint Decompression Pipeline (Gemini collaborator, 2026-05). "
                           "Demonstrates the 'no-judge' stack for the Implicit-k reviewer concern: "
                           "prompt-gen LLM, tagger LLM, generator LLM, but the verdict is a "
                           "deterministic Python linter against a static library of atomic verifiers. "
                           "The tagger never invents rules; it only selects from a fixed bank. The "
                           "generator never decides correctness; the linter does. Routing here uses a "
                           "simple k-threshold (k > " + str(args.router_threshold) + " -> staged); a "
                           "production system would replace this with the geometric rho-hat / "
                           "delta_min computation against the library's pre-computed embeddings.",
            "sampling_note": "Non-opus-4.7 models use temperature=0.0; opus-4.7 omits the parameter "
                             "(rejected by API). Source: https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7",
        },
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"Total wall: {elapsed:.1f}s; total cells: {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
