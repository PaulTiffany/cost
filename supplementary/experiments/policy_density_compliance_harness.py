#!/usr/bin/env python3
"""
policy_density_compliance_harness.py

Policy-Density compliance stress-test for the diagonal-cost prediction.
Proposed by Gemini collaborator (2026-05): replaces synthetic "k constraints
in a JSON" benchmarks with a naturally-scaling compliance task where k grows
as the policy tier escalates from Basic to Enterprise.

Core idea:
  - One draft corporate email (deliberately violating most rules)
  - Four policy tiers: Basic (k=2), Standard (k=5), Strict (k=12), Enterprise (k=30)
  - Each tier inherits the previous tier's rules
  - Two protocols: one_shot (dump all rules) vs staged (rules in chunks)
  - Deterministic compliance linter: regex / keyword / length / structure checks

Geometric rationale:
  - Tier 3+ has GENUINE inter-rule conflict, e.g. T2.no_passive_voice forbids
    passive voice while T3.legal_disclaimer requires the literal sentence "This
    communication is confidential and intended only for the recipient." which
    is itself passive voice. The rho > 0 here is empirical, not synthetic.
  - The hypothesis: one_shot pass rate plummets at tier 3 -> 4; staged
    sustains it. The ratio measures the "diagonal cost" in policy-density units.

Models: full Claude family via Anthropic SDK (handles opus-4.7's no-temperature
constraint identically to the existing opus-4.7 addition harnesses).

Usage:
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    python policy_density_compliance_harness.py
    python policy_density_compliance_harness.py --trials 5 --models opus-4.7,sonnet-4.5
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
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, List, Tuple, Optional

N_WORKERS = 6
DEFAULT_TRIALS = 3
MAX_TOKENS = 1024

CLAUDE_FAMILY = {
    "haiku-3": "claude-3-haiku-20240307",  # 404 since 2026-05; deprecated by Anthropic
    "sonnet-4": "claude-sonnet-4-20250514",
    "opus-4": "claude-opus-4-20250514",
    "opus-4.1": "claude-opus-4-1-20250805",
    "haiku-4.5": "claude-haiku-4-5-20251001",
    "sonnet-4.5": "claude-sonnet-4-5-20250929",
    "opus-4.5": "claude-opus-4-5-20251101",
    "opus-4.6": "claude-opus-4-6",
    "sonnet-4.6": "claude-sonnet-4-6",
    "opus-4.7": "claude-opus-4-7",
}

OPUS47_MODELS = {"opus-4.7"}  # only opus-4.7 rejects temperature; 4.6 generation accepts
LEGACY_TEMPERATURE = 0.0

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = "supplementary/experiments/policy_density_compliance_harness.py"
OUTPUT_DIR = REPO_ROOT / "supplementary/experiments/outputs/policy_density"
OUTPUT_PATH = OUTPUT_DIR / "policy_density_results.json"


# =============================================================================
# DRAFT EMAIL (the seed text the model rewrites)
# =============================================================================

DRAFT_EMAIL = """Subject: New Product Launch

Hi everyone,

We're super excited to announce our amazing new platform! It's the best thing
we've ever built and we're confident you'll love it. Click here to check it out.

This stunning product was designed by our world-class team and we guarantee
you'll see results immediately. Our competitor's product can't even come close.

