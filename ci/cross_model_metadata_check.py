#!/usr/bin/env python3
"""
cross_model_metadata_check.py - Verify that n_models / n_providers / n_trials
claimed in the paper near fig:cross_model match the JSONs consumed by
plot_cross_model.py.

Reads:
  1. supplementary/experiments_rebuttal/cross_model/plot_cross_model.py
     (to discover which JSON files it loads)
  2. Those JSONs (falling back to two known paths if auto-discovery fails)
  3. paper/main.tex (lines around \\label{fig:cross_model} and the cross_model
     paragraph)

Computes:
  - n_models  : len(unique model IDs across both JSONs)
  - n_providers : len({provider(m) for m in models})  via PROVIDER_MAP
  - n_trials  : sum of all trial counts across both JSONs

Asserts the computed numbers appear in the paper window.

Exit codes
----------
  0  all checks pass
  1  one or more checks fail
  2  invocation / file error (not a logic failure)

Output
------
  ci/cross_model_metadata_results.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
PLOT_SCRIPT = (
    REPO_ROOT
    / "supplementary"
    / "experiments_rebuttal"
    / "cross_model"
    / "plot_cross_model.py"
)
FALLBACK_JSON_SMALL = (
    REPO_ROOT / "supplementary" / "experiments" / "code_constraint_results.json"
)
FALLBACK_JSON_FRONTIER = REPO_ROOT / "rebuttal" / "figures" / "cross_model_results.json"
PAPER_TEX = REPO_ROOT / "paper" / "main.tex"
RESULTS_OUT = REPO_ROOT / "ci" / "cross_model_metadata_results.json"

# ---------------------------------------------------------------------------
# Provider mapping  (prefix rules, longest match wins)
# ---------------------------------------------------------------------------
PROVIDER_MAP: list[tuple[str, str]] = [
    # (lowercase prefix_or_substring, provider_name)
    # More-specific patterns MUST appear before substrings they contain.
    # e.g. "tinyllama" before "llama", "deepseek-ai/" before "deepseek".
    ("openai/", "OpenAI"),
    ("gpt", "OpenAI"),
    ("google/", "Google"),
    ("gemini", "Google"),
    ("tinyllama", "TinyLlama"),   # before "llama" — TinyLlama is its own org
    ("meta-llama/", "Meta"),
    ("llama", "Meta"),
    ("deepseek-ai/", "DeepSeek"),
    ("deepseek/", "DeepSeek"),
    ("deepseek", "DeepSeek"),
    ("qwen", "Alibaba"),
    ("mistral", "Mistral"),
    ("command", "Cohere"),
    ("claude", "Anthropic"),
    ("anthropic/", "Anthropic"),
]


def provider_of(model_id: str) -> str:
    """Map a model_id string to its provider name."""
    m = model_id.lower()
    for prefix, name in PROVIDER_MAP:
        if prefix in m:
            return name
    return "Unknown"


# ---------------------------------------------------------------------------
# Step 1: discover JSON paths from plot_cross_model.py
# ---------------------------------------------------------------------------
def discover_json_paths() -> list[Path]:
    """
    Parse plot_cross_model.py for REPO_ROOT / ... / "*.json" constructs.
    Returns absolute Paths.  Falls back to known paths on any parse failure.
    """
    fallbacks = [FALLBACK_JSON_SMALL, FALLBACK_JSON_FRONTIER]
    if not PLOT_SCRIPT.exists():
        print(
            f"[WARN] plot_cross_model.py not found at {PLOT_SCRIPT}; "
            "using fallback paths.",
            file=sys.stderr,
        )
        return fallbacks

    source = PLOT_SCRIPT.read_text(encoding="utf-8")

    # Match patterns like:
    #   REPO_ROOT / "supplementary" / "experiments" / "code_constraint_results.json"
    #   REPO_ROOT / "rebuttal" / "figures" / "cross_model_results.json"
    pat = re.compile(
        r'REPO_ROOT\s*/(?:\s*"([^"]+)"\s*/)*\s*"([^"]+\.json)"'
    )
    found: list[Path] = []
    for m in pat.finditer(source):
        # Reconstruct path from the parts captured
        # Use a broader approach: find all quoted segments in each match span
        span_text = source[m.start() : m.end()]
        parts = re.findall(r'"([^"]+)"', span_text)
        if parts:
            p = REPO_ROOT
            for part in parts:
                p = p / part
            if p not in found:
                found.append(p)

    if not found:
        print(
            "[WARN] Could not auto-discover JSON paths from plot_cross_model.py; "
            "using fallback paths.",
            file=sys.stderr,
        )
        return fallbacks

    return found


# ---------------------------------------------------------------------------
# Step 2: load and count from JSONs
# ---------------------------------------------------------------------------
def load_small_json(path: Path) -> tuple[list[str], int]:
    """
    code_constraint_results.json structure:
      { model_id: {"results": [...], ...}, "_meta": {...} }

    Returns (model_ids, total_trials).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    model_ids = []
    total = 0
    for k, v in data.items():
        if k == "_meta":
            continue
        # v is either {"results": [...], ...}  or  a list directly
        if isinstance(v, dict) and "results" in v:
            trials = v["results"]
        elif isinstance(v, list):
            trials = v
        else:
            trials = []
        model_ids.append(k)
        total += len(trials)
    return model_ids, total


