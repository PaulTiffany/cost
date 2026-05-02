#!/usr/bin/env python3
"""
In-the-Wild Compound Instructions with Deterministic Checks.

ICML 2026 Checklist #1: Real-world compound instructions that can be
deterministically verified, not hand-crafted synthetic scenarios.

Design principles:
1. Instructions sourced from realistic patterns (not adversarial)
2. Each constraint has a deterministic verifier (regex, AST, output check)
3. Varying rho estimates based on semantic overlap
4. No researcher degrees of freedom in evaluation

Categories of compound instructions:
1. Format + Content (e.g., "JSON with specific fields")
2. Style + Length (e.g., "formal and under 100 words")
3. Code + Constraints (e.g., "Python with no imports")
4. Multiple exclusions (e.g., "avoid X, Y, and Z")
"""

import json
import re
import ast
from dataclasses import dataclass, asdict
from typing import List, Dict, Callable, Optional, Tuple
from pathlib import Path
import hashlib


@dataclass
class CompoundInstruction:
    """A compound instruction with deterministic verifiers."""
    task_id: str
    description: str  # Human-readable task
    prompt: str       # The actual instruction given to the model
    constraints: List[Dict]  # Each has 'name', 'description', 'verifier_type'
    estimated_rho: float  # Estimated constraint similarity
    category: str     # format_content, style_length, code_constraint, exclusion


@dataclass
class VerificationResult:
    """Result of verifying a response against constraints."""
    task_id: str
    constraint_name: str
    passed: bool
    details: str


# ============================================================================
# Deterministic Verifiers
# ============================================================================

def verify_json_valid(response: str) -> Tuple[bool, str]:
    """Check if response is valid JSON."""
    try:
        json.loads(response.strip())
        return True, "Valid JSON"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"


def verify_json_has_fields(response: str, fields: List[str]) -> Tuple[bool, str]:
    """Check if JSON has required fields."""
    try:
        data = json.loads(response.strip())
        missing = [f for f in fields if f not in data]
        if missing:
            return False, f"Missing fields: {missing}"
        return True, f"Has all required fields: {fields}"
    except json.JSONDecodeError:
        return False, "Not valid JSON"


def verify_word_count(response: str, min_words: int = 0, max_words: int = float('inf')) -> Tuple[bool, str]:
    """Check word count is within bounds."""
    words = len(response.split())
    if words < min_words:
        return False, f"Too few words: {words} < {min_words}"
    if words > max_words:
        return False, f"Too many words: {words} > {max_words}"
    return True, f"Word count OK: {words}"


def verify_no_word(response: str, forbidden_word: str, case_sensitive: bool = False) -> Tuple[bool, str]:
    """Check that a specific word doesn't appear."""
    text = response if case_sensitive else response.lower()
    word = forbidden_word if case_sensitive else forbidden_word.lower()
    # Use word boundary to avoid matching substrings
    pattern = r'\b' + re.escape(word) + r'\b'
    if re.search(pattern, text):
        return False, f"Contains forbidden word: '{forbidden_word}'"
    return True, f"Does not contain: '{forbidden_word}'"


def verify_starts_with(response: str, prefix: str) -> Tuple[bool, str]:
    """Check response starts with given prefix."""
    if response.strip().startswith(prefix):
        return True, f"Starts with '{prefix}'"
    return False, f"Does not start with '{prefix}'"


def verify_ends_with(response: str, suffix: str) -> Tuple[bool, str]:
    """Check response ends with given suffix."""
    if response.strip().endswith(suffix):
        return True, f"Ends with '{suffix}'"
    return False, f"Does not end with '{suffix}'"


def verify_contains_pattern(response: str, pattern: str) -> Tuple[bool, str]:
    """Check response contains a regex pattern."""
    if re.search(pattern, response):
        return True, f"Contains pattern: {pattern}"
    return False, f"Missing pattern: {pattern}"


def verify_no_pattern(response: str, pattern: str) -> Tuple[bool, str]:
    """Check response does NOT contain a regex pattern."""
    if re.search(pattern, response):
        return False, f"Contains forbidden pattern: {pattern}"
    return True, f"Does not contain pattern: {pattern}"