Cheers,
The Team"""


# =============================================================================
# RULE VERIFIERS (each returns (passed: bool, msg: str))
# =============================================================================

def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))

def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

# --- Tier 1 (k=2): Basic ---

def v_format_salutation(t: str) -> Tuple[bool, str]:
    if re.search(r"^\s*(Dear|Hello|Greetings)\b", t, re.MULTILINE):
        return True, "Has formal salutation"
    return False, "Missing formal salutation"

def v_no_profanity(t: str) -> Tuple[bool, str]:
    blacklist = ["damn", "hell", "crap", "stupid", "idiot"]
    found = [w for w in blacklist if re.search(rf"\b{w}\b", t, re.IGNORECASE)]
    return (False, f"Found banned words: {found}") if found else (True, "Clean")

# --- Tier 2 (k=5): + brand voice ---

PASSIVE_PATTERNS = [
    r"\b(is|are|was|were|be|been|being)\s+\w+ed\b",
    r"\b(is|are|was|were|be|been|being)\s+\w+en\b",
]

def v_no_passive_voice(t: str) -> Tuple[bool, str]:
    for p in PASSIVE_PATTERNS:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            return False, f"Passive at: '{m.group(0)}'"
    return True, "No passive voice detected"

SUPERLATIVES = [
    "amazing", "stunning", "revolutionary", "world-class", "best-in-class",
    "incredible", "phenomenal", "unprecedented", "groundbreaking", "cutting-edge",
]

def v_no_superlatives(t: str) -> Tuple[bool, str]:
    found = [w for w in SUPERLATIVES if re.search(rf"\b{re.escape(w)}\b", t, re.IGNORECASE)]
    return (False, f"Found superlatives: {found}") if found else (True, "Plain language")

def v_uses_company_name(t: str) -> Tuple[bool, str]:
    if "Acme Corp" in t:
        return True, "References Acme Corp"
    return False, "Missing 'Acme Corp'"

# --- Tier 3 (k=12): + legal/regulatory ---

LEGAL_DISCLAIMER = "This communication is confidential and intended only for the recipient."
REGULATORY_NOTICE = "Regulated by the SEC."
TERMS_LINE = "Terms of Service available at acme.com/terms"
UNSUB_LINE = "To unsubscribe, reply STOP."

def v_contains_legal_disclaimer(t: str) -> Tuple[bool, str]:
    return (True, "Has legal disclaimer") if LEGAL_DISCLAIMER in t else (False, "Missing legal disclaimer (verbatim required)")

def v_contains_regulatory_notice(t: str) -> Tuple[bool, str]:
    return (True, "Has SEC notice") if REGULATORY_NOTICE in t else (False, "Missing 'Regulated by the SEC.'")

PROMISE_WORDS = ["guarantee", "guarantees", "guaranteed", "promise", "promises", "ensure", "ensures", "definitely will", "always", "never fails"]

def v_no_forward_looking_promise(t: str) -> Tuple[bool, str]:
    found = [w for w in PROMISE_WORDS if re.search(rf"\b{re.escape(w)}\b", t, re.IGNORECASE)]
    return (False, f"Promise words: {found}") if found else (True, "No promises")

PII_REQUEST = ["SSN", "social security", "password", "PIN", "credit card number"]

def v_no_pii_request(t: str) -> Tuple[bool, str]:
    found = [w for w in PII_REQUEST if re.search(re.escape(w), t, re.IGNORECASE)]
    return (False, f"PII request: {found}") if found else (True, "No PII solicitation")

def v_includes_unsubscribe(t: str) -> Tuple[bool, str]:
    return (True, "Has unsub line") if UNSUB_LINE in t else (False, "Missing unsubscribe line (verbatim required)")

def v_cites_terms(t: str) -> Tuple[bool, str]:
    return (True, "Cites ToS") if TERMS_LINE in t else (False, "Missing ToS citation (verbatim required)")

def v_no_negative_competitor(t: str) -> Tuple[bool, str]:
    if re.search(r"\b(competitor|alternative provider|rival|opponent)s?\b", t, re.IGNORECASE):
        return False, "References competitor"
    return True, "No competitor reference"

# --- Tier 4 (k=30): + enterprise prohibitions ---

def v_max_words(t: str) -> Tuple[bool, str]:
    n = _word_count(t)
    return (True, f"{n} words") if n <= 400 else (False, f"{n} words exceeds 400")

def v_min_words(t: str) -> Tuple[bool, str]:
    n = _word_count(t)
    return (True, f"{n} words") if n >= 100 else (False, f"{n} words below 100")

def v_max_sentence_length(t: str) -> Tuple[bool, str]:
    for s in _sentences(t):
        n = _word_count(s)
        if n > 25:
            return False, f"Sentence has {n} words: '{s[:60]}...'"
    return True, "All sentences <=25 words"

def v_no_em_dashes(t: str) -> Tuple[bool, str]:
    return (False, "Contains em dash") if "—" in t or "--" in t else (True, "No em dashes")

def v_no_exclamation(t: str) -> Tuple[bool, str]:
    return (False, "Contains '!'") if "!" in t else (True, "No exclamations")

FIRST_PERSON_PLURAL = ["we", "our", "us", "ours", "ourselves"]

def v_no_first_person_plural(t: str) -> Tuple[bool, str]:
    for w in FIRST_PERSON_PLURAL:
        if re.search(rf"\b{w}\b", t, re.IGNORECASE):
            return False, f"Uses first-person plural: '{w}'"
    return True, "No 'we/our/us'"

CONTRACTIONS = ["don't", "won't", "can't", "we're", "it's", "you're", "they're", "I'm", "isn't", "aren't", "wasn't", "weren't", "hasn't", "haven't", "hadn't", "doesn't", "didn't", "couldn't", "wouldn't", "shouldn't"]

def v_no_contractions(t: str) -> Tuple[bool, str]:
    found = [c for c in CONTRACTIONS if c.lower() in t.lower()]
    return (False, f"Contractions: {found[:3]}") if found else (True, "No contractions")

JARGON = ["synergy", "leverage", "circle back", "deep dive", "low-hanging fruit", "move the needle", "bandwidth", "actionable"]

def v_no_jargon(t: str) -> Tuple[bool, str]:
    found = [w for w in JARGON if re.search(rf"\b{re.escape(w)}\b", t, re.IGNORECASE)]
    return (False, f"Jargon: {found}") if found else (True, "No jargon")

def v_includes_iso_date(t: str) -> Tuple[bool, str]:
    return (True, "Has ISO date") if re.search(r"\b20\d{2}-\d{2}-\d{2}\b", t) else (False, "Missing YYYY-MM-DD date")

def v_includes_reference_number(t: str) -> Tuple[bool, str]:
    return (True, "Has ref number") if re.search(r"\bRef:\s*\d+\b", t) else (False, "Missing 'Ref: <digits>'")

def _paragraph_count(t: str) -> int:
    return len([p for p in re.split(r"\n\s*\n", t.strip()) if p.strip()])

def v_min_paragraphs(t: str) -> Tuple[bool, str]:
    n = _paragraph_count(t)
    return (True, f"{n} paragraphs") if n >= 3 else (False, f"{n} paragraphs (need >=3)")

def v_max_paragraphs(t: str) -> Tuple[bool, str]:
    n = _paragraph_count(t)
    return (True, f"{n} paragraphs") if n <= 10 else (False, f"{n} paragraphs (need <=10)")

CTA_VERBS = ["click here", "buy now", "act now", "subscribe today", "sign up now"]

def v_no_imperative_cta(t: str) -> Tuple[bool, str]:
    found = [w for w in CTA_VERBS if re.search(re.escape(w), t, re.IGNORECASE)]
    return (False, f"CTA: {found}") if found else (True, "No imperative CTAs")

INDUSTRY_DISCLAIMER = "Past performance is not indicative of future results."

def v_industry_disclaimer(t: str) -> Tuple[bool, str]:
    return (True, "Has industry disclaimer") if INDUSTRY_DISCLAIMER in t else (False, "Missing industry disclaimer (verbatim required)")

EMOJI_RANGES = [
    (0x1F300, 0x1F5FF), (0x1F600, 0x1F64F), (0x1F680, 0x1F6FF),
    (0x1F700, 0x1F77F), (0x1F900, 0x1F9FF), (0x2600, 0x26FF), (0x2700, 0x27BF),
]

def v_no_emojis(t: str) -> Tuple[bool, str]:
    for ch in t:
        cp = ord(ch)
        for lo, hi in EMOJI_RANGES:
            if lo <= cp <= hi:
                return False, f"Emoji at codepoint U+{cp:04X}"
    return True, "No emojis"

def v_signature_block(t: str) -> Tuple[bool, str]:
    lines = [l for l in t.strip().splitlines() if l.strip()]
    if len(lines) < 3:
        return False, "Less than 3 final lines"
    last3 = lines[-3:]
    if not re.search(r"\bAcme Corp\b", "\n".join(last3)):
        return False, "Last 3 lines lack 'Acme Corp'"
    return True, "Signature block present"

def v_no_questions(t: str) -> Tuple[bool, str]:
    return (False, "Contains '?'") if "?" in t else (True, "No questions")

def v_consistent_currency(t: str) -> Tuple[bool, str]:
    if "$" not in t:
        return True, "No currency"
    bad = re.findall(r"\$\d+(?!\.\d{2})\b", t)
    return (False, f"Bad currency: {bad}") if bad else (True, "Currency well-formatted")

def v_no_all_caps_word(t: str) -> Tuple[bool, str]:
    for m in re.finditer(r"\b[A-Z]{3,}\b", t):
        w = m.group(0)
        if w not in {"SEC", "STOP", "PII", "ToS"}:
            return False, f"All-caps word: '{w}'"
    return True, "No all-caps shouting"

def v_no_double_spaces(t: str) -> Tuple[bool, str]:
    if re.search(r"  +", t):
        return False, "Double spaces present"
    return True, "Single-spaced"

def v_subject_line_present(t: str) -> Tuple[bool, str]:
    return (True, "Has Subject:") if re.match(r"^Subject:\s+\S", t) else (False, "Missing Subject: line")

def v_subject_title_case(t: str) -> Tuple[bool, str]:
    m = re.match(r"^Subject:\s+(.+)$", t, re.MULTILINE)
    if not m:
        return False, "No Subject line"
    subj = m.group(1).strip()
    minor = {"a", "an", "the", "and", "but", "or", "for", "on", "in", "to", "of"}
    words = subj.split()
    for i, w in enumerate(words):
        wc = re.sub(r"[^A-Za-z]", "", w)
        if not wc:
            continue
        if i == 0 or wc.lower() not in minor:
            if not wc[0].isupper():
                return False, f"Subject not title-case at '{w}'"
    return True, "Subject in title case"


@dataclass
class Rule:
    name: str
    description: str
    verify: Callable[[str], Tuple[bool, str]]
    tier: int


# Build the cumulative rule set. Tier N includes Tiers 1..N.
# Tier sizes: T1=2, T2=5, T3=8, T4=12, T5=16, T6=22, T7=28, T8=34
def build_all_rules() -> List[Rule]:
    return [
        # Tier 1 (k=2): Basic - format + safety
        Rule("format_salutation", "Email begins with formal salutation (Dear/Hello/Greetings).", v_format_salutation, 1),
        Rule("no_profanity", "No profanity (damn, hell, crap, stupid, idiot).", v_no_profanity, 1),
        # Tier 2 (k=5): + brand voice core
        Rule("no_passive_voice", "No passive voice (avoid 'is/was/are/were + past participle').", v_no_passive_voice, 2),
        Rule("no_superlatives", "No marketing superlatives (amazing, stunning, world-class, etc.).", v_no_superlatives, 2),
        Rule("uses_company_name", "Must reference 'Acme Corp' verbatim at least once.", v_uses_company_name, 2),
        # Tier 3 (k=8): + extended brand voice
        Rule("no_first_person_plural", "Do not use 'we', 'our', 'us', 'ours', or 'ourselves'.", v_no_first_person_plural, 3),
        Rule("no_jargon", "No corporate jargon (synergy, leverage, circle back, etc.).", v_no_jargon, 3),
        Rule("no_em_dashes", "No em dashes (— or --).", v_no_em_dashes, 3),
        # Tier 4 (k=12): + legal core
        Rule("contains_legal_disclaimer", f"Must contain the verbatim sentence: '{LEGAL_DISCLAIMER}'", v_contains_legal_disclaimer, 4),
        Rule("contains_regulatory_notice", f"Must contain the verbatim sentence: '{REGULATORY_NOTICE}'", v_contains_regulatory_notice, 4),
        Rule("no_forward_looking_promise", "No promise/guarantee/ensure language.", v_no_forward_looking_promise, 4),
        Rule("no_pii_request", "No request for SSN, password, PIN, or credit card number.", v_no_pii_request, 4),
        # Tier 5 (k=16): + extended legal
        Rule("includes_unsubscribe", f"Must include the verbatim line: '{UNSUB_LINE}'", v_includes_unsubscribe, 5),
        Rule("cites_terms", f"Must include the verbatim line: '{TERMS_LINE}'", v_cites_terms, 5),
        Rule("no_negative_competitor", "Do not reference competitors or rivals.", v_no_negative_competitor, 5),
        Rule("industry_disclaimer", f"Must contain verbatim: '{INDUSTRY_DISCLAIMER}'", v_industry_disclaimer, 5),
        # Tier 6 (k=22): + structural / metadata
        Rule("max_words", "Total word count must not exceed 400.", v_max_words, 6),
        Rule("min_words", "Total word count must be at least 100.", v_min_words, 6),
        Rule("max_sentence_length", "No sentence may exceed 25 words.", v_max_sentence_length, 6),
        Rule("includes_iso_date", "Include a date in YYYY-MM-DD format.", v_includes_iso_date, 6),
        Rule("includes_reference_number", "Include 'Ref: <digits>' somewhere in the body.", v_includes_reference_number, 6),
        Rule("subject_line_present", "Email must begin with 'Subject: <text>' line.", v_subject_line_present, 6),
        # Tier 7 (k=28): + style polish
        Rule("no_exclamation", "No exclamation points.", v_no_exclamation, 7),
        Rule("no_questions", "No question marks.", v_no_questions, 7),
        Rule("no_contractions", "No contractions (don't, won't, we're, it's, etc.).", v_no_contractions, 7),
        Rule("consistent_currency", "Any '$' amount must be formatted as $X.XX.", v_consistent_currency, 7),
        Rule("no_emojis", "No emoji characters.", v_no_emojis, 7),
        Rule("no_double_spaces", "No double spaces.", v_no_double_spaces, 7),
        # Tier 8 (k=34): + enterprise polish (hardest layer)
        Rule("min_paragraphs", "Body must contain at least 3 paragraphs (separated by blank lines).", v_min_paragraphs, 8),
        Rule("max_paragraphs", "Body must contain at most 10 paragraphs.", v_max_paragraphs, 8),
        Rule("no_imperative_cta", "No imperative call-to-action (click here, buy now, etc.).", v_no_imperative_cta, 8),
        Rule("no_all_caps_word", "No all-caps shouting (3+ char all-caps words, except SEC/STOP/PII/ToS).", v_no_all_caps_word, 8),
        Rule("subject_title_case", "Subject line must be in title case.", v_subject_title_case, 8),
        Rule("signature_block", "End with a 3-line signature block including 'Acme Corp'.", v_signature_block, 8),
    ]


def rules_for_tier(tier: int) -> List[Rule]:
    """Cumulative: tier N includes all rules with .tier <= N."""
    return [r for r in build_all_rules() if r.tier <= tier]


# =============================================================================
# PROMPT BUILDERS
# =============================================================================

def format_rule_list(rules: List[Rule]) -> str:
    return "\n".join(f"  {i+1}. [{r.name}] {r.description}" for i, r in enumerate(rules))


def prompt_one_shot(rules: List[Rule]) -> str:
    return f"""You will rewrite the email below so that it satisfies ALL {len(rules)} of the following corporate compliance rules.