def load_frontier_json(path: Path) -> tuple[list[str], int]:
    """
    cross_model_results.json structure:
      { "results": [{"model_id": "openai/gpt-4o", ...}, ...], "total_results": N, ... }

    Returns (model_ids_unique, total_trials).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    results = data.get("results", [])
    model_ids = list({r["model_id"] for r in results if "model_id" in r})
    total = len(results)
    return model_ids, total


def compute_metadata(json_paths: list[Path]) -> dict[str, Any]:
    """
    Given the two JSON paths (in discovery order: small first, frontier second),
    compute n_models, n_providers, n_trials.
    """
    all_models: list[str] = []
    total_trials = 0
    data_files: list[str] = []
    errors: list[str] = []

    for path in json_paths:
        data_files.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
        if not path.exists():
            errors.append(f"Missing: {path}")
            continue

        try:
            # Decide format by path name
            if "code_constraint" in path.name:
                ids, count = load_small_json(path)
            else:
                ids, count = load_frontier_json(path)
            all_models.extend(ids)
            total_trials += count
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Error reading {path}: {exc}")

    unique_models = list(dict.fromkeys(all_models))  # preserve discovery order
    provider_set = sorted({provider_of(m) for m in unique_models})

    return {
        "n_models": len(unique_models),
        "n_providers": len(provider_set),
        "n_trials": total_trials,
        "model_list": unique_models,
        "provider_set": provider_set,
        "data_files": data_files,
        "load_errors": errors,
    }


# ---------------------------------------------------------------------------
# Step 3: extract paper window around fig:cross_model
# ---------------------------------------------------------------------------
def extract_paper_window(
    tex_path: Path,
    context_lines: int = 10,
    fixed_start: int = 510,
    fixed_end: int = 540,
) -> tuple[list[tuple[int, str]], str]:
    """
    Returns (lines_with_numbers, combined_text).

    Strategy: union of
      (a) fixed line range [fixed_start..fixed_end] (1-indexed)
      (b) every line containing 'fig:cross_model' plus ±context_lines
    """
    all_lines = tex_path.read_text(encoding="utf-8").splitlines()
    n = len(all_lines)

    selected: set[int] = set()

    # (a) fixed window
    for i in range(fixed_start - 1, min(fixed_end, n)):
        selected.add(i)

    # (b) context around label occurrences
    for i, line in enumerate(all_lines):
        if "fig:cross_model" in line:
            lo = max(0, i - context_lines)
            hi = min(n - 1, i + context_lines)
            for j in range(lo, hi + 1):
                selected.add(j)

    ordered = sorted(selected)
    lines_with_nums = [(idx + 1, all_lines[idx]) for idx in ordered]
    combined = "\n".join(t for _, t in lines_with_nums)
    return lines_with_nums, combined


# ---------------------------------------------------------------------------
# Step 4: normalize and search
# ---------------------------------------------------------------------------
def normalize_thousands(text: str) -> str:
    r"""
    Normalize LaTeX thousand separators to plain digits.

    4{,}120  -> 4120
    4\,120   -> 4120
    4,120    -> 4120   (only when surrounded by digits)
    """
    # {,} or \, between digit groups
    text = re.sub(r"(\d)\{,\}(\d)", r"\1\2", text)
    text = re.sub(r"(\d)\\,(\d)", r"\1\2", text)
    return text


def find_number_in_window(
    n: int,
    lines_with_nums: list[tuple[int, str]],
) -> tuple[bool, list[int]]:
    """
    Search for the integer n (as bare digits) in the normalized paper window.
    Returns (found, [line_numbers_where_found]).
    """
    target = str(n)
    found_lines = []
    for lineno, raw in lines_with_nums:
        normalized = normalize_thousands(raw)
        # Look for the number as a word-bounded token
        if re.search(r"(?<!\d)" + re.escape(target) + r"(?!\d)", normalized):
            found_lines.append(lineno)
    return bool(found_lines), found_lines


# ---------------------------------------------------------------------------
# Step 5: assemble and report
# ---------------------------------------------------------------------------
def find_phrase_in_window(value: int, noun: str, paper_window: list[tuple[int, str]]) -> tuple[bool, list[int]]:
    """Stricter than find_number_in_window: requires the digit to appear adjacent to the noun.

    Examples that match value=6 noun='providers':
      "6 providers"  "6\\,providers"  "6{,}000 providers"  "(6 providers)"
    Examples that DO NOT match (caught drift):
      "5 providers" near digit 6 in "$N{=}3{,}120$" elsewhere on the line.
    """
    found_lines: list[int] = []
    pat = re.compile(r"(?<!\d)" + re.escape(str(value)) + r"(?!\d)\s*" + re.escape(noun))
    for lineno, raw in paper_window:
        normalized = normalize_thousands(raw)
        if pat.search(normalized):
            found_lines.append(lineno)
    return bool(found_lines), found_lines


def run_checks(computed: dict[str, Any], paper_window: list[tuple[int, str]]) -> list[dict[str, Any]]:
    checks = []
    # Phrase-locality checks: require value adjacent to its noun.
    for key, label, noun in [
        ("n_models", "n_models_in_paper_window", "models"),
        ("n_providers", "n_providers_in_paper_window", "providers"),
    ]:
        value = computed[key]
        passed, found_lines = find_phrase_in_window(value, noun, paper_window)
        checks.append({
            "name": label,
            "passed": passed,
            f"expected_{key}": value,
            "found_at_lines": found_lines,
            "detail": (
                f"Found phrase '{value} {noun}' at line(s) {found_lines}"
                if passed else
                f"computed {key}={value} not found adjacent to '{noun}' in paper window "
                f"(lines {paper_window[0][0]}..{paper_window[-1][0]})"
            ),
        })
    # n_trials: free-floating digit search since N can render as $N{=}3{,}120$ etc.
    value = computed["n_trials"]
    passed, found_lines = find_number_in_window(value, paper_window)
    checks.append({
        "name": "n_trials_in_paper_window",
        "passed": passed,
        "expected_n_trials": value,
        "found_at_lines": found_lines,
        "detail": (
            f"Found '{value}' at line(s) {found_lines}"
            if passed else
            f"computed n_trials={value} not found in paper window "
            f"(lines {paper_window[0][0]}..{paper_window[-1][0]})"
        ),
    })
    return checks


def build_report(
    computed: dict[str, Any],
    checks: list[dict[str, Any]],
    paper_window_range: tuple[int, int],
) -> dict[str, Any]:
    total = len(checks)
    passed = sum(1 for c in checks if c["passed"])
    failed = total - passed
    return {
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "overall": "PASS" if failed == 0 else "FAIL",
            "paper_window_lines": list(paper_window_range),
        },
        "computed": computed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify cross-model metadata in paper matches source JSONs."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any failure (default: always exit 1 on failure).",
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Skip writing results JSON (useful in dry-run tests).",
    )
    args = parser.parse_args()

    # Guard: paper must exist
    if not PAPER_TEX.exists():
        print(f"[ERROR] paper/main.tex not found at {PAPER_TEX}", file=sys.stderr)
        return 2

    # Step 1: discover
    try:
        json_paths = discover_json_paths()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] path discovery failed: {exc}", file=sys.stderr)
        return 2

    # Step 2: compute
    try:
        computed = compute_metadata(json_paths)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] metadata computation failed: {exc}", file=sys.stderr)
        return 2

    if computed["load_errors"]:
        for err in computed["load_errors"]:
            print(f"[ERROR] {err}", file=sys.stderr)
        return 2

    # Step 3: paper window
    try:
        paper_lines, _combined = extract_paper_window(PAPER_TEX)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] could not read paper tex: {exc}", file=sys.stderr)
        return 2

    # Step 4: checks
    checks = run_checks(computed, paper_lines)

    # Step 5: report
    window_range = (paper_lines[0][0], paper_lines[-1][0]) if paper_lines else (0, 0)
    report = build_report(computed, checks, window_range)

    # Print summary
    print(f"\n=== cross_model_metadata_check ===")
    print(
        f"Computed from JSONs: n_models={computed['n_models']}, "
        f"n_providers={computed['n_providers']}, n_trials={computed['n_trials']}"
    )
    print(f"Models  : {computed['model_list']}")
    print(f"Providers: {computed['provider_set']}")
    print(f"Paper window: lines {window_range[0]}..{window_range[1]}")
    print()
    for c in checks:
        status = "PASS" if c["passed"] else "FAIL"
        print(f"  [{status}] {c['name']}: {c['detail']}")

    overall = report["summary"]["overall"]
    print(f"\nOverall: {overall}")

    # Write output JSON
    if not args.no_output:
        RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_OUT.write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"Results written to: {RESULTS_OUT.relative_to(REPO_ROOT)}")

    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
