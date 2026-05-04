#!/usr/bin/env python3
"""
openrouter_described_image_pivot_harness.py

Sibling to openrouter_forbidden_pivot_harness.py: same forbidden-technique
pivot question, but in the IMAGE-DESCRIPTION domain instead of code.

Why text-described-image rather than real image generation:
- Real image-gen breaks the deterministic-verifier contract (image inspection
  requires manual rubric or OCR).
- Text-described-image preserves judge-free verification: "model must produce
  a description of an image that does not contain forbidden words" is
  exactly what regex can verify.
- Pivot signature is preserved: model can mention forbidden word early in
  the description, then abandon it in the final answer.

Spec:
- 6 models (same panel as forbidden-pivot code harness)
- 3 image-description tasks
- 2 conditions: compatible vs conflicting forbidden-word sets
- 3 trials per cell
- 6 x 3 x 2 x 3 = 108 calls

Usage:
    $env:OPENROUTER_API_KEY = "sk-or-..."
    python openrouter_described_image_pivot_harness.py
"""
import json
import os
import sys
import time
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

N_WORKERS = 8
SEED_BASE = 42
TEMPERATURE = 0.7
MAX_TOKENS = 600

_ckpt_lock = threading.Lock()
_counter_lock = threading.Lock()
_counter = {"done": 0}

# Same panel as the code-domain harness for direct comparison.
MODEL_PANEL = {
    "premier_1":       "anthropic/claude-opus-4.5",
    "premier_2":       "openai/gpt-5",
    "cheap_current_1": "anthropic/claude-3.5-haiku",
    "cheap_current_2": "openai/gpt-4o-mini",
    "legacy_1":        "openai/gpt-4o",
    "legacy_2":        "google/gemini-2.5-flash",
}
FALLBACK_PANEL = {
    "premier_1":       "anthropic/claude-sonnet-4.5",
    "premier_2":       "openai/gpt-4o",
    "cheap_current_1": "anthropic/claude-3.5-haiku",
    "cheap_current_2": "openai/gpt-4o-mini",
    "legacy_1":        "openai/gpt-4o",
    "legacy_2":        "google/gemini-2.5-flash",
}

# Three image-description tasks. Each carries:
#   subject: what the model is asked to describe
#   forbid_compat: short list of forbidden-words for the COMPATIBLE condition
#                  (a clear default-vocabulary pivot exists)
#   forbid_conflict: longer list for the CONFLICTING condition (predicted
#                    near-infeasible: nearly all natural vocabulary is blocked)
TASKS = [
    {
        "id": "sunset",
        "subject": "a sunset over the ocean",
        "forbid_compat": ["orange"],          # easy pivot: pink/red/coral
        "forbid_conflict": ["orange", "yellow", "red", "pink", "gold", "amber"],
    },
    {
        "id": "forest",
        "subject": "a forest scene with morning light",
        "forbid_compat": ["green"],           # easy pivot: brown trunks, golden mist
        "forbid_conflict": ["green", "tree", "leaf", "leaves", "branch", "trunk"],
    },
    {
        "id": "city_grid",
        "subject": "a busy city intersection at night",
        "forbid_compat": ["car"],             # easy pivot: pedestrians, bikes, lights
        "forbid_conflict": ["car", "person", "people", "light", "lights", "traffic"],
    },
]

CONDITIONS = ["compatible_forbidden_words", "conflicting_forbidden_words"]
N_TRIALS = 3


def stable_seed(*parts) -> int:
    payload = "|".join([str(SEED_BASE), *map(str, parts)]).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "little") & 0x7FFFFFFF


def build_prompt(task: dict, condition: str) -> str:
    forbid = task["forbid_compat"] if condition == "compatible_forbidden_words" else task["forbid_conflict"]
    forbid_str = ", ".join(f'"{w}"' for w in forbid)
    return (
        f"Describe in vivid prose an image of {task['subject']}. "
        f"Aim for 80-200 words of pure visual description. "
        f"Strict constraint: do not use any of the following words (or their direct plurals/inflections) "
        f"anywhere in your description: {forbid_str}. "
        f"If you must change your approach mid-description, you may; but the final text must obey the constraint."
    )


def call_openrouter(client, model_id: str, prompt: str, seed: int) -> str:
    resp = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        seed=seed,
    )
    return resp.choices[0].message.content or ""