DRAFT EMAIL:
\"\"\"
{DRAFT_EMAIL}
\"\"\"

COMPLIANCE RULES (must satisfy ALL):
{format_rule_list(rules)}

Output ONLY the rewritten email between triple backticks, with no preamble or explanation:
```
<rewritten email here>
```"""


def stage_chunks(rules: List[Rule], n_stages: int = 3) -> List[List[Rule]]:
    """Split rules into roughly equal chunks, preserving tier order."""
    if not rules:
        return []
    n = len(rules)
    size = max(1, (n + n_stages - 1) // n_stages)
    return [rules[i:i+size] for i in range(0, n, size)]


def prompt_staged_first(stage_rules: List[Rule], stage_idx: int, total_stages: int) -> str:
    return f"""Stage {stage_idx+1} of {total_stages}: rewrite the email below to satisfy ONLY these {len(stage_rules)} rules. Do not worry about other rules yet.

DRAFT EMAIL:
\"\"\"
{DRAFT_EMAIL}
\"\"\"

RULES FOR THIS STAGE:
{format_rule_list(stage_rules)}

Output ONLY the rewritten email between triple backticks:
```
<rewritten email>
```"""


def prompt_staged_next(prev_text: str, prev_rules: List[Rule], stage_rules: List[Rule], stage_idx: int, total_stages: int) -> str:
    return f"""Stage {stage_idx+1} of {total_stages}: take the email below and ALSO satisfy these {len(stage_rules)} additional rules. CRITICAL: keep ALL previously-applied rules satisfied. Do not regress.

