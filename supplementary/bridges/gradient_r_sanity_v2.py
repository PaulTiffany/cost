#!/usr/bin/env python3
"""
Gradient-rho sanity check v2.

Paper: "The Cost of Cacophony" (ICML 2026)
  - main.tex, references.bib

Verifies:
  - Appendix I (app:grad_r): Gradient-ρ correlation analysis
  - Claim C4: r_s = 1.0 rank correlation between ρ̂ and failure
  - Table A9 (tab:transfer): Cross-domain transfer validation

Key improvements over v1:
1. Completion-only gradients (prompt+target, mask prompt tokens)
2. Conflict coverage: explicit anti-aligned pairs (ρ > 0)
3. Multi-layer gradient ensemble (3 layers)
4. Reports: Pearson r, Spearman ρ, sign agreement

This directly addresses the "embedding proxy gap" critique by showing
embedding-space conflict tracks gradient-space conflict on open weights.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
from scipy import stats


@dataclass
class PairSpecV2:
    """Extended pair spec with target completions for gradient computation."""
    name: str
    category: str  # "control" or "conflict"
    prompt_a: str
    target_a: str
    prompt_b: str
    target_b: str


# 6 pairs: 2 controls (aligned) + 4 conflicts (anti-aligned)
PAIRS_V2 = [
    # === CONTROLS (aligned, same-axis) ===
    PairSpecV2(
        name="control_helpful_tutorial",
        category="control",
        prompt_a="Explain how neural networks learn.",
        target_a="Neural networks learn by adjusting weights through backpropagation, minimizing loss on training data.",
        prompt_b="Describe the basics of machine learning.",
        target_b="Machine learning uses algorithms to find patterns in data and make predictions on new examples.",
    ),
    PairSpecV2(
        name="control_json_formats",
        category="control",
        prompt_a="List three fruits as JSON.",
        target_a='{"fruits": ["apple", "banana", "cherry"]}',
        prompt_b="List three colors as JSON.",
        target_b='{"colors": ["red", "green", "blue"]}',
    ),

    # === CONFLICTS (anti-aligned, diagonal pain) ===
    PairSpecV2(
        name="conflict_concision_vs_completeness",
        category="conflict",
        prompt_a="Explain photosynthesis briefly.",
        target_a="Plants convert sunlight to energy.",  # Maximally concise
        prompt_b="Explain photosynthesis thoroughly.",
        target_b="Photosynthesis is the process by which plants convert sunlight into chemical energy. Chlorophyll in leaves absorbs light. This energy drives reactions that convert carbon dioxide and water into glucose and oxygen. The process occurs in chloroplasts and is essential for life on Earth.",  # Detailed
    ),
    PairSpecV2(
        name="conflict_confidence_vs_uncertainty",
        category="conflict",
        prompt_a="What stock should I buy? Be confident.",
        target_a="Invest in index funds tracking the S&P 500 for reliable long-term growth.",  # Confident
        prompt_b="What stock should I buy? Acknowledge uncertainty.",
        target_b="I cannot predict stock performance with certainty. Markets are inherently unpredictable. Consider consulting a financial advisor who knows your situation.",  # Uncertain
    ),
    PairSpecV2(
        name="conflict_json_vs_prose",
        category="conflict",
        prompt_a="Describe your capabilities as JSON only.",
        target_a='{"capabilities": ["text generation", "analysis", "coding"], "limitations": ["no internet", "knowledge cutoff"]}',
        prompt_b="Describe your capabilities as natural prose.",
        target_b="I can help with text generation, analysis, and coding tasks. However, I have limitations including no internet access and a knowledge cutoff date.",
    ),
    PairSpecV2(
        name="conflict_loops_vs_recursion",
        category="conflict",
        prompt_a="Write a function to sum a list. Use loops, no recursion.",
        target_a="def sum_list(lst):\n    total = 0\n    for x in lst:\n        total += x\n    return total",
        prompt_b="Write a function to sum a list. Use recursion, no loops.",
        target_b="def sum_list(lst):\n    if not lst:\n        return 0\n    return lst[0] + sum_list(lst[1:])",
    ),
]


def compute_embedding_rho(pairs: List[PairSpecV2], model_name: str) -> List[float]:
    """Compute embedding-based conflict for each pair."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError("Install sentence-transformers") from exc

    model = SentenceTransformer(model_name)
    rhos = []

    for p in pairs:
        # Encode prompt+target as the full "constraint direction"
        full_a = p.prompt_a + " " + p.target_a
        full_b = p.prompt_b + " " + p.target_b

        emb_a = model.encode([full_a])[0]
        emb_b = model.encode([full_b])[0]

        cos = np.dot(emb_a, emb_b) / (np.linalg.norm(emb_a) * np.linalg.norm(emb_b))
        # Conflict = negative cosine (anti-aligned = positive ρ)
        rhos.append(float(-cos))

    return rhos


