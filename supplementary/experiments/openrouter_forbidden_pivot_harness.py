#!/usr/bin/env python3
"""
OpenRouter forbidden-pivot experiment harness.

USAGE
-----
    $env:OPENROUTER_API_KEY = "sk-or-..."
    python supplementary/experiments/openrouter_forbidden_pivot_harness.py

Optional flags (not yet wired; add as needed):
    --checkpoint PATH   Override default checkpoint path.

Goal: test whether premier models pivot more often under forbidden-technique
constraints, and whether pivots remain detectable mid-generation.

Design:
    6 models × 6 tasks × 2 conditions × 3 trials = 216 calls.
    Conditions:
      compatible_forbidden   — forbid ONE technique; alternative route exists.
      conflicting_forbidden  — forbid TWO competing techniques; near-infeasible.

Crash-resilient: each trial written to the output JSON immediately after
completion.  On restart, completed trials (those with a 'functional_pass' key)
are skipped; error entries are retried.
"""

import ast
import json
import os
import re
import sys
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Concurrency helpers
# ---------------------------------------------------------------------------
N_WORKERS = 8
_ckpt_lock = threading.Lock()
_counter_lock = threading.Lock()
_counter = {"done": 0}

# ---------------------------------------------------------------------------
# Local imports (same directory as openrouter_regression_harness.py)
# ---------------------------------------------------------------------------
from code_constraint_verifier import extract_code_block, verify_functional
from code_constraint_tasks import TASKS

# ---------------------------------------------------------------------------
# Model panel
# ---------------------------------------------------------------------------
# Resolve-at-runtime preferred IDs.  If OpenRouter rejects a premier model ID
# (BadRequest / 404 / ModelNotFound), the harness logs and skips that slot
# rather than crashing.
MODEL_PANEL = {
    "premier_1":       "anthropic/claude-opus-4.5",
    "premier_2":       "openai/gpt-4o",           # gpt-5 not yet broadly available; fall back to gpt-4o
    "cheap_current_1": "anthropic/claude-3.5-haiku",
    "cheap_current_2": "openai/gpt-4o-mini",
    "legacy_1":        "openai/gpt-4o",
    "legacy_2":        "google/gemini-2.5-flash",
}
# Fallback panel used when a premier model is rejected.
FALLBACK_PANEL = {
    "premier_1":       "anthropic/claude-sonnet-4.5",
    "premier_2":       "openai/gpt-4o",
    "cheap_current_1": "anthropic/claude-3.5-haiku",
    "cheap_current_2": "openai/gpt-4o-mini",
    "legacy_1":        "openai/gpt-4o",
    "legacy_2":        "google/gemini-2.5-flash",
}

# ---------------------------------------------------------------------------
# Task selection (6 of 12 available tasks)
# ---------------------------------------------------------------------------
TASK_IDS = ["factorial", "fibonacci", "find_max", "count_vowels", "fizzbuzz", "gcd"]

# ---------------------------------------------------------------------------
# Forbidden-technique pairs
# Each entry: (compatible_constraint_str, conflicting_constraint_str)
# These strings are injected verbatim into the prompt.
# ---------------------------------------------------------------------------
FORBIDDEN_PAIRS = {
    "factorial": (
        "Do NOT use recursion. Solve iteratively (loops are allowed).",
        "Do NOT use recursion AND do NOT use any for/while loops AND do NOT use stdlib shortcuts "
        "(no math.factorial, no math.prod, no functools.reduce, no operator helpers, no comprehensions). "
        "You must compute the result yourself with arithmetic only.",
    ),
    "fibonacci": (
        "Do NOT use recursion. Solve iteratively (loops are allowed).",
        "Do NOT use recursion AND do NOT use any for/while loops AND do NOT use stdlib shortcuts "
        "(no math.* shortcuts, no functools.reduce, no itertools.accumulate, no comprehensions, no closed-form). "
        "You must compute the n-th Fibonacci yourself.",
    ),
    "find_max": (
        "Do NOT use Python's built-in max() function. Use a loop or other approach.",
        "Do NOT use Python's built-in max() function AND do NOT use any for/while loops AND do NOT use "
        "comprehensions, sorted()[-1], heapq.nlargest, statistics.* helpers, or numpy.max. "
        "Keep the function under 8 lines.",
    ),
    "count_vowels": (
        "Do NOT use a for loop. Use a different approach (e.g., sum() with a generator, str.count, etc.).",
        "Do NOT use any for/while loops AND do NOT use sum(), str.count(), list/dict/set comprehensions, "
        "collections.Counter, regex re.findall, or any helper that does the counting for you.",
    ),
    "fizzbuzz": (
        "Do NOT use any if/elif/else chains. Use an alternative approach (e.g., string concatenation logic).",
        "Do NOT use any if/elif/else chains AND do NOT use the modulo operator (%) AND do NOT use "
        "divmod, ternary expressions, or dict-of-conditions lookups. Use string slicing or arithmetic only.",
    ),
    "gcd": (
        "Do NOT use recursion. Solve iteratively (loops are allowed).",
        "Do NOT use recursion AND do NOT use any for/while loops AND do NOT use math.gcd, math.lcm, "
        "fractions.gcd, numpy.gcd, or any stdlib gcd helper. You must compute it yourself.",
    ),
}