def verify_python_valid(response: str) -> Tuple[bool, str]:
    """Check if response is valid Python code."""
    # Extract code from markdown if present
    code = response
    if '```python' in response:
        match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            code = match.group(1)
    elif '```' in response:
        match = re.search(r'```\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            code = match.group(1)

    try:
        ast.parse(code)
        return True, "Valid Python syntax"
    except SyntaxError as e:
        return False, f"Invalid Python: {e}"


def verify_python_no_imports(response: str) -> Tuple[bool, str]:
    """Check Python code has no import statements."""
    code = response
    if '```python' in response:
        match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            code = match.group(1)

    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "Contains import statement"
        return True, "No imports"
    except SyntaxError:
        return False, "Invalid Python"


def verify_python_has_function(response: str, func_name: str) -> Tuple[bool, str]:
    """Check Python code defines a specific function."""
    code = response
    if '```python' in response:
        match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            code = match.group(1)

    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return True, f"Defines function '{func_name}'"
        return False, f"Missing function '{func_name}'"
    except SyntaxError:
        return False, "Invalid Python"


def verify_line_count(response: str, min_lines: int = 0, max_lines: int = float('inf')) -> Tuple[bool, str]:
    """Check line count is within bounds."""
    lines = len([l for l in response.strip().split('\n') if l.strip()])
    if lines < min_lines:
        return False, f"Too few lines: {lines} < {min_lines}"
    if lines > max_lines:
        return False, f"Too many lines: {lines} > {max_lines}"
    return True, f"Line count OK: {lines}"


def verify_bullet_format(response: str) -> Tuple[bool, str]:
    """Check response uses bullet points."""
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    bullet_pattern = r'^[-*•]\s+'
    bullet_lines = [l for l in lines if re.match(bullet_pattern, l)]
    if len(bullet_lines) >= 2:
        return True, f"Has {len(bullet_lines)} bullet points"
    return False, f"Insufficient bullet points: {len(bullet_lines)}"


def verify_numbered_format(response: str) -> Tuple[bool, str]:
    """Check response uses numbered list."""
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    number_pattern = r'^\d+[\.\)]\s+'
    numbered_lines = [l for l in lines if re.match(number_pattern, l)]
    if len(numbered_lines) >= 2:
        return True, f"Has {len(numbered_lines)} numbered items"
    return False, f"Insufficient numbered items: {len(numbered_lines)}"


def verify_all_caps(response: str) -> Tuple[bool, str]:
    """Check response is all uppercase."""
    # Only check alphabetic characters
    alpha_chars = [c for c in response if c.isalpha()]
    if not alpha_chars:
        return True, "No alphabetic characters"
    if all(c.isupper() for c in alpha_chars):
        return True, "All uppercase"
    return False, "Contains lowercase letters"


def verify_no_caps(response: str) -> Tuple[bool, str]:
    """Check response has no uppercase (except possibly first letter)."""
    # Skip first character
    text = response[1:] if response else ""
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True, "No alphabetic characters"
    if all(c.islower() for c in alpha_chars):
        return True, "All lowercase (after first char)"
    return False, "Contains uppercase letters"


# ============================================================================
# Compound Instructions Dataset
# ============================================================================

COMPOUND_INSTRUCTIONS: List[CompoundInstruction] = [
    # Category: FORMAT + CONTENT (rho varies based on constraint relatedness)

    CompoundInstruction(
        task_id="json_recipe",
        description="Generate a recipe in JSON format with specific fields",
        prompt="Write a recipe for chocolate chip cookies. Output ONLY valid JSON with these exact fields: 'name', 'ingredients' (array), 'steps' (array), 'prep_time_minutes' (integer).",
        constraints=[
            {"name": "valid_json", "description": "Output is valid JSON", "verifier": "json_valid"},
            {"name": "has_name", "description": "JSON has 'name' field", "verifier": "json_field:name"},
            {"name": "has_ingredients", "description": "JSON has 'ingredients' field", "verifier": "json_field:ingredients"},
            {"name": "has_steps", "description": "JSON has 'steps' field", "verifier": "json_field:steps"},
            {"name": "has_prep_time", "description": "JSON has 'prep_time_minutes' field", "verifier": "json_field:prep_time_minutes"},
        ],
        estimated_rho=0.75,  # High: all format constraints, strongly correlated
        category="format_content"
    ),

    CompoundInstruction(
        task_id="json_no_string_escape",
        description="JSON that avoids certain characters",
        prompt="Describe a sunrise in JSON format with a 'description' field. Do NOT use quotation marks or apostrophes in the description text itself (the JSON structure quotes are fine).",
        constraints=[
            {"name": "valid_json", "description": "Output is valid JSON", "verifier": "json_valid"},
            {"name": "has_description", "description": "JSON has 'description' field", "verifier": "json_field:description"},
            {"name": "no_inner_quotes", "description": "Description value has no quotes/apostrophes", "verifier": "no_pattern_in_json_value:description:['\"]"},
        ],
        estimated_rho=0.35,  # Medium: format vs content restriction
        category="format_content"
    ),

    CompoundInstruction(
        task_id="bullets_concise",
        description="Bullet points with length constraint",
        prompt="List 5 benefits of exercise. Use bullet points. Each bullet must be under 10 words.",
        constraints=[
            {"name": "bullet_format", "description": "Uses bullet point format", "verifier": "bullet_format"},
            {"name": "min_bullets", "description": "Has at least 5 bullets", "verifier": "min_bullets:5"},
            {"name": "words_per_bullet", "description": "Each bullet under 10 words", "verifier": "max_words_per_bullet:10"},
        ],
        estimated_rho=0.45,  # Medium: format + length, partially coupled
        category="style_length"
    ),

    # Category: STYLE + LENGTH

    CompoundInstruction(
        task_id="formal_brief",
        description="Formal tone with strict word limit",
        prompt="Explain what machine learning is. Be formal (no contractions, no slang). Maximum 50 words.",
        constraints=[
            {"name": "word_limit", "description": "Under 50 words", "verifier": "max_words:50"},
            {"name": "no_contractions", "description": "No contractions", "verifier": "no_contractions"},
        ],
        estimated_rho=0.20,  # Low: style and length are different dimensions
        category="style_length"
    ),

    CompoundInstruction(
        task_id="casual_detailed",
        description="Casual tone with minimum content",
        prompt="Explain how to make coffee. Be casual (use contractions, informal language). Must be at least 100 words.",
        constraints=[
            {"name": "min_words", "description": "At least 100 words", "verifier": "min_words:100"},
            {"name": "has_contractions", "description": "Uses contractions", "verifier": "has_contractions"},
        ],
        estimated_rho=0.15,  # Low: different constraint dimensions
        category="style_length"
    ),

    CompoundInstruction(
        task_id="numbered_formal",
        description="Numbered list in formal style",
        prompt="List 3 reasons to learn programming. Use numbered format (1. 2. 3.). Formal tone, no contractions.",
        constraints=[
            {"name": "numbered_format", "description": "Uses numbered list", "verifier": "numbered_format"},
            {"name": "no_contractions", "description": "No contractions", "verifier": "no_contractions"},
            {"name": "min_items", "description": "At least 3 items", "verifier": "min_numbered:3"},
        ],
        estimated_rho=0.30,  # Low-medium: format + style
        category="style_length"
    ),

    # Category: CODE CONSTRAINTS

    CompoundInstruction(
        task_id="python_no_imports",
        description="Python code without using imports",
        prompt="Write a Python function called 'factorial' that computes n! without using any import statements.",
        constraints=[
            {"name": "valid_python", "description": "Valid Python syntax", "verifier": "python_valid"},
            {"name": "no_imports", "description": "No import statements", "verifier": "python_no_imports"},
            {"name": "has_factorial", "description": "Defines 'factorial' function", "verifier": "python_function:factorial"},
        ],
        estimated_rho=0.55,  # Medium-high: code constraints overlap
        category="code_constraint"
    ),

    CompoundInstruction(
        task_id="python_short",
        description="Python code with line limit",
        prompt="Write a Python function 'is_palindrome' that checks if a string is a palindrome. Maximum 5 lines of code.",
        constraints=[
            {"name": "valid_python", "description": "Valid Python syntax", "verifier": "python_valid"},
            {"name": "has_function", "description": "Defines 'is_palindrome' function", "verifier": "python_function:is_palindrome"},
            {"name": "max_lines", "description": "Maximum 5 lines", "verifier": "max_code_lines:5"},
        ],
        estimated_rho=0.40,  # Medium: code + length constraint
        category="code_constraint"
    ),

    CompoundInstruction(
        task_id="python_no_loops",
        description="Python without explicit loops",
        prompt="Write a Python function 'sum_list' that sums all numbers in a list. Do NOT use for/while loops (recursion or built-ins OK).",
        constraints=[
            {"name": "valid_python", "description": "Valid Python syntax", "verifier": "python_valid"},
            {"name": "has_function", "description": "Defines 'sum_list' function", "verifier": "python_function:sum_list"},
            {"name": "no_loops", "description": "No for/while loops", "verifier": "python_no_loops"},
        ],
        estimated_rho=0.50,  # Medium: code structure constraints
        category="code_constraint"
    ),

    # Category: MULTIPLE EXCLUSIONS

    CompoundInstruction(
        task_id="no_common_words",
        description="Avoid common words",
        prompt="Describe the ocean without using the words: water, blue, wave, fish, or sea.",
        constraints=[
            {"name": "no_water", "description": "Does not use 'water'", "verifier": "no_word:water"},
            {"name": "no_blue", "description": "Does not use 'blue'", "verifier": "no_word:blue"},
            {"name": "no_wave", "description": "Does not use 'wave'", "verifier": "no_word:wave"},
            {"name": "no_fish", "description": "Does not use 'fish'", "verifier": "no_word:fish"},
            {"name": "no_sea", "description": "Does not use 'sea'", "verifier": "no_word:sea"},
        ],
        estimated_rho=0.65,  # High: all exclusion constraints, correlated failure modes
        category="exclusion"
    ),

    CompoundInstruction(
        task_id="no_letters",
        description="Avoid certain letters",
        prompt="Write a sentence about cats that does not contain the letter 'e' or the letter 'a'.",
        constraints=[
            {"name": "no_e", "description": "Does not use letter 'e'", "verifier": "no_letter:e"},
            {"name": "no_a", "description": "Does not use letter 'a'", "verifier": "no_letter:a"},
            {"name": "about_cats", "description": "Mentions cats/feline", "verifier": "pattern:cat|felin|kitty|kitten"},
        ],
        estimated_rho=0.70,  # High: letter constraints very correlated
        category="exclusion"
    ),

    CompoundInstruction(
        task_id="alliteration_constraint",
        description="Alliteration with word exclusion",
        prompt="Write a sentence where every word starts with 'S'. Do not use the words 'she', 'so', or 'some'.",
        constraints=[
            {"name": "all_s", "description": "All words start with S", "verifier": "all_words_start:s"},
            {"name": "no_she", "description": "Does not use 'she'", "verifier": "no_word:she"},
            {"name": "no_so", "description": "Does not use 'so'", "verifier": "no_word:so"},
            {"name": "no_some", "description": "Does not use 'some'", "verifier": "no_word:some"},
        ],
        estimated_rho=0.60,  # High: heavily constrained domain
        category="exclusion"
    ),

    # Category: MIXED/COMPOUND

    CompoundInstruction(
        task_id="haiku_nature",
        description="Haiku format about specific topic",
        prompt="Write a haiku (5-7-5 syllables) about autumn. Do not use the word 'fall'.",
        constraints=[
            {"name": "three_lines", "description": "Exactly 3 lines", "verifier": "line_count:3:3"},
            {"name": "no_fall", "description": "Does not use 'fall'", "verifier": "no_word:fall"},
        ],
        estimated_rho=0.25,  # Low-medium: format + exclusion, different types
        category="mixed"
    ),

    CompoundInstruction(
        task_id="acronym_explanation",
        description="Explain acronym with format constraint",
        prompt="Explain what API stands for. Start with 'API stands for' and end with a period. Maximum 20 words.",
        constraints=[
            {"name": "starts_correct", "description": "Starts with 'API stands for'", "verifier": "starts_with:API stands for"},
            {"name": "ends_period", "description": "Ends with period", "verifier": "ends_with:."},
            {"name": "max_words", "description": "Maximum 20 words", "verifier": "max_words:20"},
        ],
        estimated_rho=0.35,  # Medium: format constraints, partially independent
        category="mixed"
    ),

    CompoundInstruction(
        task_id="email_subject",
        description="Email subject line constraints",
        prompt="Write an email subject line about a meeting. Must be under 50 characters. Must include the word 'urgent'. Must not use exclamation marks.",
        constraints=[
            {"name": "max_chars", "description": "Under 50 characters", "verifier": "max_chars:50"},
            {"name": "has_urgent", "description": "Contains 'urgent'", "verifier": "pattern:urgent"},
            {"name": "no_exclamation", "description": "No exclamation marks", "verifier": "no_pattern:!"},
        ],
        estimated_rho=0.30,  # Low-medium: mixed constraint types
        category="mixed"
    ),
]


# ============================================================================
# Verifier Registry
# ============================================================================

def verify_no_contractions(response: str) -> Tuple[bool, str]:
    """Check for common contractions."""
    contractions = ["n't", "'re", "'ve", "'ll", "'d", "'m", "'s"]
    text = response.lower()
    found = [c for c in contractions if c in text]
    if found:
        return False, f"Contains contractions: {found}"
    return True, "No contractions found"


def verify_has_contractions(response: str) -> Tuple[bool, str]:
    """Check that at least one contraction is used."""
    contractions = ["n't", "'re", "'ve", "'ll", "'d", "'m"]
    text = response.lower()
    found = [c for c in contractions if c in text]
    if found:
        return True, f"Has contractions: {found}"
    return False, "No contractions found"


def verify_no_letter(response: str, letter: str) -> Tuple[bool, str]:
    """Check that a specific letter doesn't appear."""
    if letter.lower() in response.lower():
        return False, f"Contains letter '{letter}'"
    return True, f"Does not contain letter '{letter}'"


def verify_all_words_start(response: str, letter: str) -> Tuple[bool, str]:
    """Check all words start with a specific letter."""
    words = re.findall(r'\b[a-zA-Z]+\b', response)
    bad_words = [w for w in words if not w.lower().startswith(letter.lower())]
    if bad_words:
        return False, f"Words not starting with '{letter}': {bad_words[:3]}"
    return True, f"All {len(words)} words start with '{letter}'"


def verify_max_chars(response: str, max_chars: int) -> Tuple[bool, str]:
    """Check character count."""
    chars = len(response.strip())
    if chars > max_chars:
        return False, f"Too many characters: {chars} > {max_chars}"
    return True, f"Character count OK: {chars}"


def verify_min_bullets(response: str, min_count: int) -> Tuple[bool, str]:
    """Check minimum bullet points."""
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    bullet_pattern = r'^[-*•]\s+'
    bullet_lines = [l for l in lines if re.match(bullet_pattern, l)]
    if len(bullet_lines) < min_count:
        return False, f"Too few bullets: {len(bullet_lines)} < {min_count}"
    return True, f"Has {len(bullet_lines)} bullets"


def verify_max_words_per_bullet(response: str, max_words: int) -> Tuple[bool, str]:
    """Check each bullet point is under word limit."""
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    bullet_pattern = r'^[-*•]\s+'
    for line in lines:
        if re.match(bullet_pattern, line):
            # Remove bullet marker and count words
            content = re.sub(bullet_pattern, '', line)
            words = len(content.split())
            if words > max_words:
                return False, f"Bullet too long: '{line[:30]}...' has {words} words"
    return True, f"All bullets under {max_words} words"


def verify_min_numbered(response: str, min_count: int) -> Tuple[bool, str]:
    """Check minimum numbered items."""
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    number_pattern = r'^\d+[\.\)]\s+'
    numbered = [l for l in lines if re.match(number_pattern, l)]
    if len(numbered) < min_count:
        return False, f"Too few numbered items: {len(numbered)} < {min_count}"
    return True, f"Has {len(numbered)} numbered items"


def verify_python_no_loops(response: str) -> Tuple[bool, str]:
    """Check Python code has no for/while loops."""
    code = response
    if '```python' in response:
        match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            code = match.group(1)

    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                return False, "Contains for/while loop"
        return True, "No loops"
    except SyntaxError:
        return False, "Invalid Python"


def verify_max_code_lines(response: str, max_lines: int) -> Tuple[bool, str]:
    """Check code line count."""
    code = response
    if '```python' in response:
        match = re.search(r'```python\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            code = match.group(1)

    lines = [l for l in code.strip().split('\n') if l.strip()]
    if len(lines) > max_lines:
        return False, f"Too many lines: {len(lines)} > {max_lines}"
    return True, f"Line count OK: {len(lines)}"


def verify_no_pattern_in_json_value(response: str, field: str, pattern: str) -> Tuple[bool, str]:
    """Check JSON field value doesn't contain pattern."""
    try:
        data = json.loads(response.strip())
        if field not in data:
            return False, f"Field '{field}' not found"
        value = str(data[field])
        if re.search(pattern, value):
            return False, f"Field '{field}' contains forbidden pattern"
        return True, f"Field '{field}' OK"
    except json.JSONDecodeError:
        return False, "Not valid JSON"


def run_verifier(verifier_spec: str, response: str) -> Tuple[bool, str]:
    """Run a verifier based on its specification string."""
    if ':' in verifier_spec:
        parts = verifier_spec.split(':')
        verifier_name = parts[0]
        args = parts[1:]
    else:
        verifier_name = verifier_spec
        args = []

    verifiers = {
        'json_valid': lambda r: verify_json_valid(r),
        'json_field': lambda r: verify_json_has_fields(r, [args[0]]),
        'max_words': lambda r: verify_word_count(r, 0, int(args[0])),
        'min_words': lambda r: verify_word_count(r, int(args[0]), float('inf')),
        'no_word': lambda r: verify_no_word(r, args[0]),
        'starts_with': lambda r: verify_starts_with(r, ':'.join(args)),  # Rejoin in case prefix has colons
        'ends_with': lambda r: verify_ends_with(r, args[0]),
        'pattern': lambda r: verify_contains_pattern(r, args[0]),
        'no_pattern': lambda r: verify_no_pattern(r, args[0]),
        'python_valid': lambda r: verify_python_valid(r),
        'python_no_imports': lambda r: verify_python_no_imports(r),
        'python_function': lambda r: verify_python_has_function(r, args[0]),
        'python_no_loops': lambda r: verify_python_no_loops(r),
        'line_count': lambda r: verify_line_count(r, int(args[0]), int(args[1])),
        'bullet_format': lambda r: verify_bullet_format(r),
        'numbered_format': lambda r: verify_numbered_format(r),
        'no_contractions': lambda r: verify_no_contractions(r),
        'has_contractions': lambda r: verify_has_contractions(r),
        'no_letter': lambda r: verify_no_letter(r, args[0]),
        'all_words_start': lambda r: verify_all_words_start(r, args[0]),
        'max_chars': lambda r: verify_max_chars(r, int(args[0])),
        'min_bullets': lambda r: verify_min_bullets(r, int(args[0])),
        'max_words_per_bullet': lambda r: verify_max_words_per_bullet(r, int(args[0])),
        'min_numbered': lambda r: verify_min_numbered(r, int(args[0])),
        'max_code_lines': lambda r: verify_max_code_lines(r, int(args[0])),
        'no_pattern_in_json_value': lambda r: verify_no_pattern_in_json_value(r, args[0], args[1]),
    }

    if verifier_name not in verifiers:
        return False, f"Unknown verifier: {verifier_name}"

    return verifiers[verifier_name](response)


def verify_response(task: CompoundInstruction, response: str) -> Dict:
    """Verify a response against all constraints of a task."""
    results = []
    all_passed = True

    for constraint in task.constraints:
        verifier_spec = constraint['verifier']
        passed, details = run_verifier(verifier_spec, response)
        results.append({
            'constraint': constraint['name'],
            'description': constraint['description'],
            'passed': passed,
            'details': details
        })
        if not passed:
            all_passed = False

    return {
        'task_id': task.task_id,
        'all_passed': all_passed,
        'n_constraints': len(task.constraints),
        'n_passed': sum(1 for r in results if r['passed']),
        'estimated_rho': task.estimated_rho,
        'category': task.category,
        'constraint_results': results
    }


def get_dataset_summary() -> Dict:
    """Get summary statistics of the dataset."""
    by_category = {}
    by_rho_bucket = {'low': [], 'medium': [], 'high': []}

    for task in COMPOUND_INSTRUCTIONS:
        cat = task.category
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(task.task_id)

        rho = task.estimated_rho
        if rho < 0.30:
            by_rho_bucket['low'].append(task.task_id)
        elif rho < 0.50:
            by_rho_bucket['medium'].append(task.task_id)
        else:
            by_rho_bucket['high'].append(task.task_id)

    return {
        'n_tasks': len(COMPOUND_INSTRUCTIONS),
        'n_total_constraints': sum(len(t.constraints) for t in COMPOUND_INSTRUCTIONS),
        'mean_constraints_per_task': sum(len(t.constraints) for t in COMPOUND_INSTRUCTIONS) / len(COMPOUND_INSTRUCTIONS),
        'by_category': {k: len(v) for k, v in by_category.items()},
        'by_rho_bucket': {k: len(v) for k, v in by_rho_bucket.items()},
        'rho_range': (
            min(t.estimated_rho for t in COMPOUND_INSTRUCTIONS),
            max(t.estimated_rho for t in COMPOUND_INSTRUCTIONS)
        )
    }


def export_for_experiment() -> List[Dict]:
    """Export tasks in format suitable for experiment runner."""
    return [
        {
            'task_id': task.task_id,
            'prompt': task.prompt,
            'description': task.description,
            'constraints': task.constraints,
            'estimated_rho': task.estimated_rho,
            'category': task.category,
            'verifier_hash': hashlib.md5(
                json.dumps([c['verifier'] for c in task.constraints]).encode()
            ).hexdigest()[:8]
        }
        for task in COMPOUND_INSTRUCTIONS
    ]


if __name__ == "__main__":
    print("In-the-Wild Compound Instructions Dataset")
    print("=" * 50)

    summary = get_dataset_summary()
    print(f"\nTotal tasks: {summary['n_tasks']}")
    print(f"Total constraints: {summary['n_total_constraints']}")
    print(f"Mean constraints/task: {summary['mean_constraints_per_task']:.1f}")
    print(f"Rho range: {summary['rho_range']}")

    print("\nBy category:")
    for cat, count in summary['by_category'].items():
        print(f"  {cat}: {count}")

    print("\nBy rho bucket:")
    for bucket, count in summary['by_rho_bucket'].items():
        print(f"  {bucket}: {count}")

    print("\n" + "=" * 50)
    print("Sample task verification:")

    # Test with a sample response
    sample_task = COMPOUND_INSTRUCTIONS[0]  # json_recipe
    sample_response = '''{
  "name": "Chocolate Chip Cookies",
  "ingredients": ["flour", "sugar", "butter", "chocolate chips", "eggs"],
  "steps": ["Mix dry ingredients", "Add wet ingredients", "Fold in chips", "Bake at 350F"],
  "prep_time_minutes": 15
}'''

    print(f"\nTask: {sample_task.task_id}")
    print(f"Prompt: {sample_task.prompt[:80]}...")
    print(f"\nSample response (manually crafted for testing):")
    print(sample_response[:200] + "...")

    result = verify_response(sample_task, sample_response)
    print(f"\nVerification result:")
    print(f"  All passed: {result['all_passed']}")
    print(f"  Passed: {result['n_passed']}/{result['n_constraints']}")
    for cr in result['constraint_results']:
        status = "PASS" if cr['passed'] else "FAIL"
        print(f"  [{status}] {cr['constraint']}: {cr['details']}")