CURRENT EMAIL:
\"\"\"
{prev_text}
\"\"\"

PREVIOUSLY-APPLIED RULES (must remain satisfied):
{format_rule_list(prev_rules)}

NEW RULES FOR THIS STAGE:
{format_rule_list(stage_rules)}

Output ONLY the updated email between triple backticks:
```
<updated email>
```"""


# =============================================================================
# OUTPUT EXTRACTION + SCORING
# =============================================================================

def extract_email(response: str) -> str:
    m = re.search(r"```(?:\w+)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return response.strip()


def score(text: str, rules: List[Rule]) -> dict:
    results = {}
    n_pass = 0
    for r in rules:
        try:
            ok, msg = r.verify(text)
        except Exception as e:
            ok, msg = False, f"verifier error: {e}"
        results[r.name] = {"pass": ok, "msg": msg}
        if ok:
            n_pass += 1
    return {
        "n_pass": n_pass,
        "n_total": len(rules),
        "all_pass": n_pass == len(rules),
        "rule_results": results,
    }


# =============================================================================
# API
# =============================================================================

def call_anthropic(client, model_id: str, prompt: str, max_tokens: int = MAX_TOKENS) -> Tuple[str, int]:
    kwargs = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    # opus-4.7 rejects temperature; everyone else uses LEGACY_TEMPERATURE
    if model_id != CLAUDE_FAMILY["opus-4.7"]:
        kwargs["temperature"] = LEGACY_TEMPERATURE
    resp = client.messages.create(**kwargs)
    text = "".join(block.text for block in resp.content if hasattr(block, "text"))
    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    return text, tokens


# =============================================================================
# RUN
# =============================================================================

@dataclass
class CellResult:
    model_name: str
    model_id: str
    tier: int
    k: int
    protocol: str
    trial: int
    n_pass: int
    n_total: int
    all_pass: bool
    rewritten_text: str
    rule_results: dict
    tokens_used: int
    elapsed_sec: float


def run_one_cell(client, model_name: str, tier: int, protocol: str, trial: int) -> CellResult:
    model_id = CLAUDE_FAMILY[model_name]
    rules = rules_for_tier(tier)
    t0 = time.time()
    tokens_total = 0

    if protocol == "one_shot":
        text, toks = call_anthropic(client, model_id, prompt_one_shot(rules))
        tokens_total += toks
        final = extract_email(text)
    else:  # staged
        chunks = stage_chunks(rules, n_stages=min(3, len(rules)))
        if not chunks:
            final = ""
        else:
            text, toks = call_anthropic(client, model_id, prompt_staged_first(chunks[0], 0, len(chunks)))
            tokens_total += toks
            current = extract_email(text)
            applied: List[Rule] = list(chunks[0])
            for i in range(1, len(chunks)):
                text, toks = call_anthropic(client, model_id, prompt_staged_next(current, applied, chunks[i], i, len(chunks)))
                tokens_total += toks
                current = extract_email(text)
                applied.extend(chunks[i])
            final = current

    sc = score(final, rules)
    return CellResult(
        model_name=model_name,
        model_id=model_id,
        tier=tier,
        k=len(rules),
        protocol=protocol,
        trial=trial,
        n_pass=sc["n_pass"],
        n_total=sc["n_total"],
        all_pass=sc["all_pass"],
        rewritten_text=final,
        rule_results=sc["rule_results"],
        tokens_used=tokens_total,
        elapsed_sec=time.time() - t0,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--models", type=str, default=",".join(CLAUDE_FAMILY.keys()),
                        help="comma-separated model names from CLAUDE_FAMILY")
    parser.add_argument("--tiers", type=str, default="1,2,3,4,5,6,7,8")
    parser.add_argument("--protocols", type=str, default="one_shot,staged")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip() in CLAUDE_FAMILY]
    tiers = [int(t) for t in args.tiers.split(",")]
    protocols = [p.strip() for p in args.protocols.split(",")]

    if args.dry_run:
        for tier in tiers:
            rs = rules_for_tier(tier)
            print(f"Tier {tier}: k={len(rs)}")
            for r in rs:
                ok, msg = r.verify(DRAFT_EMAIL)
                print(f"  draft {'PASS' if ok else 'FAIL'} {r.name}: {msg}")
        return 0

    try:
        import anthropic
    except ImportError:
        print("anthropic package not found", file=sys.stderr); return 1
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY env var required", file=sys.stderr); return 1
    client = anthropic.Anthropic(api_key=api_key)

    cells = [(m, t, p, tr) for m in models for t in tiers for p in protocols for tr in range(1, args.trials + 1)]
    print(f"Running {len(cells)} cells: {len(models)} models x {len(tiers)} tiers x {len(protocols)} protocols x {args.trials} trials "
          f"(parallel N_WORKERS={N_WORKERS})", flush=True)
    t_start = time.time()

    results: List[CellResult] = []
    print_lock = threading.Lock()

    def task(args_tuple):
        m, tier, p, tr = args_tuple
        try:
            r = run_one_cell(client, m, tier, p, tr)
            with print_lock:
                tag = "PASS" if r.all_pass else f"{r.n_pass}/{r.n_total}"
                print(f"  [done] {m:<12} T{tier} k={r.k:>2} {p:<8} trial {tr}: {tag} ({r.elapsed_sec:.1f}s)", flush=True)
            return r
        except Exception as e:
            with print_lock:
                print(f"  [FAIL] {m} T{tier} {p} trial {tr}: {e}", file=sys.stderr, flush=True)
            return None

    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futures = [ex.submit(task, c) for c in cells]
        for fut in as_completed(futures):
            r = fut.result()
            if r is not None:
                results.append(r)

    elapsed = time.time() - t_start

    # Aggregate: pass-rate per (model, tier, protocol)
    from collections import defaultdict
    agg = defaultdict(lambda: {"n_trials": 0, "n_all_pass": 0, "mean_n_pass": 0.0, "k": 0})
    for r in results:
        key = (r.model_name, r.tier, r.protocol)
        agg[key]["n_trials"] += 1
        agg[key]["n_all_pass"] += int(r.all_pass)
        agg[key]["mean_n_pass"] += r.n_pass
        agg[key]["k"] = r.k
    summary = []
    for (m, tier, p), v in agg.items():
        v["all_pass_rate"] = v["n_all_pass"] / v["n_trials"] if v["n_trials"] else 0.0
        v["mean_n_pass"] = v["mean_n_pass"] / v["n_trials"] if v["n_trials"] else 0.0
        summary.append({"model": m, "tier": tier, "k": v["k"], "protocol": p, **v})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = {
        "_meta": {
            "experiment": "policy_density_compliance",
            "via": "Anthropic SDK",
            "generator_script": SCRIPT_PATH,
            "script_hash": script_hash,
            "models": models,
            "model_ids": {m: CLAUDE_FAMILY[m] for m in models},
            "tiers": tiers,
            "protocols": protocols,
            "trials_per_cell": args.trials,
            "n_workers": N_WORKERS,
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - elapsed)),
            "finished_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_sec": elapsed,
            "n_cells": len(results),
            "draft_email": DRAFT_EMAIL,
            "rule_set_size_by_tier": {t: len(rules_for_tier(t)) for t in tiers},
            "methodology_note": "Claude family compliance stress-test proposed by Gemini collaborator. "
                                "Tiered policy bundles scale k naturally (2/5/12/30). Tier 3+ rules contain "
                                "GENUINE inter-rule conflict (e.g. no_passive_voice T2 vs verbatim "
                                "passive-voice legal disclaimer T3), so rho > 0 is empirical, not synthetic.",
            "sampling_note": "All non-opus-4.7 models use temperature=0.0. opus-4.7 rejects the "
                             "temperature parameter (per Anthropic Opus 4.7 docs); for that model only, "
                             "temperature is omitted and Anthropic's server-side default sampling is used. "
                             "Source: https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7",
        },
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH}")
    print("\n=== Pass rates by (model, tier, protocol) ===")
    print(f"{'model':<12} {'tier':<5} {'k':<4} {'protocol':<10} {'pass_rate':<10} {'mean_n_pass':<12}")
    for s in sorted(summary, key=lambda x: (x["model"], x["tier"], x["protocol"])):
        print(f"  {s['model']:<10} T{s['tier']:<4} {s['k']:<4} {s['protocol']:<10} {s['all_pass_rate']*100:>5.0f}%      {s['mean_n_pass']:.1f}/{s['k']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