# Module-qualified stdlib calls that count as "escape hatches" for the
# conflicting condition. Per task we expand this list; values are sets of
# (module, attr) tuples or bare attr names that should fail the technique check.
ESCAPE_LIBRARIES = {
    "factorial": {
        ("math", "factorial"), ("math", "prod"),
        ("functools", "reduce"),
    },
    "fibonacci": {
        ("itertools", "accumulate"), ("functools", "reduce"),
        ("math", "comb"),  # closed-form via Binet would also use math.sqrt; cover broadly
    },
    "find_max": {
        ("heapq", "nlargest"), ("statistics", "fmax"), ("statistics", "median_high"),
        ("numpy", "max"), ("np", "max"),
    },
    "count_vowels": {
        ("collections", "Counter"), ("re", "findall"), ("re", "finditer"),
    },
    "fizzbuzz": set(),  # all blocked via syntax check
    "gcd": {
        ("math", "gcd"), ("math", "lcm"),
        ("fractions", "gcd"),
        ("numpy", "gcd"), ("np", "gcd"),
    },
}

CONDITIONS = ["compatible_forbidden", "conflicting_forbidden"]
N_TRIALS = 3
TEMPERATURE = 0.7
MAX_TOKENS = 512
SEED_BASE = 199  # v2: tightened verifier (escape libraries blocked)