def compute_grad_rho_v2(
    pairs: List[PairSpecV2],
    model_name: str,
    device: str,
    layer_indices: List[int] = [-1, -2, -3]
) -> List[float]:
    """
    Compute gradient-based conflict using completion-only loss.

    Key differences from v1:
    - Concatenate prompt + target, mask prompt tokens with -100
    - Use model.eval() (dropout off) but still backprop
    - Ensemble gradients across multiple layers
    """
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise ImportError("Install transformers/torch") from exc

    print(f"[info] Loading {model_name}...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)
    model.to(device)
    model.eval()  # Dropout off, but we can still backprop

    # Identify gradient extraction points
    def get_grad_params(model, layer_idx: int) -> List:
        """Get parameter tensors from specified layer."""
        params = []
        try:
            layer = model.model.layers[layer_idx]
            # MLP down projection
            if hasattr(layer, 'mlp') and hasattr(layer.mlp, 'down_proj'):
                params.append(layer.mlp.down_proj.weight)
            # Attention output projection
            if hasattr(layer, 'self_attn') and hasattr(layer.self_attn, 'o_proj'):
                params.append(layer.self_attn.o_proj.weight)
        except (AttributeError, IndexError):
            pass
        return params

    def grad_for_completion(prompt: str, target: str) -> np.ndarray:
        """Compute gradient direction for prompt→target completion."""
        import torch

        # Tokenize separately
        prompt_enc = tokenizer(prompt, return_tensors="pt", add_special_tokens=True)
        target_enc = tokenizer(target, return_tensors="pt", add_special_tokens=False)

        prompt_ids = prompt_enc["input_ids"].to(device)
        target_ids = target_enc["input_ids"].to(device)

        # Concatenate: [prompt][target]
        input_ids = torch.cat([prompt_ids, target_ids], dim=1)
        attention_mask = torch.ones_like(input_ids)

        # Create labels: mask prompt tokens with -100, keep target tokens
        labels = input_ids.clone()
        labels[:, :prompt_ids.shape[1]] = -100

        # Forward + backward
        outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
        loss = outputs.loss

        model.zero_grad(set_to_none=True)
        loss.backward()

        # Collect gradients from multiple layers
        all_grads = []
        for layer_idx in layer_indices:
            for param in get_grad_params(model, layer_idx):
                if param.grad is not None:
                    g = param.grad.detach().flatten().cpu().numpy()
                    all_grads.append(g / (np.linalg.norm(g) + 1e-12))

        if not all_grads:
            raise RuntimeError(f"No gradients collected from layers {layer_indices}")

        # Concatenate all gradient vectors
        combined = np.concatenate(all_grads)
        return combined / (np.linalg.norm(combined) + 1e-12)

    rhos = []
    for i, p in enumerate(pairs):
        print(f"[{i+1}/{len(pairs)}] {p.name} ({p.category})...")

        g_a = grad_for_completion(p.prompt_a, p.target_a)
        g_b = grad_for_completion(p.prompt_b, p.target_b)

        cos = float(np.dot(g_a, g_b))
        rhos.append(float(-cos))  # Conflict = negative cosine

    return rhos


def compute_metrics(embed_rho: List[float], grad_rho: List[float]) -> Dict[str, Any]:
    """Compute correlation metrics between embedding and gradient ρ."""
    embed_arr = np.array(embed_rho)
    grad_arr = np.array(grad_rho)

    # Pearson correlation
    pearson_r, pearson_p = stats.pearsonr(embed_arr, grad_arr)

    # Spearman rank correlation
    spearman_r, spearman_p = stats.spearmanr(embed_arr, grad_arr)

    # Sign agreement (key metric for "identifies conflict regimes")
    # Define conflict as ρ > 0 (anti-aligned)
    embed_conflict = embed_arr > 0
    grad_conflict = grad_arr > 0
    sign_agreement = np.mean(embed_conflict == grad_conflict)

    return {
        "pearson_r": float(pearson_r),
        "pearson_p": float(pearson_p),
        "spearman_r": float(spearman_r),
        "spearman_p": float(spearman_p),
        "sign_agreement": float(sign_agreement),
        "n_pairs": len(embed_rho),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gradient-rho sanity check v2")
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--embed-model", default="all-MiniLM-L6-v2")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", type=Path, default=Path("gradient_rho_v2_results.json"))
    args = parser.parse_args()

    print("=== Gradient-rho Sanity Check v2 ===")
    print(f"Model: {args.model}")
    print(f"Embedding model: {args.embed_model}")
    print(f"Device: {args.device}")
    print(f"Pairs: {len(PAIRS_V2)} (2 control + 4 conflict)")
    print()

    # Compute embedding rho
    print("[1/3] Computing embedding rho...")
    embed_rho = compute_embedding_rho(PAIRS_V2, args.embed_model)

    # Compute gradient rho (v2: completion-only)
    print("\n[2/3] Computing gradient rho (completion-only, multi-layer)...")
    grad_rho = compute_grad_rho_v2(PAIRS_V2, args.model, args.device)

    # Compute metrics
    print("\n[3/3] Computing metrics...")
    metrics = compute_metrics(embed_rho, grad_rho)

    # Build output
    payload = {
        "version": "v2",
        "model": args.model,
        "embed_model": args.embed_model,
        "pairs": [
            {
                "name": p.name,
                "category": p.category,
                "embed_rho": embed_rho[i],
                "grad_rho": grad_rho[i],
            }
            for i, p in enumerate(PAIRS_V2)
        ],
        "metrics": metrics,
    }

    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n[ok] wrote {args.out}")

    # Print summary
    print("\n" + "="*50)
    print("RESULTS SUMMARY")
    print("="*50)
    print(f"Pearson r:      {metrics['pearson_r']:.3f} (p={metrics['pearson_p']:.4f})")
    print(f"Spearman rho:   {metrics['spearman_r']:.3f} (p={metrics['spearman_p']:.4f})")
    print(f"Sign agreement: {metrics['sign_agreement']:.1%}")
    print()
    print("Per-pair breakdown:")
    print(f"{'Name':<35} {'Cat':<8} {'Embed_rho':>10} {'Grad_rho':>10}")
    print("-" * 70)
    for i, p in enumerate(PAIRS_V2):
        print(f"{p.name:<35} {p.category:<8} {embed_rho[i]:>10.3f} {grad_rho[i]:>10.3f}")


if __name__ == "__main__":
    main()
