#!/usr/bin/env python3
"""
claim_audit.py - Audit numeric claims in main.tex against CLAIM_AUDIT.md registry.

Walks the 25 critical (Tier 1) claims registered in ../CLAIM_AUDIT.md and
verifies each numeric signature still appears in ../paper/main.tex.

Lessons baked in:
  * main.tex is ~2,560 source lines (~250KB). The default 'any'-mode
    matcher streams the file line-by-line so per-line memory stays
    bounded. The 'joint'-mode matcher (added later for table-row
    claims) reads the whole file once, since windowed access is
    needed; this is fine for a 250KB paper but would not generalize
    to MB-scale corpora.
  * No auto-fix. Report only. The human decides what drift means.
  * Lexicon-respecting: the script only audits numeric signatures and never
    proposes prose changes. Protected terms (kinematic, judge-free, regime
    index, diagonal cost, ...) are loaded for visibility but never edited.

Exit codes:
  0  every claim found (with or without drift warnings)
  1  one or more claims missing entirely

Usage:
  python claim_audit.py              # quiet: summary + JSON only
  python claim_audit.py --verbose    # per-claim status as it goes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Paths (anchored to this script's location so it runs from any CWD)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CLAIMS_MD = REPO_ROOT / "CLAIM_AUDIT.md"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
LEXICON_MD = SCRIPT_DIR / "de_llm_lexicon.md"
RESULTS_JSON = SCRIPT_DIR / "claim_audit_results.json"

# Status glyphs (kept ASCII-friendly so Windows consoles render them).
FOUND = "OK"      # exact match for at least one signature
DRIFT = "WARN"    # partial match: some signatures hit, others did not
MISSING = "MISS"  # no signatures matched anywhere

# ---------------------------------------------------------------------------
# Hand-curated signature table for the 25 Tier-1 claims.
#
# Each claim gets a list of regex patterns (compiled later). A claim counts
# as FOUND when ALL its pattern groups hit; DRIFT when SOME hit but not all;
# MISSING when none hit. Patterns are LaTeX-aware: numbers are matched
# loosely (\, between digits is allowed because LaTeX often writes 4{,}800
# or 4,800 or 4{,}\!800).
#
# These signatures are the *minimum distinguishing fingerprint* of each
# claim, not a full transcription. If a claim's numeric value drifts, the
# regex stops matching and the audit flags it.
# ---------------------------------------------------------------------------

# Helper regex fragments
NUM = r"[0-9]+(?:[,\\{}\\!\s]*[0-9]+)*"  # 4800, 4,800, 4{,}800, 4{,}\!800
DEC = r"[0-9]+(?:\.[0-9]+)?"
PM = r"(?:\\pm|\+/-)"

CLAIMS: list[dict] = [
    {
        "id": "C1",
        "description": r"$\delta_{\min} = \sqrt{2/(1-\rho)}$ diagonal cost (Theorem 3.1)",
        "patterns": [
            # Accept either surface form: sqrt(2/(1-rho)) or sqrt(2)/sqrt(1-rho)
            r"\\sqrt\s*\{\s*2\s*/\s*\(\s*1\s*-\s*\\rho\s*\)\s*\}|\\sqrt\s*\{\s*2\s*\}\s*\}?\s*/?\s*\}?\s*\{?\s*\\sqrt\s*\{\s*1\s*-\s*\\rho\s*\}",
            r"diagonal\s+cost",
        ],
    },
    {
        "id": "C2",
        "description": r"$\delta_{\min} = \sqrt{k}$ orthogonal k-scaling (joint, Theorem 3.4)",
        "patterns": [
            r"\\sqrt\s*\{\s*k\s*\}",
            r"orthogonal",
            r"\\delta_\{\\min\}|delta_min",
        ],
        "match_mode": "joint",
        "match_window": 5,
    },
    {
        "id": "C3",
        "description": r"0/4,800 smooth-regime refutations (Abstract)",
        "patterns": [
            r"0\s*/\s*4[,\\{}\\!\s]*800",
        ],
    },
    {
        "id": "C4",
        "description": r"$r_s = 1.0$ rank correlation (Table 2)",
        "patterns": [
            # Allow LaTeX collapsed form r_s{=}1.0 in addition to r_s = 1.0
            r"r_s\s*\{?=\}?\s*1\.0|r_s\s*[={]+\s*1\.0",
        ],
    },
    {
        "id": "C5",
        "description": r"4.8x staging at frontier (opus-4.5, Table 4)",
        "patterns": [
            r"4\.8\s*(?:\\times|x|\$\\times\$)",
        ],
    },
    {
        "id": "C6",
        "description": r"26.4x staging (opus-4.1 highest, Table 4)",
        "patterns": [
            r"26\.4\s*(?:\\times|x|\$\\times\$)",
        ],
    },
    {
        "id": "C7",
        "description": r"$\delta_{\min} = \sqrt{k/(1-\rho(k-1))}$ generalized (Corollary 3.5)",
        "patterns": [
            r"\\sqrt\s*\{\s*k\s*/\s*\(\s*1\s*[-{}\s]*\\rho\s*\(?\s*k\s*[-{}\s]*1",
            r"1\s*/\s*\(\s*k\s*[-{}\s]*1\s*\)",
        ],
    },
    {
        "id": "C8",
        "description": r"$\hat{L} \in [0.019, 0.031]$ across 4 models (Table 1)",
        "patterns": [
            r"0\.019",
            r"0\.031",
        ],
    },
    {
        "id": "C9",
        "description": r"89% smooth, 11% pivot regime (joint with 'pivot' or 'Lipschitz' anchor)",
        "patterns": [
            r"89\s*\\?%",
            r"11\s*\\?%",
            r"pivot|[Ll]ipschitz",
        ],
        "match_mode": "joint",
        "match_window": 5,
    },
    {
        "id": "C10",
        "description": r"94% router agreement across encoders (joint with 'router' + 'agree')",
        "patterns": [
            r"94\s*\\?%",
            r"router",
            r"agree",
        ],
        "match_mode": "joint",
        "match_window": 5,
    },
    {
        "id": "C11",
        "description": r"7.37% constitution conflict rate (Appendix W)",
        "patterns": [
            r"7\.37\s*\\?%",
        ],
    },
    {
        "id": "C12",
        "description": r"93% to 3% feasibility decay (joint with 'feasibility' anchor)",
        "patterns": [
            r"93\s*\\?%",
            r"3\s*\\?%",
            r"[Ff]easibility|[Cc]harit",
        ],
        "match_mode": "joint",
        "match_window": 5,
    },
    {
        "id": "C13",
        "description": r"$\hat{\rho}_{\text{stage}} = 0.15$ (Algorithm 1)",
        "patterns": [
            r"(?:stage|\\text\{stage\})\s*\}?\s*=\s*0\.15",
        ],
    },
    {
        "id": "C14",
        "description": r"$\hat{\rho}_{\text{fail}} = 0.5$ (Algorithm 1)",
        "patterns": [
            r"(?:fail|\\text\{fail\})\s*\}?\s*=\s*0\.5",
        ],
    },
    {
        "id": "C15",
        "description": r"18% pass, 384 tokens, 0.67x efficiency (Table 11)",
        "patterns": [
            r"18\s*\\?%",
            r"384",
            r"0\.67\s*(?:\\times|x|\$\\times\$)",
        ],
    },
    {
        "id": "C16",
        "description": r">=91% threshold robustness (+/-20%) (joint with 'threshold' anchor)",
        "patterns": [
            r"91\s*\\?%",
            r"20\s*\\?%",
            r"[Tt]hreshold",
        ],
        "match_mode": "joint",
        "match_window": 5,
    },
    {
        "id": "C17",
        "description": r"$r_s \geq 0.94$ across encoders (Table 6)",
        "patterns": [
            r"r_s\s*\{?(?:\\geq|>=|\\ge|=)\}?\s*0\.94|r_s\s*[={]+\s*0\.94",
            r"0\.94",
        ],
    },
    {
        "id": "C18",
        "description": r"94% trajectories with drift <0.15 (Section 4)",
        "patterns": [
            r"94\s*\\?%",
            r"0\.15",
        ],
    },
    {
        "id": "C19",
        "description": r"6% high-drift = pivot completions (Appendix J)",
        "patterns": [
            r"6\s*\\?%",
            r"high[-\s]drift|pivot",
        ],
    },
    {
        "id": "C20",
        "description": r"<2% regret vs oracle (Table 11)",
        "patterns": [
            r"(?:<|\\leq|\\le)\s*2\s*\\?%|2\s*\\?%\s*regret|1\.8\s*\\?\\?pm\s*0\.4\s*\\?%",
        ],
    },
    {
        "id": "C21",
        "description": r"100/100 vs 0/100 hard-negative (Appendix D.5)",
        "patterns": [
            r"100\s*/\s*100",
            r"0\s*/\s*100",
        ],
    },
    {
        "id": "C22",
        "description": r"59% to 0% Bytebeat collapse (Section 5.2)",
        "patterns": [
            r"59\s*\\?%",
            r"Bytebeat|bytebeat",
        ],
    },
    {
        "id": "C23",
        "description": r"0% IF-DSL at high rho (joint with 'IF-DSL' + '0%' + 'high' anchor, Section 5.2)",
        "patterns": [
            r"IF[-\s]?DSL",
            r"0\s*\\?%",
            r"high",
        ],
        "match_mode": "joint",
        "match_window": 3,
    },
    {
        "id": "C24",
        "description": r"77.5% to 100% JSON-NL failure (Appendix H)",
        "patterns": [
            r"JSON[-\s]?NL",
            r"77\.5\s*\\?%|100\s*\\?%",
        ],
    },
    {
        "id": "C25",
        "description": r"20 principles -> 190 pairs (Appendix W)",
        "patterns": [
            r"190\s+pairs|190\\\s*pairs",
            r"20\s+principles|20\\\s*principles",
        ],
    },
]

assert len(CLAIMS) == 25, f"Expected 25 critical claims, got {len(CLAIMS)}"


# ---------------------------------------------------------------------------
# Tier 2: Important Claims (75)
#
# Table cells from the main paper plus headline appendix numbers. Patterns
# here are tighter than Tier 1 — usually the bare numeric signature, since
# table cells are unambiguous fingerprints. When a value would be ambiguous
# (e.g. "94"), we anchor it with a model name or a named statistic.
# ---------------------------------------------------------------------------

IMPORTANT: list[dict] = [
    # Table 1: Lipschitz Calibration (I1-I6) — joint-context: all
    # patterns must co-occur within a 3-line window, so model name +
    # value(s) are anchored together. Stronger than per-pattern-anywhere.
    {"id": "I1", "description": "Qwen-2.5-Coder L=0.023+/-0.008", "patterns": [r"Qwen.*?Coder|Qwen-2\.5-Coder", r"0\.023", r"0\.008"], "match_mode": "joint", "match_window": 3},
    {"id": "I2", "description": "Qwen-2.5-Coder 91% tightness (joint with model name)", "patterns": [r"Qwen.*?Coder|Qwen-2\.5-Coder", r"91\s*\\?%"], "match_mode": "joint", "match_window": 3},
    {"id": "I3", "description": "DeepSeek-Coder L=0.019+/-0.006", "patterns": [r"DeepSeek.*?Coder|DeepSeek-Coder", r"0\.019", r"0\.006"], "match_mode": "joint", "match_window": 3},
    {"id": "I4", "description": "DeepSeek-Coder 93% tightness (joint with model name)", "patterns": [r"DeepSeek.*?Coder|DeepSeek-Coder", r"93\s*\\?%"], "match_mode": "joint", "match_window": 3},
    {"id": "I5", "description": "TinyLlama-1.1B L=0.031+/-0.011", "patterns": [r"TinyLlama", r"0\.031", r"0\.011"], "match_mode": "joint", "match_window": 3},
    {"id": "I6", "description": "TinyLlama-1.1B 84% tightness (joint with model name)", "patterns": [r"TinyLlama", r"84\s*\\?%"], "match_mode": "joint", "match_window": 3},
    # Table 2: Regime Performance (I7-I10) — joint-context with tier label as anchor
    {"id": "I7", "description": "Control rho=0.05, 76%/24% (joint)", "patterns": [r"[Cc]ontrol", r"0\.05", r"76\s*\\?%"], "match_mode": "joint", "match_window": 5},
    {"id": "I8", "description": "Low rho=0.20, 56%/44% (joint)", "patterns": [r"\bLow\b", r"0\.20", r"56\s*\\?%"], "match_mode": "joint", "match_window": 5},
    {"id": "I9", "description": "Moderate rho=0.40, 23%/77% (joint)", "patterns": [r"[Mm]oderate", r"0\.40", r"23\s*\\?%"], "match_mode": "joint", "match_window": 5},
    {"id": "I10", "description": "High rho=0.65, 2%/98% (joint)", "patterns": [r"\bHigh\b", r"0\.65", r"98\s*\\?%"], "match_mode": "joint", "match_window": 5},
    # Table 3: Feasibility Boundary (I11-I15)
    {"id": "I11", "description": "rho=0.0 -> delta=1.4142", "patterns": [r"1\.4142"]},
    {"id": "I12", "description": "rho=0.3 -> delta=1.6903", "patterns": [r"1\.6903"]},
    {"id": "I13", "description": "rho=0.5 -> delta=2.0000", "patterns": [r"2\.0000"]},
    {"id": "I14", "description": "rho=0.7 -> delta=2.5820", "patterns": [r"2\.5820"]},
    {"id": "I15", "description": "rho=0.9 -> delta=4.4721", "patterns": [r"4\.4721"]},
    # Table 4: Claude Family (I16-I22) — joint-context with model name anchor.
    # The ratio decimals (22.7, 26.4, 23.3, 23.8, 4.8) are already specific
    # but joint mode ensures they live in the right model's row.
    {"id": "I16", "description": "haiku-3 ratio 2.5x (joint)", "patterns": [r"haiku[-\s]?3\b", r"2\.5\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    {"id": "I17", "description": "sonnet-4 ratio 4.4x (joint)", "patterns": [r"sonnet[-\s]?4\b", r"4\.4\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    {"id": "I18", "description": "opus-4 ratio 22.7x (joint)", "patterns": [r"opus[-\s]?4\b", r"22\.7\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    {"id": "I19", "description": "opus-4.1 ratio 26.4x (joint)", "patterns": [r"opus[-\s]?4\.1", r"26\.4\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    {"id": "I20", "description": "haiku-4.5 ratio 23.3x (joint)", "patterns": [r"haiku[-\s]?4\.5", r"23\.3\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    {"id": "I21", "description": "sonnet-4.5 ratio 23.8x (joint)", "patterns": [r"sonnet[-\s]?4\.5", r"23\.8\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    {"id": "I22", "description": "opus-4.5 ratio 4.8x (joint)", "patterns": [r"opus[-\s]?4\.5", r"4\.8\s*(?:\\times|x|\$\\times\$)"], "match_mode": "joint", "match_window": 3},
    # Table 5: Embedding Analysis (I23-I25)
    {"id": "I23", "description": "MiniLM-L6-v2 rho_max=0.12+/-0.02", "patterns": [r"0\.12", r"0\.02", r"MiniLM"]},
    {"id": "I24", "description": "mpnet-base-v2 rho_max=0.18+/-0.03", "patterns": [r"0\.18", r"0\.03", r"mpnet"]},
    {"id": "I25", "description": "multilingual-MiniLM rho_max=0.20+/-0.04", "patterns": [r"0\.20", r"0\.04", r"multilingual"]},
    # Table 6: Encoder Sensitivity (I26-I28)
    {"id": "I26", "description": "MiniLM-L6-v2 rho range [0.05, 0.65]", "patterns": [r"0\.05", r"0\.65"]},
    {"id": "I27", "description": "mpnet-base-v2 rho range [0.08, 0.71], 94%/91%", "patterns": [r"0\.08", r"0\.71"]},
    {"id": "I28", "description": "multilingual rho range [0.06, 0.68], 96%/93%", "patterns": [r"0\.06", r"0\.68"]},
    # Table 7: Threshold Sensitivity (I29-I30)
    {"id": "I29", "description": "-20% perturbation: 93% agreement, +1.2% pass", "patterns": [r"1\.2\s*\\?%", r"20\s*\\?%"]},
    {"id": "I30", "description": "+20% perturbation: 91% agreement, -2.1% pass", "patterns": [r"2\.1\s*\\?%"]},
    # Table 8: k-Scaling (I31-I35)
    {"id": "I31", "description": "k=2 -> delta=1.4142 (numerical)", "patterns": [r"1\.4142"]},
    {"id": "I32", "description": "k=3 -> delta=1.7321", "patterns": [r"1\.7321"]},
    {"id": "I33", "description": "k=4 -> delta=2.0000", "patterns": [r"2\.0000"]},
    {"id": "I34", "description": "k=5 -> delta=2.2361", "patterns": [r"2\.2361"]},
    {"id": "I35", "description": "k=8 -> delta=2.8284", "patterns": [r"2\.8284"]},
    # Table 11: Baselines (I36-I41) — joint-context with protocol name anchor.
    # Token counts (256/512/1024/640/384/320) and pass percentages
    # individually look weak but co-occur with the protocol label in the row.
    {"id": "I36", "description": "One-shot: 5% pass, 256 tokens (joint)", "patterns": [r"[Oo]ne[-\s]?shot", r"5\s*\\?%", r"256"], "match_mode": "joint", "match_window": 3},
    {"id": "I37", "description": "Staged (always): 18% pass, 512 tokens (joint)", "patterns": [r"[Ss]taged", r"18\s*\\?%", r"512"], "match_mode": "joint", "match_window": 3},
    {"id": "I38", "description": "Best-of-4: 12% pass, 1024 tokens (joint)", "patterns": [r"[Bb]est[-\s]?of[-\s]?4", r"12\s*\\?%", r"1024"], "match_mode": "joint", "match_window": 3},
    {"id": "I39", "description": "Self-refine: 15% pass, 640 tokens (joint)", "patterns": [r"[Ss]elf[-\s]?refine", r"15\s*\\?%", r"640"], "match_mode": "joint", "match_window": 3},
    {"id": "I40", "description": "Geometric router: 18% pass, 384 tokens, 0.67x (joint)", "patterns": [r"[Gg]eometric", r"384", r"0\.67"], "match_mode": "joint", "match_window": 3},
    {"id": "I41", "description": "Oracle: 21% pass, 320 tokens (joint)", "patterns": [r"[Oo]racle", r"21\s*\\?%", r"320"], "match_mode": "joint", "match_window": 3},
    # Table 12: Transfer (I42-I45) — joint-context with domain anchor
    {"id": "I42", "description": "Code: 100% router agree, 1.8% regret (joint)", "patterns": [r"[Cc]ode", r"1\.8\s*\\?%"], "match_mode": "joint", "match_window": 3},
    {"id": "I43", "description": "JSON-NL: 94% router, +2% delta, 2.1% regret (joint)", "patterns": [r"JSON[-\s]?NL", r"2\.1\s*\\?%"], "match_mode": "joint", "match_window": 3},
    {"id": "I44", "description": "IF-DSL: 91% router, -1% delta, 2.4% regret (joint)", "patterns": [r"IF[-\s]?DSL", r"2\.4\s*\\?%"], "match_mode": "joint", "match_window": 3},
    {"id": "I45", "description": "Bytebeat: 88% router, +1% delta, 3.1% regret (joint)", "patterns": [r"[Bb]ytebeat", r"3\.1\s*\\?%"], "match_mode": "joint", "match_window": 3},
    # Hyperparameters (I46-I51)
    {"id": "I46", "description": "Token budgets: 128, 192, 256, 384, 512 (joint with 'token budget' anchor)", "patterns": [r"[Tt]oken\s+budget", r"128", r"512"], "match_mode": "joint", "match_window": 5},
    {"id": "I47", "description": "Sample size N=60 per condition (main)", "patterns": [r"N\s*[={]+\s*60|N\}?\s*=\s*60"]},
    {"id": "I48", "description": "Sample size N=100 (supplementary spot-checks)", "patterns": [r"N\s*[={]+\s*100|N=100|spot[-\s]check"], "supplementary_only": True},
    {"id": "I49", "description": "Default L_hat ~ 0.025", "patterns": [r"0\.025"]},
    {"id": "I50", "description": "Pivot step threshold = 2.5 x L_hat", "patterns": [r"2\.5\\?hat\{?L|2\.5\s*\\hat\{L\}|2\.5L"]},
    {"id": "I51", "description": "Direction drift threshold 15 deg", "patterns": [r"15\s*\\?(?:deg|circ|\\degree|\^)"]},
    # Derived Statistics (I52-I58)
    {"id": "I52", "description": "Benchmark structure 12 tasks x 4 tiers x 4 models", "patterns": [r"12\s*(?:tasks?|\\times)", r"4\s*(?:tiers?|\\times|\\,)", r"4\s*models?"]},
    {"id": "I53", "description": "Total trials 4,800", "patterns": [r"4[,\\{}\\!\s]*800"]},
    {"id": "I54", "description": "Median direction drift 0.07 (IQR 0.03-0.12)", "patterns": [r"0\.07", r"IQR"]},
    {"id": "I55", "description": "95th percentile step 0.041", "patterns": [r"0\.041"]},
    {"id": "I56", "description": "99th percentile step 0.067", "patterns": [r"0\.067"]},
    {"id": "I57", "description": "Maximum step 0.12", "patterns": [r"0\.12"]},
    {"id": "I58", "description": "Semantic jump rate 2.3% (>3sigma)", "patterns": [r"2\.3\s*\\?%"]},
    # Appendix Key Claims (I59-I75)
    {"id": "I59", "description": "App B: 0.00% error for k=2,3,4,5,8", "patterns": [r"0\.00\s*\\?%"]},
    {"id": "I60", "description": "App V: k=2/3/4/5 -> 93/86/79/73% (supplementary granular)", "patterns": [r"86\s*\\?%", r"79\s*\\?%", r"73\s*\\?%"], "supplementary_only": True, "weak_ok": True},
    {"id": "I61", "description": "App V: k=6/8/10 -> 67/56/3% (supplementary granular)", "patterns": [r"67\s*\\?%", r"56\s*\\?%"], "supplementary_only": True, "weak_ok": True},
    {"id": "I62", "description": "App W: 190 pairs, 7.37% high conflict", "patterns": [r"190\s+pairs|190\\\s*pairs", r"7\.37"]},
    {"id": "I63", "description": "App W: mean rho=0.267, max=0.86", "patterns": [r"0\.267", r"0\.86"]},
    {"id": "I64", "description": "App X: 15 compound tasks, rho 0.15-0.75", "patterns": [r"15\s+compound|compound\s+tasks", r"0\.15", r"0\.75"]},
    {"id": "I65", "description": "App H: JSON-NL 22.5%, 10%, 0%, 0%", "patterns": [r"22\.5\s*\\?%"]},
    {"id": "I66", "description": "App I: gradient-rho r~0.4, r_s=1.0", "patterns": [r"r\s*[={]+\s*0\.4|0\.4", r"r_s\s*[={]+\s*1\.0"]},
    {"id": "I67", "description": "App P: <10ms router latency", "patterns": [r"10\s*ms|<\s*10\\,?ms"]},
    {"id": "I68", "description": "App Q: AM 20-70Hz mapping", "patterns": [r"20.{0,5}70\s*Hz|20\\?-70"]},
    {"id": "I69", "description": "App R: IF-DSL 0% at high rho (joint with '0%' + 'high' anchors)", "patterns": [r"IF[-\s]?DSL", r"0\s*\\?%", r"high"], "match_mode": "joint", "match_window": 3},
    {"id": "I70", "description": "App S: Bytebeat cliff at rho>=0.8", "patterns": [r"0\.8", r"[Bb]ytebeat"]},
    {"id": "I71", "description": "App U: <5min CPU, ~20h GPU", "patterns": [r"5\s*min(?:utes?)?|<\s*5", r"20\s*h(?:ours?)?|20\\,?h"]},
    {"id": "I72", "description": "App E: 5% vs 58%/71% conjunction (joint with 'conjunction' anchor)", "patterns": [r"[Cc]onjunction", r"58\s*\\?%", r"71\s*\\?%"], "match_mode": "joint", "match_window": 5},
    {"id": "I73", "description": "App M: phase transition 1/(k-1)", "patterns": [r"1\s*/\s*\(?\s*k\s*[-{}\s]*1"]},
    {"id": "I74", "description": "App D: rho_max ~ 0.18", "patterns": [r"0\.18"]},
    {"id": "I75", "description": "App D: 24% reinforcing pairs", "patterns": [r"24\s*\\?%", r"reinforcing"]},
]

assert len(IMPORTANT) == 75, f"Expected 75 important claims, got {len(IMPORTANT)}"


# ---------------------------------------------------------------------------
# Lexicon load (visibility only; we never edit prose)
# ---------------------------------------------------------------------------
def load_protected_terms(path: Path) -> list[str]:
    """Pull the bullet-listed protected terms from the lexicon file.

    We extract from Sections 1a-1d so a future check can warn if someone
    proposes editing one. This script only reads them; it does not act.
    """
    if not path.exists():
        return []
    terms: list[str] = []
    in_protected = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## 1."):
            in_protected = True
            continue
        if stripped.startswith("## 2."):
            in_protected = False
            continue
        if not in_protected:
            continue
        # Pull terms from markdown table cells and bullet points.
        m = re.match(r"\|\s*`([^`]+)`", line)
        if m:
            terms.append(m.group(1))
            continue
        m = re.match(r"-\s*`([^`]+)`", line)
        if m:
            terms.append(m.group(1))
    return terms


# ---------------------------------------------------------------------------
# Audit core
# ---------------------------------------------------------------------------
@dataclass
class PatternHit:
    pattern: str
    locations: list[tuple[int, str]] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.locations)


@dataclass
class ClaimResult:
    claim_id: str
    description: str
    status: str  # FOUND / DRIFT / MISSING
    pattern_hits: list[PatternHit]
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "paper_text_signature": self.description,
            "found": self.status,
            "locations": [
                {
                    "pattern": h.pattern,
                    "hits": [{"line": ln, "snippet": snip} for ln, snip in h.locations],
                }
                for h in self.pattern_hits
            ],
            "notes": self.notes,
        }


def stream_lines(path: Path) -> Iterable[tuple[int, str]]:
    """Yield (line_no, line_text) WITHOUT loading the full file.

    This is the truncation-safe pathway: each line is processed once and
    discarded. No 51-page buffer, no stale snapshot.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            yield i, line.rstrip("\n")