# Pivot phrases to scan for in the full response text.
PIVOT_PHRASES = [
    r"let me reconsider",
    r"i'll need to change approach",
    r"let me try a different",
    r"actually[,]? let me",
    r"on second thought",
    r"scratch that",
    r"i need to rethink",
    r"changing my approach",
    r"let me change",
    r"revised approach",
]
_PIVOT_RE = re.compile("|".join(PIVOT_PHRASES), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stable_seed(*parts):
    """Deterministic int32 seed from arbitrary string parts."""
    payload = "|".join([str(SEED_BASE), *map(str, parts)]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "little") & 0x7FFFFFFF


def build_prompt(task, constraint: str) -> str:
    return (
        f"Write a Python function that solves the following task.\n\n"
        f"TASK: {task.description}\n\n"
        f"CONSTRAINT: {constraint}\n\n"
        f"Important: you MUST respect the constraint above.\n"
        f"Provide your solution in a ```python code block."
    )


def split_into_quartile_prefixes(text: str) -> list[str]:
    """
    Split text into 4 prefixes at 25/50/75/100 % of its character length.
    Returns a list of 4 strings (increasing length).
    """
    n = len(text)
    return [text[: max(1, n * q // 4)] for q in range(1, 5)]


# ---------------------------------------------------------------------------
# AST-based forbidden-technique checkers
# ---------------------------------------------------------------------------

def _uses_recursion(code: str, func_name: str) -> bool:
    """Return True if `code` contains a self-recursive call to func_name."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == func_name:
                        return True
    return False


def _uses_loops(code: str) -> bool:
    """Return True if `code` contains any for or while loop."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # If partial / unparseable, check text heuristically.
        return bool(re.search(r'\bfor\b|\bwhile\b', code))
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            return True
    return False


def _uses_builtin_max(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return bool(re.search(r'\bmax\s*\(', code))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "max":
                return True
    return False


def _uses_sum_or_count_or_comprehension(code: str) -> bool:
    """Check sum(), str.count(), and list comprehensions (for count_vowels conflicting)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("sum",):
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr == "count":
                return True
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            return True
    return False


def _uses_if_elif(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return bool(re.search(r'\bif\b|\belif\b', code))
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            return True
    return False


def _uses_escape_library(code: str, banned: set) -> bool:
    """Return True if `code` calls any (module, attr) pair in `banned`.

    Detects:
      - import math; math.factorial(n)
      - from math import factorial; factorial(n)  (matches by attr name)
      - import math as m; m.factorial(n)
      - import numpy as np; np.max(...)
    Also catches bare top-level builtins that would shortcut the task,
    e.g. divmod / sorted / sorted()[-1].
    """
    if not banned:
        return False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Heuristic textual fallback
        for mod, attr in banned:
            if re.search(rf"\b{re.escape(mod)}\s*\.\s*{re.escape(attr)}\b", code):
                return True
            if re.search(rf"\bfrom\s+{re.escape(mod)}\s+import[^\n]*\b{re.escape(attr)}\b", code):
                return True
        return False
    # Build a name->module map from imports.
    alias_to_module = {}
    imported_names = set()  # names brought into local scope via `from X import name`
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                alias_to_module[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    imported_names.add((node.module, alias.asname or alias.name))
    banned_attrs = {attr for _, attr in banned}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            # Pattern: m.attr(...) where alias_to_module[m] is a banned module
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                mod_alias = f.value.id
                attr = f.attr
                real_mod = alias_to_module.get(mod_alias, mod_alias)
                if (real_mod, attr) in banned:
                    return True
            # Pattern: bare name call where (module, name) was from-imported
            if isinstance(f, ast.Name):
                for mod, attr in imported_names:
                    if attr == f.id and (mod, attr) in banned:
                        return True
                # Plus: bare attr name that's also in banned_attrs (defensive — catches
                # cases where the model wrote e.g. `from math import *` then used `factorial(n)`)
                if f.id in banned_attrs:
                    return True
    return False


def _uses_modulo(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "%" in code
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return True
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Mod):
            return True
    return False


# Per-task forbidden-technique checker:  (task_id, condition) -> bool_uses_forbidden(code)
def _make_forbidden_checker(task_id: str, condition: str, func_name: str):
    """
    Return a callable (code: str) -> bool that returns True if the forbidden
    technique IS present in code (i.e., the constraint is VIOLATED).

    Conflicting condition also blocks stdlib escape hatches (math.factorial,
    functools.reduce, etc.) per ESCAPE_LIBRARIES and comprehensions where
    they would short-circuit the no-loops constraint.
    """
    escape_set = ESCAPE_LIBRARIES.get(task_id, set())

    if task_id in ("factorial", "fibonacci", "gcd"):
        if condition == "compatible_forbidden":
            return lambda code: _uses_recursion(code, func_name)
        else:
            # Forbid recursion + loops + comprehensions (loops in disguise) +
            # stdlib shortcut calls.
            return lambda code: (
                _uses_recursion(code, func_name)
                or _uses_loops(code)
                or _uses_comprehensions(code)
                or _uses_escape_library(code, escape_set)
            )

    elif task_id == "find_max":
        if condition == "compatible_forbidden":
            return lambda code: _uses_builtin_max(code)
        else:
            return lambda code: (
                _uses_builtin_max(code)
                or _uses_loops(code)
                or _uses_comprehensions(code)
                or _uses_sorted_indexing(code)
                or _uses_escape_library(code, escape_set)
            )

    elif task_id == "count_vowels":
        if condition == "compatible_forbidden":
            return lambda code: bool(re.search(r'\bfor\b', code))
        else:
            return lambda code: (
                _uses_loops(code)
                or _uses_sum_or_count_or_comprehension(code)
                or _uses_escape_library(code, escape_set)
            )

    elif task_id == "fizzbuzz":
        if condition == "compatible_forbidden":
            return lambda code: _uses_if_elif(code)
        else:
            return lambda code: (
                _uses_if_elif(code)
                or _uses_modulo(code)
                or _uses_divmod(code)
                or _uses_ternary(code)
            )

    return lambda code: False


def _uses_comprehensions(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            return True
    return False


def _uses_sorted_indexing(code: str) -> bool:
    """Detect sorted(...)[-1] / sorted(...)[0] patterns that shortcut max/min."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            v = node.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "sorted":
                return True
    return False


def _uses_divmod(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "divmod" in code
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "divmod":
            return True
    return False


def _uses_ternary(code: str) -> bool:
    """Detect Python ternary `X if cond else Y` (which is an if/elif chain in disguise)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.IfExp):
            return True
    return False


# ---------------------------------------------------------------------------
# OpenRouter call
# ---------------------------------------------------------------------------

def call_openrouter(client, model_id: str, messages: list, seed: int) -> str:
    resp = client.chat.completions.create(
        model=model_id,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        seed=seed,
    )
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# Model availability probe
# ---------------------------------------------------------------------------

def probe_model(client, model_id: str) -> bool:
    """Return True if OpenRouter accepts this model_id for a tiny call."""
    try:
        client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Per-trial worker
# ---------------------------------------------------------------------------

def run_trial(client, resolved_models, task_map, args):
    model_name, task_id, condition, trial = args
    model_id = resolved_models[model_name]
    task = task_map[task_id]
    func_name = task.function_name
    constraint = FORBIDDEN_PAIRS[task_id][0 if condition == "compatible_forbidden" else 1]
    forbidden_checker = _make_forbidden_checker(task_id, condition, func_name)

    seed = stable_seed(model_name, task_id, condition, "pivot", trial)
    t0 = time.time()

    # Single-shot prompt (no staged conversation).
    prompt = build_prompt(task, constraint)
    messages = [{"role": "user", "content": prompt}]

    try:
        final_code = call_openrouter(client, model_id, messages, seed)
    except Exception as e:
        return {
            "model": model_name, "model_id": model_id,
            "task_id": task_id, "condition": condition, "trial": trial,
            "error": f"{type(e).__name__}: {e}",
            "_dt": time.time() - t0,
        }

    # Extract final code block.
    extracted_code_block = extract_code_block(final_code) or ""

    # Quartile prefix extraction.
    prefixes = split_into_quartile_prefixes(final_code)
    extracted_prefix_codes = [extract_code_block(p) or "" for p in prefixes]

    # --- Verifiers (run on FINAL extracted code only) ---
    # Functional pass.
    functional_pass, _msg_func = verify_functional(extracted_code_block, task.test_code)

    # Format pass: simple heuristic — code block exists and is under 50 lines.
    if extracted_code_block:
        n_lines = len(extracted_code_block.strip().splitlines())
        format_pass = n_lines <= 50
    else:
        format_pass = False

    # Forbidden-technique pass: True means constraint is OBEYED (technique not found).
    if extracted_code_block:
        forbidden_technique_pass = not forbidden_checker(extracted_code_block)
    else:
        forbidden_technique_pass = False

    joint_pass = functional_pass and format_pass and forbidden_technique_pass

    # --- Pivot signatures ---
    # forbidden_in_early_prefix_only: forbidden technique in Q1 prefix but NOT in final.
    early_code = extracted_prefix_codes[0]
    if early_code:
        forbidden_in_early = forbidden_checker(early_code)
    else:
        # Heuristic from raw text prefix.
        forbidden_in_early = bool(re.search(r'\bfor\b|\bwhile\b|\bdef\b.*\bdef\b', prefixes[0]))

    forbidden_in_final = not forbidden_technique_pass  # i.e., technique IS present in final.
    forbidden_in_early_prefix_only = bool(forbidden_in_early and not forbidden_in_final)

    # early_prefix_fails_forbidden_check: Q1 prefix uses the forbidden technique.
    early_prefix_fails_forbidden_check = bool(forbidden_in_early)

    # explicit_pivot_phrase: response text mentions a pivot.
    explicit_pivot_phrase = bool(_PIVOT_RE.search(final_code))

    pivot_signatures = {
        "forbidden_in_early_prefix_only": forbidden_in_early_prefix_only,
        "early_prefix_fails_forbidden_check": early_prefix_fails_forbidden_check,
        "explicit_pivot_phrase": explicit_pivot_phrase,
    }

    return {
        "model": model_name,
        "model_id": model_id,
        "task_id": task_id,
        "condition": condition,
        "trial": trial,
        "final_code": final_code,
        "extracted_code_block": extracted_code_block,
        "functional_pass": bool(functional_pass),
        "format_pass": bool(format_pass),
        "forbidden_technique_pass": bool(forbidden_technique_pass),
        "joint_pass": bool(joint_pass),
        "pivot_signatures": pivot_signatures,
        "_dt": time.time() - t0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        from openai import OpenAI
    except ImportError:
        print("openai package not found; pip install openai", file=sys.stderr)
        return 1

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY env var required", file=sys.stderr)
        return 1

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    # Resolve model IDs: probe each premier slot; fall back gracefully.
    print("  Probing model availability...", flush=True)
    resolved_models = {}
    for slot, model_id in MODEL_PANEL.items():
        if probe_model(client, model_id):
            resolved_models[slot] = model_id
            print(f"    {slot}: {model_id} OK", flush=True)
        else:
            fallback_id = FALLBACK_PANEL.get(slot, model_id)
            resolved_models[slot] = fallback_id
            print(f"    {slot}: {model_id} REJECTED -> fallback {fallback_id}", flush=True)

    task_map = {t.task_id: t for t in TASKS}

    out_path = Path(__file__).parent / "openrouter_forbidden_pivot_results.json"
    state = {
        "_meta": {
            "experiment": "openrouter_forbidden_pivot",
            "generator_script": "supplementary/experiments/openrouter_forbidden_pivot_harness.py",
            "models": list(resolved_models.keys()),
            "model_ids": resolved_models,
            "tasks": TASK_IDS,
            "conditions": CONDITIONS,
            "n_trials": N_TRIALS,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "results": [],
    }

    # Resume from existing checkpoint.
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            if "results" in old:
                state["results"] = old["results"]
                print(f"  Resumed: {len(state['results'])} prior results loaded.", flush=True)
        except Exception:
            pass

    # Only successful trials count as done; error entries are retried.
    done_keys = {
        (r["model"], r["task_id"], r["condition"], r["trial"])
        for r in state["results"]
        if "functional_pass" in r
    }
    state["results"] = [r for r in state["results"] if "functional_pass" in r]

    # Build work queue.
    work = []
    for model_name in resolved_models:
        for task_id in TASK_IDS:
            for condition in CONDITIONS:
                for trial in range(N_TRIALS):
                    if (model_name, task_id, condition, trial) in done_keys:
                        continue
                    work.append((model_name, task_id, condition, trial))

    n_total = len(resolved_models) * len(TASK_IDS) * len(CONDITIONS) * N_TRIALS
    n_to_do = len(work)
    n_already = len(state["results"])
    print(
        f"  Plan: {n_total} total cells | {n_already} done | {n_to_do} to run | {N_WORKERS} workers",
        flush=True,
    )
    t_start = time.time()

    def run_cell(args):
        return run_trial(client, resolved_models, task_map, args)

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(run_cell, w) for w in work]
        for fut in as_completed(futs):
            r = fut.result()
            with _counter_lock:
                _counter["done"] += 1
                done_now = _counter["done"]
            with _ckpt_lock:
                state["results"].append(r)
                out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

            tag = (
                f"{r['model']:<18} {r['task_id']:<14} "
                f"{r['condition']:<28} t{r['trial']}"
            )
            if "error" in r:
                msg = f"ERROR: {r['error'][:60]}"
            else:
                ps = r["pivot_signatures"]
                msg = (
                    f"fn{int(r['functional_pass'])} "
                    f"fmt{int(r['format_pass'])} "
                    f"fbd{int(r['forbidden_technique_pass'])} "
                    f"joint{int(r['joint_pass'])} "
                    f"| piv_early{int(ps['forbidden_in_early_prefix_only'])} "
                    f"piv_fail{int(ps['early_prefix_fails_forbidden_check'])} "
                    f"piv_phrase{int(ps['explicit_pivot_phrase'])}"
                )
            elapsed_now = time.time() - t_start
            eta = (elapsed_now / done_now) * (n_to_do - done_now) if done_now else 0
            print(
                f"  [{done_now:3d}/{n_to_do}] {tag} | {msg} | {r['_dt']:.1f}s | ETA {eta/60:.1f}min",
                flush=True,
            )

    elapsed = time.time() - t_start
    state["_meta"]["elapsed_sec"] = elapsed
    state["_meta"]["finished_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Per-(model, condition) summary.
    summary = {}
    for model_name in resolved_models:
        summary[model_name] = {}
        for cond in CONDITIONS:
            rs = [
                r for r in state["results"]
                if r.get("model") == model_name
                and r.get("condition") == cond
                and "functional_pass" in r
            ]
            n = len(rs)
            pivot_any = sum(
                1 for r in rs
                if any(r.get("pivot_signatures", {}).values())
            )
            summary[model_name][cond] = {
                "n": n,
                "functional_pass_rate": round(sum(r["functional_pass"] for r in rs) / max(1, n), 3),
                "forbidden_pass_rate": round(sum(r["forbidden_technique_pass"] for r in rs) / max(1, n), 3),
                "joint_pass_rate": round(sum(r["joint_pass"] for r in rs) / max(1, n), 3),
                "pivot_any_rate": round(pivot_any / max(1, n), 3),
            }
    state["summary"] = summary

    out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\nTotal: {elapsed:.1f}s ({elapsed / max(1, n_total):.1f}s/trial)")
    print(f"\n=== SUMMARY (joint_pass_rate | forbidden_pass_rate | pivot_any_rate) ===")
    print(f"{'model':<18} {'condition':<28} {'n':<4} {'joint%':<8} {'fbd%':<8} {'pivot%':<8}")
    for model_name, conds in summary.items():
        for cond, s in conds.items():
            print(
                f"{model_name:<18} {cond:<28} {s['n']:<4} "
                f"{s['joint_pass_rate']*100:6.1f}%  "
                f"{s['forbidden_pass_rate']*100:6.1f}%  "
                f"{s['pivot_any_rate']*100:6.1f}%"
            )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
