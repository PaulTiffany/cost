"""
Structured-output tests for code_constraint_verifier.

Covers the additive subprocess / format-violation fields exposed by
``verify_functional_structured``, ``verify_format_structured``, and
``verify_both``. Existing back-compat fields (``msg_a``/``msg_b``) are
also exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make sibling experiments importable when pytest runs from repo root
EXP_DIR = Path(__file__).resolve().parents[1]
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

import code_constraint_verifier as ccv  # noqa: E402


PASSING_CODE = (
    'def add(a, b):\n'
    '    """Add two numbers."""\n'
    '    return a + b\n'
)
PASSING_TESTS = 'assert add(1, 2) == 3\n'

FAILING_CODE = (
    'def add(a, b):\n'
    '    """Wrong impl."""\n'
    '    return a - b\n'
)
FAILING_TESTS = 'assert add(1, 2) == 3\n'


def _basic_rules() -> ccv.FormatRules:
    return ccv.FormatRules(max_lines=50)


# ---------------------------------------------------------------------------


def test_verifier_structured_output_includes_subprocess_return_code():
    """Return code surfaces for both pass and fail subprocess outcomes."""
    res_pass = ccv.verify_functional_structured(PASSING_CODE, PASSING_TESTS, timeout=5.0)
    assert res_pass["passed"] is True
    assert res_pass["subprocess_return_code"] == 0

    res_fail = ccv.verify_functional_structured(FAILING_CODE, FAILING_TESTS, timeout=5.0)
    assert res_fail["passed"] is False
    # Non-zero exit on AssertionError
    assert isinstance(res_fail["subprocess_return_code"], int)
    assert res_fail["subprocess_return_code"] != 0


def test_verifier_structured_output_includes_separate_stdout_stderr():
    """Failing code with stderr should populate stderr field; stdout stays separate."""
    code = (
        'def f():\n'
        '    """noop."""\n'
        '    print("hello-stdout-marker")\n'
        '    return 0\n'
        'f()\n'
        'raise RuntimeError("explicit-stderr-marker")\n'
    )
    res = ccv.verify_functional_structured(code, "", timeout=5.0)
    assert res["passed"] is False
    assert "hello-stdout-marker" in res["subprocess_stdout_scrubbed"]
    assert "explicit-stderr-marker" in res["subprocess_stderr_scrubbed"]
    # Markers must not bleed across streams
    assert "hello-stdout-marker" not in res["subprocess_stderr_scrubbed"]
    assert "explicit-stderr-marker" not in res["subprocess_stdout_scrubbed"]


def test_verifier_structured_output_path_scrubbed_in_stdout_too():
    """Stdout containing a Windows-style author path is scrubbed defensively."""
    # Embed a path that matches one of _TEMP_PATH_PATTERNS at runtime.
    code = (
        'def f():\n'
        '    """noop."""\n'
        '    print(r"C:\\\\Users\\\\paulc\\\\secret")\n'
        '    return 0\n'
        'f()\n'
    )
    res = ccv.verify_functional_structured(code, "", timeout=5.0)
    assert res["passed"] is True
    # The literal username must not survive in scrubbed stdout
    assert "paulc" not in res["subprocess_stdout_scrubbed"]
    assert "<tmp>" in res["subprocess_stdout_scrubbed"]


def test_verifier_structured_output_timeout_flag_fires_on_infinite_loop():
    """An infinite loop trips ``subprocess_timed_out=True`` and records the budget."""
    code = (
        'def loop():\n'
        '    """spin."""\n'
        '    while True:\n'
        '        pass\n'
        'loop()\n'
    )
    res = ccv.verify_functional_structured(code, "", timeout=0.5)
    assert res["passed"] is False
    assert res["subprocess_timed_out"] is True
    assert res["subprocess_timeout_seconds"] == pytest.approx(0.5)
    assert res["subprocess_return_code"] == -1


def test_verifier_structured_output_format_violations_listed_separately():
    """Multiple format violations come back as a list, not a joined string."""
    rules = ccv.FormatRules(
        max_lines=2,
        no_imports=True,
        require_docstring=True,
        single_function=True,
    )
    bad_code = (
        'import os\n'
        'def a():\n'
        '    return 1\n'
        'def b():\n'
        '    return 2\n'
    )
    res = ccv.verify_format_structured(bad_code, rules)
    assert res["passed"] is False
    assert res["format_check_skipped"] is False
    assert isinstance(res["format_violations"], list)
    assert len(res["format_violations"]) >= 2
    # Each entry is a discrete string, not concatenated with "; "
    assert all(isinstance(v, str) for v in res["format_violations"])
    assert all("; " not in v for v in res["format_violations"])


def test_verifier_structured_output_back_compat_msg_a_unchanged_shape():
    """``verify_both`` still emits the legacy pass_*/msg_* keys with string values."""
    rules = _basic_rules()
    out = ccv.verify_both(PASSING_CODE, PASSING_TESTS, rules, timeout=5.0)
    for key in ("pass_a", "pass_b", "pass_both", "msg_a", "msg_b"):
        assert key in out, f"missing back-compat key: {key}"
    assert isinstance(out["msg_a"], str) and out["msg_a"]
    assert isinstance(out["msg_b"], str) and out["msg_b"]
    assert out["pass_both"] is True
    # New fields coexist
    assert out["subprocess_return_code"] == 0
    assert out["format_check_skipped"] is False


def test_verifier_structured_output_wall_time_populated_and_positive():
    """Wall-time captures real elapsed milliseconds and is reasonable."""
    res = ccv.verify_functional_structured(PASSING_CODE, PASSING_TESTS, timeout=5.0)
    wall = res["subprocess_wall_time_ms"]
    assert isinstance(wall, float)
    assert wall > 0.0
    # Sanity bound: well under the timeout budget for a trivial assert
    assert wall < 5_000.0
