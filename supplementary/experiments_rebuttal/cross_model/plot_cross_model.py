
#!/usr/bin/env python3
"""
Plot Cross-Model Cliff: Heatmap of Pass vs Tier across models.
"""

import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
import matplotlib.pyplot as plt
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

def load_data():
    # Load Small Models (original benchmarks)
    path_small = REPO_ROOT / "supplementary" / "experiments" / "code_constraint_results.json"
    with open(path_small, "r", encoding="utf-8") as f:
        data_small = json.load(f)

    # Load Frontier Models (OpenRouter extension)
    path_frontier = REPO_ROOT / "rebuttal" / "figures" / "cross_model_results.json"
    with open(path_frontier, "r", encoding="utf-8") as f:
        data_frontier = json.load(f)

    merged = {}
    
    # Process Small Models
    for k, v in data_small.items():
        if k == "_meta": continue
        merged[k] = v["results"] # List of trials

    # Process Frontier Models
    # Structure: flat list in "results" with "model" field
    # Group by model
    for r in data_frontier["results"]:
        m = r["model_id"] # e.g. "openai/gpt-4o"
        if m not in merged:
            merged[m] = []
        merged[m].append(r)
        
    return merged

def plot_heatmap(data, output_dir):
    # Order models roughly by capability/size
    # Manual ordering preference
    order_pref = [
        "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        "deepseek-ai/deepseek-coder-1.3b-instruct", 
        "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        "Qwen/Qwen2.5-3B-Instruct",
        "meta-llama/llama-3.1-70b-instruct", # Frontier starts here
        "google/gemini-2.5-flash-latest",
        "google/gemini-2.5-pro-latest",
        "deepseek/deepseek-chat", # v3
        "openai/gpt-4o",
    ]
    
    # Normalize keys in data to match prefs if needed, or just sort
    # Let's clean keys first
    clean_data = {}
    for k, v in data.items():
        # normalize key
        clean_key = k
        if "deepseek-coder-1.3b" in k.lower(): clean_key = "DeepSeek-Coder-1.3B"
        elif "qwen2.5-coder-1.5b" in k.lower(): clean_key = "Qwen2.5-Coder-1.5B"
        elif "tinyllama" in k.lower(): clean_key = "TinyLlama-1.1B"
        elif "qwen2.5-3b" in k.lower(): clean_key = "Qwen2.5-3B"
        elif "70b" in k.lower(): clean_key = "Llama-3.1-70B"
        elif "flash" in k.lower(): clean_key = "Gemini-2.5-Flash"
        elif "pro" in k.lower(): clean_key = "Gemini-2.5-Pro"
        elif "deepseek-v3" in k.lower() or "deepseek-chat" in k.lower(): clean_key = "DeepSeek-V3"
        elif "gpt-4o" in k.lower(): clean_key = "GPT-4o"
        
        # Aggregate trials
        clean_data[clean_key] = v

    # Define final sorted list
    sorted_models = [
        "TinyLlama-1.1B", "DeepSeek-Coder-1.3B", "Qwen2.5-Coder-1.5B", "Qwen2.5-3B",
        "Llama-3.1-70B", "Gemini-2.5-Flash", "Gemini-2.5-Pro", "DeepSeek-V3", "GPT-4o"
    ]
    # Filter to only present models
    present_models = [m for m in sorted_models if m in clean_data]
    
    tiers = ["control", "low", "moderate", "high"]
    
    # Build Matrix
    matrix = []
    labels = []
    
    for m in present_models:
        labels.append(m)
        row = []
        for t in tiers:
            trials = [r for r in clean_data[m] if r["tier"] == t]
            if not trials:
                row.append(0.0)
            else:
                # Weighted pass rate
                pass_rate = np.mean([1.0 if r["pass_both"] else 0.0 for r in trials])
                row.append(pass_rate)
        matrix.append(row)
    
    matrix = np.array(matrix)
    
    # Plotting
    fig, ax = plt.subplots(figsize=(12, 6))
    im = ax.imshow(matrix, cmap="YlOrBr_r", vmin=0, vmax=1, aspect=0.6)
    
    ax.set_xticks(np.arange(len(tiers)))
    ax.set_yticks(np.arange(len(labels)))
    ax.set_xticklabels([t.capitalize() for t in tiers])
    ax.set_yticklabels(labels)
    
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center")
    
    # Annotations
    for i in range(len(labels)):
        for j in range(len(tiers)):
            val = matrix[i, j]
            text = ax.text(j, i, f"{val:.0%}",
                           ha="center", va="center", color="black" if val >= 0.4 else "white",
                           fontweight="bold", fontsize=9)
            # Dual encoding: intuitive border (green/orange/red) over accessible fill
            border = "#81C784" if val >= 0.6 else "#FFB74D" if val >= 0.3 else "#E57373"
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                         fill=False, edgecolor=border, linewidth=1.5))
    
    ax.set_title("Geometric Cliff: From 1B to Frontier (Pass Rate)", fontsize=14, pad=20)
    fig.tight_layout()
    
    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ["pdf", "png"]:
        path = output_dir / f"cross_model_cliff.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=150)
        print(f"Figure saved: {path}")
    plt.close(fig)

def main():
    print("Generating Cross-Model Cliff Plot...")
    data = load_data()
    output_dir = REPO_ROOT / "rebuttal" / "figures"
    plot_heatmap(data, output_dir)

if __name__ == "__main__":
    main()