def _check_joint_window(patterns: list[re.Pattern], lines: list[str], window: int = 3) -> tuple[bool, int | None]:
    """Joint-context match: do ALL patterns hit within some window of consecutive lines?

    Returns (found, anchor_line_no). The window slides; we accept the
    earliest position where every pattern matches in the joined text
    of `window` lines centered on (or starting at) that position.
    """
    n = len(lines)
    half = window // 2
    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half + 1)
        text = "\n".join(lines[start:end])
        if all(p.search(text) for p in patterns):
            return True, i + 1  # 1-indexed line numbers
    return False, None


def audit_claims(tex_path: Path, claims: list[dict], verbose: bool = False) -> list[ClaimResult]:
    """Single streaming pass over main.tex; multi-pattern matcher per line.

    Two match modes:
      - default ('any'): each pattern must hit somewhere in main.tex,
        not necessarily co-occurring. Streaming-friendly.
      - 'joint': all patterns must hit within a small window
        (default 3 consecutive lines). Stronger fingerprint; required
        for table-cell claims where co-occurrence matters. Joint-mode
        claims load main.tex into memory once (~2,500 lines, ~250KB).
    """
    # Pre-compile every pattern.
    compiled: dict[str, list[tuple[str, re.Pattern]]] = {}
    hits: dict[str, list[PatternHit]] = {}
    for claim in claims:
        compiled[claim["id"]] = [(p, re.compile(p)) for p in claim["patterns"]]
        hits[claim["id"]] = [PatternHit(pattern=p) for p in claim["patterns"]]

    # Streaming pass for any-mode claims; track per-pattern locations.
    for line_no, line in stream_lines(tex_path):
        for claim_id, patterns in compiled.items():
            for idx, (raw, regex) in enumerate(patterns):
                if regex.search(line):
                    snippet = line.strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    hits[claim_id][idx].locations.append((line_no, snippet))

    # Joint-mode claims need windowed access. Read all lines once.
    joint_claims = [c for c in claims if c.get("match_mode") == "joint"]
    joint_results: dict[str, tuple[bool, int | None]] = {}
    if joint_claims:
        all_lines = tex_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for claim in joint_claims:
            window = claim.get("match_window", 3)
            patterns = [re.compile(p) for p in claim["patterns"]]
            joint_results[claim["id"]] = _check_joint_window(patterns, all_lines, window=window)

    # Roll up status.
    results: list[ClaimResult] = []
    for claim in claims:
        h = hits[claim["id"]]
        n_total = len(h)
        n_found = sum(1 for ph in h if ph.found)

        # Joint-mode claims override the per-pattern roll-up: they
        # require the windowed all-patterns-co-occur check to pass.
        if claim.get("match_mode") == "joint":
            joint_ok, anchor = joint_results[claim["id"]]
            if joint_ok:
                status = FOUND
                notes = f"All {n_total} patterns co-occur within {claim.get('match_window', 3)}-line window (anchor line ~{anchor})."
            else:
                status = MISSING
                # Are individual patterns at least present anywhere? If yes,
                # this is drift (values exist but not together).
                if n_found == n_total:
                    status = DRIFT
                    notes = f"All {n_total} patterns appear in main.tex but NOT within {claim.get('match_window', 3)}-line window. Values may have separated; verify the table/context still ties them together."
                elif n_found > 0:
                    notes = f"Joint-mode FAIL: {n_found}/{n_total} patterns appear, none co-occur in {claim.get('match_window', 3)}-line window."
                else:
                    notes = f"Joint-mode FAIL: no patterns matched in main.tex."
        elif n_found == n_total:
            status = FOUND
            notes = f"All {n_total} signature patterns matched."
        elif n_found == 0:
            status = MISSING
            notes = "No signature patterns matched. Number may have drifted, or claim was removed."
        else:
            status = DRIFT
            missed = [ph.pattern for ph in h if not ph.found]
            notes = f"{n_found}/{n_total} patterns matched. Missing: {missed}"

        result = ClaimResult(
            claim_id=claim["id"],
            description=claim["description"],
            status=status,
            pattern_hits=h,
            notes=notes,
        )
        results.append(result)

        if verbose:
            badge = {FOUND: "[OK]  ", DRIFT: "[WARN]", MISSING: "[MISS]"}[status]
            print(f"  {badge} {claim['id']}: {claim['description']}")
            print(f"         {notes}")
            for ph in h:
                if ph.found:
                    ln, snip = ph.locations[0]
                    print(f"           hit @ line {ln}: {snip}")

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(results: list[ClaimResult], lexicon_terms: list[str], tier_label: str) -> None:
    n_found = sum(1 for r in results if r.status == FOUND)
    n_drift = sum(1 for r in results if r.status == DRIFT)
    n_missing = sum(1 for r in results if r.status == MISSING)
    total = len(results)

    print("=" * 70)
    print(f"CLAIM AUDIT REPORT  ({tier_label}: {total} claims)")
    print("=" * 70)
    print(f"Paper:    {MAIN_TEX}")
    print(f"Registry: {CLAIMS_MD}")
    print(f"Lexicon:  {LEXICON_MD}  ({len(lexicon_terms)} protected terms loaded)")
    print()
    print(f"  Verbatim found : {n_found:>3} / {total}")
    print(f"  Drift warning  : {n_drift:>3} / {total}")
    print(f"  Missing        : {n_missing:>3} / {total}")
    print()

    if n_drift or n_missing:
        print("-" * 70)
        print("Claims requiring human review:")
        print("-" * 70)
        for r in results:
            if r.status == FOUND:
                continue
            badge = "[WARN]" if r.status == DRIFT else "[MISS]"
            print(f"  {badge} {r.claim_id}: {r.description}")
            print(f"         {r.notes}")
        print()

    print("Full per-claim JSON written to:")
    print(f"  {RESULTS_JSON}")
    print()


