#!/usr/bin/env python3
"""
Prompt-Length Sweep: Does rho_hat ordering degrade with longer/denser constraint descriptions?

Directly addresses Reviewer C5UW Q3:
  "As the ambient embedding dimension saturates with excessively long or
   semantically dense prompts, does the resolution of your cosine-based
   proxy degrade?"

Design:
  - Take constraint pairs from code constraint tiers (known outcomes)
  - Create 4 verbosity levels: minimal / standard / verbose / very_verbose
  - Compute rho_hat at each level
  - Measure Spearman correlation of rho_hat ordering across verbosity levels
  - Generate figure showing ordering stability

Output: rebuttal/figures/prompt_length_sweep.png
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple

import numpy as np

try:
    from scipy.stats import spearmanr
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

from sentence_transformers import SentenceTransformer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIGURES_DIR = REPO_ROOT / "rebuttal" / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Constraint definitions at 4 verbosity levels
# These match the code constraint tiers from the paper (Section 5)
# ---------------------------------------------------------------------------

CONSTRAINT_SETS = {
    "control": {
        "functional": {
            "minimal": "pass all tests",
            "standard": "The generated code must pass all provided unit tests.",
            "verbose": "The generated Python code must correctly implement the specified function such that it passes all provided unit tests. Each test case must return the expected output for the given inputs without raising any exceptions.",
            "very_verbose": "The generated Python code must correctly and completely implement the specified function according to its docstring specification, such that when executed against the complete suite of provided unit tests, every single test case passes successfully. The implementation must handle all edge cases described in the test suite, return exactly the expected output type and value for each input combination, and must not raise any unhandled exceptions, assertion errors, or produce any side effects that would cause test failures.",
        },
        "format": {
            "minimal": "max 50 lines",
            "standard": "Code must be at most 50 lines.",
            "verbose": "The generated code must not exceed 50 lines in total length, counting all lines including comments, blank lines, and import statements.",
            "very_verbose": "The total line count of the generated Python code, when measured by splitting the output on newline characters and counting all resulting lines including but not limited to executable statements, comments, docstrings, blank lines, import statements, and decorators, must not exceed fifty (50) lines. Any output exceeding this limit will be considered a formatting violation regardless of functional correctness.",
        },
    },
    "low": {
        "functional": {
            "minimal": "pass all tests",
            "standard": "The generated code must pass all provided unit tests.",
            "verbose": "The generated Python code must correctly implement the specified function such that it passes all provided unit tests. Each test case must return the expected output for the given inputs without raising any exceptions.",
            "very_verbose": "The generated Python code must correctly and completely implement the specified function according to its docstring specification, such that when executed against the complete suite of provided unit tests, every single test case passes successfully. The implementation must handle all edge cases described in the test suite, return exactly the expected output type and value for each input combination, and must not raise any unhandled exceptions, assertion errors, or produce any side effects that would cause test failures.",
        },
        "format": {
            "minimal": "max 25 lines, no imports",
            "standard": "Code must be at most 25 lines with no import statements.",
            "verbose": "The generated code must not exceed 25 lines and must not contain any import statements. All functionality must be implemented using only Python built-in functions and operators.",
            "very_verbose": "The total line count of the generated Python code must not exceed twenty-five (25) lines, and the code must not contain any import statements whatsoever, including but not limited to standard library imports, third-party package imports, relative imports, or from-import statements. All required functionality must be implemented exclusively using Python's built-in functions, operators, data structures, and language constructs without relying on any external modules or libraries.",
        },
    },
    "moderate": {
        "functional": {
            "minimal": "pass all tests",
            "standard": "The generated code must pass all provided unit tests.",
            "verbose": "The generated Python code must correctly implement the specified function such that it passes all provided unit tests. Each test case must return the expected output for the given inputs without raising any exceptions.",
            "very_verbose": "The generated Python code must correctly and completely implement the specified function according to its docstring specification, such that when executed against the complete suite of provided unit tests, every single test case passes successfully. The implementation must handle all edge cases described in the test suite, return exactly the expected output type and value for each input combination, and must not raise any unhandled exceptions, assertion errors, or produce any side effects that would cause test failures.",
        },
        "format": {
            "minimal": "max 15 lines",
            "standard": "Code must be at most 15 lines.",
            "verbose": "The generated code must not exceed 15 lines in total, a tight constraint that requires concise implementation strategies such as list comprehensions, ternary operators, or compact control flow.",
            "very_verbose": "The total line count of the generated Python code must not exceed fifteen (15) lines when measured by splitting on newline characters. This tight constraint requires highly concise implementation strategies, potentially including but not limited to the use of list comprehensions, dictionary comprehensions, generator expressions, ternary conditional operators, compact control flow structures, and single-line function definitions. Any output exceeding this strict limit will be considered a formatting violation.",
        },
    },
    "high": {
        "functional": {
            "minimal": "pass all tests",
            "standard": "The generated code must pass all provided unit tests.",
            "verbose": "The generated Python code must correctly implement the specified function such that it passes all provided unit tests. Each test case must return the expected output for the given inputs without raising any exceptions.",
            "very_verbose": "The generated Python code must correctly and completely implement the specified function according to its docstring specification, such that when executed against the complete suite of provided unit tests, every single test case passes successfully. The implementation must handle all edge cases described in the test suite, return exactly the expected output type and value for each input combination, and must not raise any unhandled exceptions, assertion errors, or produce any side effects that would cause test failures.",
        },
        "format": {
            "minimal": "max 10 lines, no loops",
            "standard": "Code must be at most 10 lines with no loop constructs.",
            "verbose": "The generated code must not exceed 10 lines and must not use any loop constructs including for loops, while loops, or list comprehensions that iterate over sequences. Recursion is permitted.",
            "very_verbose": "The total line count of the generated Python code must not exceed ten (10) lines, and the code must not contain any iterative loop constructs whatsoever, including but not limited to for loops, while loops, do-while patterns, list comprehensions with iteration, dictionary comprehensions with iteration, generator expressions with iteration, or any other construct that repeatedly executes a block of code over a sequence or until a condition is met. Recursive function calls are permitted as an alternative to iteration. This combination of extreme brevity and structural restriction creates significant tension with functional correctness requirements.",
        },
    },
}

VERBOSITY_LEVELS = ["minimal", "standard", "verbose", "very_verbose"]
TIERS = ["control", "low", "moderate", "high"]


def compute_rho_hat(model: SentenceTransformer, text_a: str, text_b: str) -> float:
    """Compute rho_hat (cosine similarity) between two constraint texts."""
    embeddings = model.encode([text_a, text_b], normalize_embeddings=True)
    return float(np.dot(embeddings[0], embeddings[1]))


def run_sweep(model_name: str = "all-MiniLM-L6-v2") -> Dict:
    """Run the prompt-length sweep experiment."""
    print(f"Loading model: {model_name}")
    model = SentenceTransformer(model_name)

    results = {}

    for verbosity in VERBOSITY_LEVELS:
        rho_values = []
        for tier in TIERS:
            func_text = CONSTRAINT_SETS[tier]["functional"][verbosity]
            fmt_text = CONSTRAINT_SETS[tier]["format"][verbosity]
            rho = compute_rho_hat(model, func_text, fmt_text)
            rho_values.append(rho)
            print(f"  {verbosity:>14s} | {tier:>10s} | rho_hat = {rho:.4f} "
                  f"(func: {len(func_text)} chars, fmt: {len(fmt_text)} chars)")

        results[verbosity] = {
            "rho_values": rho_values,
            "char_lengths": {
                tier: {
                    "functional": len(CONSTRAINT_SETS[tier]["functional"][verbosity]),
                    "format": len(CONSTRAINT_SETS[tier]["format"][verbosity]),
                }
                for tier in TIERS
            },
        }

    # Compute ordering stability
    baseline_order = np.argsort(results["standard"]["rho_values"])
    stability = {}
    for verbosity in VERBOSITY_LEVELS:
        current_order = np.argsort(results[verbosity]["rho_values"])
        order_match = np.array_equal(baseline_order, current_order)

        if HAS_SCIPY:
            r_s, p_val = spearmanr(
                results["standard"]["rho_values"],
                results[verbosity]["rho_values"],
            )
            stability[verbosity] = {
                "order_preserved": bool(order_match),
                "spearman_r": float(r_s),
                "p_value": float(p_val),
            }
        else:
            stability[verbosity] = {
                "order_preserved": bool(order_match),
            }

    results["stability"] = stability
    return results


def generate_figure(results: Dict, output_path: Path):
    """Generate the prompt-length sweep figure."""
    if not HAS_MATPLOTLIB:
        print("matplotlib not available, skipping figure generation")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    # Left panel: rho_hat by tier across verbosity levels
    colors = ["#2196F3", "#4CAF50", "#FF9800", "#F44336"]
    x_positions = np.arange(len(VERBOSITY_LEVELS))
    bar_width = 0.2

    for i, tier in enumerate(TIERS):
        rho_vals = [results[v]["rho_values"][i] for v in VERBOSITY_LEVELS]
        ax1.bar(x_positions + i * bar_width, rho_vals, bar_width,
                label=f"{tier} (ρ={[0.05, 0.20, 0.40, 0.65][i]})",
                color=colors[i], alpha=0.85)

    ax1.set_xlabel("Constraint Verbosity Level", fontsize=11)
    ax1.set_ylabel("ρ̂ (cosine similarity)", fontsize=11)
    ax1.set_title("Proxy Stability Across Prompt Length", fontsize=12, fontweight="bold")
    ax1.set_xticks(x_positions + 1.5 * bar_width)
    ax1.set_xticklabels(["Minimal\n(~15 chars)", "Standard\n(~50 chars)",
                          "Verbose\n(~200 chars)", "Very Verbose\n(~550 chars)"],
                         fontsize=9)
    ax1.legend(fontsize=9, loc="upper left")
    ax1.grid(axis="y", alpha=0.3)

    # Right panel: ordering stability (Spearman r_s vs standard)
    if "stability" in results and HAS_SCIPY:
        verbosities = VERBOSITY_LEVELS
        r_s_values = [results["stability"][v]["spearman_r"] for v in verbosities]
        order_preserved = [results["stability"][v]["order_preserved"] for v in verbosities]

        bar_colors = ["#2196F3" if op else "#FF9800" for op in order_preserved]
        ax2.bar(range(len(verbosities)), r_s_values, color=bar_colors, alpha=0.85)
        ax2.set_xlabel("Constraint Verbosity Level", fontsize=11)
        ax2.set_ylabel("Spearman r_s vs. Standard", fontsize=11)
        ax2.set_title("Ordering Stability (r_s)", fontsize=12, fontweight="bold")
        ax2.set_xticks(range(len(verbosities)))
        ax2.set_xticklabels(["Minimal", "Standard", "Verbose", "Very Verbose"], fontsize=9)
        ax2.set_ylim(0, 1.25)
        ax2.axhline(y=1.0, color="gray", linestyle="--", alpha=0.5, label="Perfect ordering")
        ax2.axhline(y=0.94, color="blue", linestyle=":", alpha=0.5, label="Encoder invariance threshold")

        for i, (rs, op) in enumerate(zip(r_s_values, order_preserved)):
            if op:
                marker = "exact"
            else:
                marker = "1 swap"
            label = f"$r_s$={rs:.2f}\n({marker})"
            ax2.text(i, rs + 0.03, label, ha="center", fontsize=9, fontweight="bold")

        ax2.legend(fontsize=8, loc="upper right")
        ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Figure saved to {output_path}")
    plt.close()


def main():
    print("=" * 60)
    print("Prompt-Length Sweep Experiment")
    print("Addressing C5UW Q3: proxy degradation with dense prompts")
    print("=" * 60)

    results = run_sweep()

    # Print summary
    print("\n" + "=" * 60)
    print("ORDERING STABILITY SUMMARY")
    print("=" * 60)
    for verbosity in VERBOSITY_LEVELS:
        s = results["stability"][verbosity]
        r_s = s.get("spearman_r", "N/A")
        preserved = "YES" if s["order_preserved"] else "NO"
        avg_chars = np.mean([
            results[verbosity]["char_lengths"][t]["functional"] +
            results[verbosity]["char_lengths"][t]["format"]
            for t in TIERS
        ])
        print(f"  {verbosity:>14s}: r_s = {r_s:.3f}  order {preserved}  "
              f"(avg {avg_chars:.0f} chars/constraint)")

    # Generate figure
    fig_path = FIGURES_DIR / "prompt_length_sweep.png"
    generate_figure(results, fig_path)

    # Save results
    results_path = Path(__file__).parent / "prompt_length_sweep_results.json"

    # Convert numpy types for JSON serialization
    def convert(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    serializable = {}
    for key, val in results.items():
        if isinstance(val, dict):
            serializable[key] = {
                k: {kk: convert(vv) for kk, vv in v.items()} if isinstance(v, dict) else convert(v)
                for k, v in val.items()
            }
        else:
            serializable[key] = convert(val)

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, default=convert)
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
