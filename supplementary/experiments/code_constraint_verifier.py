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
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
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


_TEMP_PATH_PATTERNS = [
    # Windows AppData/Temp paths (greedy through end of path)
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\Users\\[^\"'\s\n,]+", re.IGNORECASE),
    # POSIX user/temp paths
    re.compile(r"/Users/[A-Za-z0-9_.\-]+(?:/[^\"'\s\n,]*)?", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_.\-]+(?:/[^\"'\s\n,]*)?", re.IGNORECASE),
    re.compile(r"/tmp/[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"/var/folders/[^\"'\s\n,]+", re.IGNORECASE),
    # Generic <repo>/<repo> author-machine fingerprints (anonymity hardening)
    re.compile(r"[A-Za-z]:\\\\src\\\\[^\"'\s\n,]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\src\\[^\"'\s\n,]+", re.IGNORECASE),
]


def _scrub_paths(text: str, temp_path: str = "") -> str:
    """Replace any local temp/user paths with anonymous placeholders.

    Verifier subprocess output (stderr/stdout) embeds the temp file path,
    which contains the local username. We scrub before returning so packets
    are PII-clean regardless of the host machine.
    """
    if not text:
        return text
    if temp_path:
        text = text.replace(temp_path, "<tmp>.py")
    for pat in _TEMP_PATH_PATTERNS:
        text = pat.sub("<tmp>", text)
    return text


def verify_functional_structured(
    code: Optional[str], test_code: str, timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Verifier A (structured): runs code in subprocess and returns full audit dict.

    Always-present fields:
        passed: bool
        message: str (back-compat — same string ``verify_functional`` returns)
        subprocess_return_code: Optional[int] (None when short-circuited)
        subprocess_stdout_scrubbed: str
        subprocess_stderr_scrubbed: str
        subprocess_timed_out: bool
        subprocess_timeout_seconds: float
        subprocess_wall_time_ms: float
    """
    # Sentinel defaults (populated even on no-code / timeout / error paths)
    result_dict: Dict[str, Any] = {
        "passed": False,
        "message": "",
        "subprocess_return_code": None,
        "subprocess_stdout_scrubbed": "",
        "subprocess_stderr_scrubbed": "",
        "subprocess_timed_out": False,
        "subprocess_timeout_seconds": float(timeout),
        "subprocess_wall_time_ms": 0.0,
    }

    if code is None:
        result_dict["message"] = "No code block found in response"
        return result_dict

    # Combine code and tests
    full_code = f"{code}\n\n# Tests\n{test_code}"

    # Write to temp file (UTF-8 for Unicode chars like ≥)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(full_code)
        temp_path = f.name

    try:
        # Run in subprocess with timeout (wall-clock around .run only)
        t0 = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, "-I", temp_path],  # -I = isolated mode
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tempfile.gettempdir(),
                env={"PATH": os.environ.get("PATH", "")},  # Minimal env
            )
        finally:
            result_dict["subprocess_wall_time_ms"] = (time.monotonic() - t0) * 1000.0

        # Scrub stdout AND stderr defensively (stdout often empty but scrub anyway)
        stdout_scrubbed = _scrub_paths(completed.stdout or "", temp_path)
        stderr_scrubbed = _scrub_paths(completed.stderr or "", temp_path)
        result_dict["subprocess_return_code"] = completed.returncode
        result_dict["subprocess_stdout_scrubbed"] = stdout_scrubbed
        result_dict["subprocess_stderr_scrubbed"] = stderr_scrubbed

        if completed.returncode == 0:
            result_dict["passed"] = True
            result_dict["message"] = "All tests passed"
        else:
            error_msg = (completed.stderr or "")[:500] if completed.stderr else "Unknown error"
            result_dict["message"] = _scrub_paths(f"Tests failed: {error_msg}", temp_path)

    except subprocess.TimeoutExpired:
        # wall_time_ms already set by inner finally
        result_dict["subprocess_timed_out"] = True
        result_dict["subprocess_return_code"] = -1
        result_dict["message"] = f"Timeout after {timeout}s"
    except Exception as e:
        result_dict["subprocess_return_code"] = -1
        result_dict["message"] = _scrub_paths(f"Execution error: {str(e)[:200]}", temp_path)
    finally:
        # Cleanup temp file
        try:
            os.unlink(temp_path)
        except Exception:
            pass

    return result_dict


def verify_functional(code: str, test_code: str, timeout: float = 5.0) -> Tuple[bool, str]:
    """
    Verifier A: Run code + tests in subprocess with timeout.

    Returns (passed: bool, message: str). Back-compat wrapper around
    ``verify_functional_structured``; the message is identical to what this
    function previously returned (path-scrubbed via ``_scrub_paths``).

    Uses subprocess isolation:
    - Separate process (can't crash main)
    - Hard timeout (can't hang)
    - Temp directory (can't pollute)
    - Stripped environment
    """
    structured = verify_functional_structured(code, test_code, timeout)
    return structured["passed"], structured["message"]


def verify_format_structured(code: Optional[str], rules: FormatRules) -> Dict[str, Any]:
    """
    Verifier B (structured): AST format check returning the full violation list
    rather than a concatenated message.

    Always-present fields:
        passed: bool
        message: str (back-compat — first 3 violations joined with "; ")
        format_violations: List[str] (full list; may be empty)
        format_check_skipped: bool (True iff code is None)
    """
    result_dict: Dict[str, Any] = {
        "passed": False,
        "message": "",
        "format_violations": [],
        "format_check_skipped": False,
    }

    if code is None:
        result_dict["format_check_skipped"] = True
        result_dict["message"] = "No code block found in response"
        return result_dict

    violations: List[str] = []

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
        # Syntax error short-circuits — surface as a single violation entry
        # so the structured field stays a List[str].
        result_dict["format_violations"] = [f"Syntax error: {e}"]
        result_dict["message"] = f"Syntax error: {e}"
        return result_dict

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

    result_dict["format_violations"] = violations
    if violations:
        result_dict["message"] = "; ".join(violations[:3])  # Show first 3 (back-compat)
    else:
        result_dict["passed"] = True
        result_dict["message"] = "All format checks passed"
    return result_dict


def verify_format(code: str, rules: FormatRules) -> Tuple[bool, str]:
    """
    Verifier B: Check format constraints via AST parsing.

    Returns (passed: bool, message: str). Back-compat wrapper around
    ``verify_format_structured`` — message is identical to prior shape.
    """
    structured = verify_format_structured(code, rules)
    return structured["passed"], structured["message"]


def verify_both(code: str, test_code: str, rules: FormatRules, timeout: float = 5.0) -> dict:
    """
    Run both verifiers and return structured result.

    Back-compat fields:
        pass_a, pass_b, pass_both, msg_a, msg_b

    New (additive) fields exposing subprocess + format internals so reviewers
    can audit return code, stdout, stderr, timing, and the raw violation list:
        subprocess_return_code, subprocess_stdout_scrubbed,
        subprocess_stderr_scrubbed, subprocess_timed_out,
        subprocess_timeout_seconds, subprocess_wall_time_ms,
        format_violations, format_check_skipped
    """
    func_struct = verify_functional_structured(code, test_code, timeout)
    fmt_struct = verify_format_structured(code, rules)

    pass_a = func_struct["passed"]
    pass_b = fmt_struct["passed"]

    return {
        # ---- back-compat ----
        "pass_a": pass_a,
        "pass_b": pass_b,
        "pass_both": pass_a and pass_b,
        "msg_a": func_struct["message"],
        "msg_b": fmt_struct["message"],
        # ---- new structured subprocess fields ----
        "subprocess_return_code": func_struct["subprocess_return_code"],
        "subprocess_stdout_scrubbed": func_struct["subprocess_stdout_scrubbed"],
        "subprocess_stderr_scrubbed": func_struct["subprocess_stderr_scrubbed"],
        "subprocess_timed_out": func_struct["subprocess_timed_out"],
        "subprocess_timeout_seconds": func_struct["subprocess_timeout_seconds"],
        "subprocess_wall_time_ms": func_struct["subprocess_wall_time_ms"],
        # ---- new structured format fields ----
        "format_violations": fmt_struct["format_violations"],
        "format_check_skipped": fmt_struct["format_check_skipped"],
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