def check_forbidden_words(text: str, forbidden: list[str]) -> dict:
    """Word-boundary regex search for each forbidden word.

    Returns:
        violation_count: how many forbidden words appear at all
        violations: list of {word, count, first_offset_pct}
    """
    violations = []
    text_lower = text.lower()
    text_len = max(1, len(text))
    for w in forbidden:
        # Match word with optional plural/possessive. Word boundary on both sides.
        pattern = r"\b" + re.escape(w.lower()) + r"(?:s|es|'s|ing)?\b"
        matches = list(re.finditer(pattern, text_lower))
        if matches:
            violations.append({
                "word": w,
                "count": len(matches),
                "first_offset_pct": round(100 * matches[0].start() / text_len, 1),
            })
    return {
        "violation_count": sum(v["count"] for v in violations),
        "violations": violations,
        "passed": len(violations) == 0,
    }


def detect_pivot_signatures(text: str, forbidden: list[str]) -> dict:
    """Pivot signatures specific to text-described-image task.

    A pivot is when the model uses a forbidden word in the early prefix
    but not in the final description, OR when the model explicitly says
    it's changing approach.
    """
    n = len(text)
    if n < 40:
        return {
            "forbidden_in_first_quartile_only": False,
            "forbidden_in_final_quartile": False,
            "explicit_pivot_phrase": False,
        }
    q1 = text[: n // 4]
    q4 = text[3 * n // 4 :]
    forbidden_lower = [w.lower() for w in forbidden]

    def any_word_in(s: str) -> bool:
        sl = s.lower()
        return any(re.search(r"\b" + re.escape(w) + r"(?:s|es|'s|ing)?\b", sl) for w in forbidden_lower)

    in_q1 = any_word_in(q1)
    in_q4 = any_word_in(q4)
    pivot_phrases = [
        "let me try", "let me reconsider", "actually,", "on second thought",
        "rather than", "instead of", "I'll change", "let me start over",
        "I should avoid", "to comply", "without using",
    ]
    text_lower = text.lower()
    explicit = any(p in text_lower for p in pivot_phrases)

    return {
        "forbidden_in_first_quartile_only": in_q1 and not in_q4,
        "forbidden_in_final_quartile": in_q4,
        "explicit_pivot_phrase": explicit,
    }


def probe_model_id(client, model_id: str) -> bool:
    try:
        client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return True
    except Exception:
        return False


def resolve_model_panel(client) -> dict[str, str]:
    """Probe each panel slot; fall back per-slot if the primary ID is rejected."""
    resolved = {}
    for slot, primary in MODEL_PANEL.items():
        if probe_model_id(client, primary):
            resolved[slot] = primary
        else:
            fb = FALLBACK_PANEL[slot]
            print(f"  [probe] {slot}: '{primary}' rejected; falling back to '{fb}'", flush=True)
            if probe_model_id(client, fb):
                resolved[slot] = fb
            else:
                print(f"  [probe] {slot}: fallback '{fb}' also rejected; SKIPPING slot", flush=True)
                resolved[slot] = None
    return {k: v for k, v in resolved.items() if v is not None}


def main() -> int:
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

    print("Probing model panel...", flush=True)
    resolved = resolve_model_panel(client)
    print(f"Resolved {len(resolved)}/{len(MODEL_PANEL)} model slots:", flush=True)
    for slot, mid in resolved.items():
        print(f"  {slot}: {mid}", flush=True)

    out_path = Path(__file__).parent / "openrouter_described_image_pivot_results.json"
    state = {"_meta": {
        "experiment": "openrouter_described_image_pivot",
        "model_slots": resolved,
        "tasks": [t["id"] for t in TASKS],
        "conditions": CONDITIONS,
        "n_trials_per_cell": N_TRIALS,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verification_note": "Deterministic regex word-boundary check on forbidden words; no LLM-judge.",
        "generator_script": "supplementary/experiments/openrouter_described_image_pivot_harness.py",
    }, "results": []}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            if "results" in old:
                state["results"] = [r for r in old["results"] if "forbidden_check" in r]
                print(f"  Resumed: {len(state['results'])} prior trials kept.", flush=True)
        except Exception:
            pass

    done_keys = {(r["model_slot"], r["task_id"], r["condition"], r["trial"]) for r in state["results"]}

    work = []
    for slot, model_id in resolved.items():
        for task in TASKS:
            for condition in CONDITIONS:
                for trial in range(N_TRIALS):
                    if (slot, task["id"], condition, trial) in done_keys:
                        continue
                    work.append((slot, model_id, task, condition, trial))
    n_total = len(resolved) * len(TASKS) * len(CONDITIONS) * N_TRIALS
    n_to_do = len(work)
    print(f"  Plan: {n_total} cells, {n_to_do} to run, {N_WORKERS} workers", flush=True)
    t_start = time.time()

    def run_cell(args):
        slot, model_id, task, condition, trial = args
        seed = stable_seed(slot, task["id"], condition, trial)
        t_t = time.time()
        forbidden = task["forbid_compat"] if condition == "compatible_forbidden_words" else task["forbid_conflict"]
        prompt = build_prompt(task, condition)
        try:
            response = call_openrouter(client, model_id, prompt, seed)
        except Exception as e:
            return {
                "model_slot": slot, "model_id": model_id, "task_id": task["id"],
                "condition": condition, "trial": trial,
                "error": f"<<ERROR: {type(e).__name__}: {e}>>",
                "_dt": time.time() - t_t,
            }
        check = check_forbidden_words(response, forbidden)
        pivot = detect_pivot_signatures(response, forbidden)
        return {
            "model_slot": slot, "model_id": model_id, "task_id": task["id"],
            "condition": condition, "trial": trial,
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "response": response,
            "response_chars": len(response),
            "forbidden_words": forbidden,
            "forbidden_check": check,
            "pivot_signatures": pivot,
            "joint_pass": check["passed"],
            "_dt": time.time() - t_t,
        }

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
            tag = f"{r['model_slot']:<16} {r['task_id']:<10} {r['condition'][:7]:<8} t{r['trial']}"
            if "error" in r:
                msg = "ERROR"
            else:
                fc = r["forbidden_check"]
                ps = r["pivot_signatures"]
                msg = (
                    f"violations={fc['violation_count']:<2} pass={int(fc['passed'])} "
                    f"q1only={int(ps['forbidden_in_first_quartile_only'])} "
                    f"q4hit={int(ps['forbidden_in_final_quartile'])} "
                    f"explicit_pivot={int(ps['explicit_pivot_phrase'])}"
                )
            elapsed = time.time() - t_start
            eta = (elapsed / done_now) * (n_to_do - done_now) if done_now else 0
            print(f"  [{done_now:3d}/{n_to_do}] {tag} | {msg} | {r['_dt']:.1f}s | ETA {eta/60:.1f}min", flush=True)

    elapsed = time.time() - t_start
    state["_meta"]["elapsed_sec"] = elapsed
    state["_meta"]["finished_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Summary: pivot rate, joint pass rate, silent refutation count per slot.
    summary = {}
    for slot in resolved:
        for cond in CONDITIONS:
            rs = [r for r in state["results"]
                  if r.get("model_slot") == slot and r.get("condition") == cond
                  and "forbidden_check" in r]
            n = len(rs)
            if n == 0:
                continue
            pivot_rate = sum(1 for r in rs if any(r["pivot_signatures"].values())) / n
            joint_pass_rate = sum(1 for r in rs if r["joint_pass"]) / n
            # Silent refutation: joint_pass=True (no forbidden word in final) BUT no pivot signature in early text.
            # This would be the unexpected case: the model just complied perfectly without any pivot trace.
            silent_compliance = sum(
                1 for r in rs
                if r["joint_pass"] and not any(r["pivot_signatures"].values())
            )
            summary[f"{slot}/{cond}"] = {
                "n": n,
                "joint_pass_rate": round(joint_pass_rate, 3),
                "pivot_signature_rate": round(pivot_rate, 3),
                "silent_compliance": silent_compliance,
            }
    state["summary"] = summary
    out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\nTotal: {elapsed:.1f}s ({elapsed/n_total:.1f}s/trial)")
    print("\n=== Summary (slot/condition: n, joint_pass_rate, pivot_rate, silent_compliance) ===")
    for k, v in summary.items():
        print(f"  {k:<35} n={v['n']:<2} pass={v['joint_pass_rate']:.2f} pivot={v['pivot_signature_rate']:.2f} silent={v['silent_compliance']}")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
