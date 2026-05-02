#!/usr/bin/env python3
"""
Deterministic verifiers for code constraint experiment.

Verifier A: Functional correctness (subprocess + timeout)
Verifier B: Format constraints (AST parsing)

Both are deterministic and require no LLM judgment.
"""

import ast
import re
import subprocess
import sys
import tempfile
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path


@dataclass
class FormatRules:
    """Format constraints for Verifier B."""
    max_lines: int = 50
    no_imports: bool = False
    no_loops: bool = False  # no for/while
    no_recursion: bool = False  # function can't call itself
    require_docstring: bool = False
    single_function: bool = False  # only one def allowed
    forbidden_builtins: List[str] = None  # e.g., ['sorted', 'map', 'filter']

    def __post_init__(self):
        if self.forbidden_builtins is None:
            self.forbidden_builtins = []


# Tier definitions: increasing format constraint strictness
# Tasks/tests stay constant; only format rules change
FORMAT_TIERS = {
    "control": FormatRules(
        max_lines=50,
        no_imports=False,
        no_loops=False,
        require_docstring=False,
        single_function=False,
    ),
    "low": FormatRules(
        max_lines=25,
        no_imports=True,
        no_loops=False,
        require_docstring=True,
        single_function=True,
    ),
    "moderate": FormatRules(
        max_lines=15,
        no_imports=True,
        no_loops=False,
        require_docstring=True,
        single_function=True,
        forbidden_builtins=["sorted", "reversed"],
    ),
    "high": FormatRules(
        max_lines=10,
        no_imports=True,
        no_loops=True,  # Forces recursion or comprehensions
        require_docstring=True,
        single_function=True,
        forbidden_builtins=["sorted", "reversed", "map", "filter"],
    ),
}


def extract_code_block(response: str) -> Optional[str]:
    """
    Extract the last ```python code block from response.
    Returns None if no valid code block found.

    This is deterministic: no LLM judgment, just regex parsing.
    """
    # Find all python code blocks
    pattern = r'```python\s*\n(.*?)```'
    matches = re.findall(pattern, response, re.DOTALL)

    if matches:
        return matches[-1].strip()  # Take the last one

    # Fallback: try generic code blocks
    pattern = r'```\s*\n(.*?)```'
    matches = re.findall(pattern, response, re.DOTALL)

    if matches:
        # Check if it looks like Python (has def or common Python syntax)
        for match in reversed(matches):
            if 'def ' in match or 'return ' in match:
                return match.strip()

    return None


def verify_functional(code: str, test_code: str, timeout: float = 5.0) -> Tuple[bool, str]:
    """
    Verifier A: Run code + tests in subprocess with timeout.

    Returns (passed: bool, message: str)

    Uses subprocess isolation:
    - Separate process (can't crash main)
    - Hard timeout (can't hang)
    - Temp directory (can't pollute)
    - Stripped environment
    """
    if code is None:
        return False, "No code block found in response"

    # Combine code and tests
    full_code = f"{code}\n\n# Tests\n{test_code}"

    # Write to temp file (UTF-8 for Unicode chars like ≥)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(full_code)
        temp_path = f.name

    try:
        # Run in subprocess with timeout
        result = subprocess.run(
            [sys.executable, "-I", temp_path],  # -I = isolated mode
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
            env={"PATH": os.environ.get("PATH", "")},  # Minimal env
        )

        if result.returncode == 0:
            return True, "All tests passed"
        else:
            error_msg = result.stderr[:500] if result.stderr else "Unknown error"
            return False, f"Tests failed: {error_msg}"

    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, f"Execution error: {str(e)[:200]}"
    finally:
        # Cleanup temp file
        try:
            os.unlink(temp_path)
        except:
            pass