def write_json(results: list[ClaimResult], lexicon_terms: list[str], tier_label: str) -> None:
    n_found = sum(1 for r in results if r.status == FOUND)
    n_drift = sum(1 for r in results if r.status == DRIFT)
    n_missing = sum(1 for r in results if r.status == MISSING)
    payload = {
        "summary": {
            "tier": tier_label,
            "total": len(results),
            "found_verbatim": n_found,
            "drift": n_drift,
            "missing": n_missing,
        },
        "paper_path": str(MAIN_TEX),
        "registry_path": str(CLAIMS_MD),
        "lexicon_protected_terms": lexicon_terms,
        "claims": [r.to_dict() for r in results],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print per-claim status as it runs")
    parser.add_argument(
        "--tier",
        choices=["critical", "important", "all"],
        default="critical",
        help="which tier to audit: critical (25), important (75), or all (100). Default: critical.",
    )
    args = parser.parse_args(argv)

    if not MAIN_TEX.exists():
        print(f"ERROR: paper not found at {MAIN_TEX}", file=sys.stderr)
        return 2
    if not CLAIMS_MD.exists():
        print(f"ERROR: claim registry not found at {CLAIMS_MD}", file=sys.stderr)
        return 2

    if args.tier == "critical":
        raw = CLAIMS
        tier_label = "Tier 1: critical"
    elif args.tier == "important":
        raw = IMPORTANT
        tier_label = "Tier 2: important"
    else:
        raw = CLAIMS + IMPORTANT
        tier_label = "Tiers 1+2: critical+important"

    # Drop claims that are documented as supplementary-only — they live in
    # the supplementary package, not main.tex, so a "missing" verdict here
    # would be a false alarm.
    active = [c for c in raw if not c.get("supplementary_only")]
    n_skipped = len(raw) - len(active)
    if n_skipped:
        tier_label += f" ({n_skipped} supplementary-only claims skipped)"

    lexicon_terms = load_protected_terms(LEXICON_MD)

    if args.verbose:
        print(f"Loaded {len(lexicon_terms)} protected lexicon terms (read-only).")
        print(f"Streaming {MAIN_TEX} (line-by-line; no full-file buffer)...")
        print()

    results = audit_claims(MAIN_TEX, active, verbose=args.verbose)

    print_report(results, lexicon_terms, tier_label)
    write_json(results, lexicon_terms, tier_label)

    n_missing = sum(1 for r in results if r.status == MISSING)
    return 1 if n_missing else 0


if __name__ == "__main__":
    sys.exit(main())
