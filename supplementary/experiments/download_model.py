#!/usr/bin/env python3
"""
Download the LLM model for offline use.
Run this first, then run llm_bridge_experiment.py
"""

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# Auto-detect GPU and choose model
if torch.cuda.is_available():
    MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
    MODEL_SIZE = "~14GB"
    print("="*60)
    print(f"[GPU] DETECTED: {torch.cuda.get_device_name(0)}")
    print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
    print("="*60)
else:
    MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"
    MODEL_SIZE = "~1.5GB"
    print("="*60)
    print("CPU mode (no GPU detected)")
    print("="*60)

print(f"Downloading {MODEL_NAME}")
print(f"Model size: {MODEL_SIZE}")
print("This will be cached for future use")
print("="*60)

print("\n[1/2] Downloading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
print("[OK] Tokenizer downloaded")

print("\n[2/2] Downloading model (this may take several minutes)...")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    device_map="cpu"
)
print("[OK] Model downloaded")

print("\n" + "="*60)
print("SUCCESS! Model cached at:")
print(f"  ~/.cache/huggingface/hub/models--{MODEL_NAME.replace('/', '--')}")
print("\nYou can now run: python llm_bridge_experiment.py")
print("="*60)
