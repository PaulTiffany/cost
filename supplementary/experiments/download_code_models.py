#!/usr/bin/env python3
"""
Download models for the code constraint experiment.
Run this first, then run code_constraint_experiment.py
"""

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODELS = [
    ("Qwen/Qwen2.5-Coder-1.5B-Instruct", "~3GB", "Code-specialized Qwen"),
    ("deepseek-ai/deepseek-coder-1.3b-instruct", "~2.6GB", "DeepSeek Coder"),
    ("TinyLlama/TinyLlama-1.1B-Chat-v1.0", "~2.2GB", "General chat baseline"),
]

def main():
    print("="*60)
    print("Code Constraint Experiment - Model Downloader")
    print("="*60)

    if torch.cuda.is_available():
        print(f"[GPU] DETECTED: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
    else:
        print("CPU mode (no GPU detected)")

    print("="*60)
    print(f"Downloading {len(MODELS)} models...")
    print("="*60)

    for i, (model_name, size, desc) in enumerate(MODELS, 1):
        print(f"\n[{i}/{len(MODELS)}] {model_name}")
        print(f"  Size: {size} | {desc}")
        print("-"*40)

        print("  Downloading tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        print("  [OK] Tokenizer cached")

        print("  Downloading model weights...")
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map="cpu",
            torch_dtype=torch.float32
        )
        print("  [OK] Model cached")

        # Free memory
        del model
        del tokenizer
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    print("\n" + "="*60)
    print("SUCCESS! All models cached.")
    print("="*60)
    print("\nCached models:")
    for model_name, size, desc in MODELS:
        cache_name = model_name.replace('/', '--')
        print(f"  - {model_name}")
    print("\nYou can now run: python code_constraint_experiment.py")
    print("="*60)


if __name__ == "__main__":
    main()