def verify_format(code: str, rules: FormatRules) -> Tuple[bool, str]:
    """
    Verifier B: Check format constraints via AST parsing.

    Returns (passed: bool, message: str)

    All checks are deterministic - pure AST inspection.
    """
    if code is None:
        return False, "No code block found in response"

    violations = []

    # Check 1: Line count
    lines = code.strip().split('\n')
    if len(lines) > rules.max_lines:
        violations.append(f"Too many lines: {len(lines)} > {rules.max_lines}")

    # Check 2: No imports
    if rules.no_imports:
        if 'import ' in code:
            violations.append("Contains import statement")

    # Try to parse AST
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    # Check 3: No loops (for/while)
    if rules.no_loops:
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                violations.append("Contains loop (for/while)")
                break

    # Check 4: Single function only
    if rules.single_function:
        func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        if len(func_defs) > 1:
            violations.append(f"Multiple functions: {len(func_defs)} > 1")
        if len(func_defs) == 0:
            violations.append("No function definition found")

    # Check 5: Require docstring
    if rules.require_docstring:
        has_docstring = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if (node.body and isinstance(node.body[0], ast.Expr) and
                    isinstance(node.body[0].value, ast.Constant) and
                    isinstance(node.body[0].value.value, str)):
                    has_docstring = True
                    break
        if not has_docstring:
            violations.append("Missing docstring")

    # Check 6: Forbidden builtins
    if rules.forbidden_builtins:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in rules.forbidden_builtins:
                violations.append(f"Uses forbidden builtin: {node.id}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in rules.forbidden_builtins:
                    violations.append(f"Calls forbidden builtin: {node.func.id}")

    # Check 7: No recursion (function calling itself)
    if rules.no_recursion:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_name = node.name
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name) and child.func.id == func_name:
                            violations.append(f"Recursive call to {func_name}")

    if violations:
        return False, "; ".join(violations[:3])  # Show first 3
    return True, "All format checks passed"


def verify_both(code: str, test_code: str, rules: FormatRules, timeout: float = 5.0) -> dict:
    """
    Run both verifiers and return structured result.

    Returns dict with:
    - pass_a: bool (functional tests)
    - pass_b: bool (format constraints)
    - pass_both: bool (A AND B)
    - msg_a: str
    - msg_b: str
    """
    pass_a, msg_a = verify_functional(code, test_code, timeout)
    pass_b, msg_b = verify_format(code, rules)

    return {
        "pass_a": pass_a,
        "pass_b": pass_b,
        "pass_both": pass_a and pass_b,
        "msg_a": msg_a,
        "msg_b": msg_b,
    }


def format_rules_to_prompt(rules: FormatRules) -> str:
    """Convert format rules to human-readable prompt text."""
    constraints = []

    constraints.append(f"- Maximum {rules.max_lines} lines of code")

    if rules.no_imports:
        constraints.append("- No import statements allowed")

    if rules.no_loops:
        constraints.append("- No for/while loops allowed (use recursion or comprehensions)")

    if rules.require_docstring:
        constraints.append("- Must include a docstring")

    if rules.single_function:
        constraints.append("- Only one function definition allowed")

    if rules.forbidden_builtins:
        constraints.append(f"- Cannot use: {', '.join(rules.forbidden_builtins)}")

    return "\n".join(constraints)


# Quick self-test
if __name__ == "__main__":
    print("Testing verifiers...")

    # Test code extraction
    response = """
Here's my solution:

```python
def factorial(n):
    \"\"\"Compute factorial.\"\"\"
    if n <= 1:
        return 1
    return n * factorial(n - 1)
```

This works by recursion.
"""
    code = extract_code_block(response)
    print(f"Extracted code:\n{code}\n")

    # Test functional verifier
    test_code = """
assert factorial(0) == 1
assert factorial(1) == 1
assert factorial(5) == 120
print("All tests passed!")
"""
    pass_a, msg_a = verify_functional(code, test_code)
    print(f"Verifier A (functional): {pass_a} - {msg_a}")

    # Test format verifier with different tiers
    for tier_name, rules in FORMAT_TIERS.items():
        pass_b, msg_b = verify_format(code, rules)
        print(f"Verifier B ({tier_name}): {pass_b} - {msg_b}")

    print("\nVerifier self-test complete!")
