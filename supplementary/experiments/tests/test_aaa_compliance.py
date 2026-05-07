"""AAA gold-standard compliance for the experiment-side test suite.

Smaller-scope mirror of ``ci/audit/tests/test_aaa_gold_standard.py``: the
substrate already has its own meta-tests; this file enforces the same
shape on the experiment-layer tests we just added.

Checks:
  * every endpoint test has a docstring
  * every exception-path test has a docstring
  * every throughput test has a docstring
  * no test imports the real ``anthropic`` or ``openai`` SDK (mock-only)
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

TESTS_DIR = Path(__file__).resolve().parent

# Files in scope (the four experiment-side test files).
ENDPOINT_FILE = TESTS_DIR / "test_endpoint_paths.py"
EXCEPTION_FILE = TESTS_DIR / "test_exception_paths.py"
THROUGHPUT_FILE = TESTS_DIR / "test_full_pipeline_throughput.py"

# Forbidden top-level imports (the real LLM SDKs). Mocks only.
FORBIDDEN_LLM_MODULES = frozenset({"anthropic", "openai"})

ALL_TEST_FILES = (ENDPOINT_FILE, EXCEPTION_FILE, THROUGHPUT_FILE)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _test_functions(tree: ast.Module) -> Iterable[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                yield node


def _imported_top_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module.split(".")[0])
    return names


def _docstring_offenders(path: Path) -> list[str]:
    tree = _parse(path)
    offenders: list[str] = []
    for fn in _test_functions(tree):
        doc = ast.get_docstring(fn)
        if not (doc and doc.strip()):
            offenders.append(f"{path.name}::{fn.name}")
    return offenders


def test_endpoint_tests_have_docstrings():
    """Every test in ``test_endpoint_paths.py`` must carry a docstring
    explaining the endpoint scenario it exercises (the audit substrate's
    M1 discipline applied to the experiment layer).
    Refutation: any endpoint test missing a docstring.
    """
    # Arrange + Act
    offenders = _docstring_offenders(ENDPOINT_FILE)

    # Assert
    assert not offenders, (
        "endpoint tests missing docstrings:\n  " + "\n  ".join(offenders)
    )


def test_exception_tests_have_docstrings():
    """Every test in ``test_exception_paths.py`` must carry a docstring
    naming the try/except branch under refutation.
    Refutation: any exception-path test missing a docstring.
    """
    # Arrange + Act
    offenders = _docstring_offenders(EXCEPTION_FILE)

    # Assert
    assert not offenders, (
        "exception-path tests missing docstrings:\n  " + "\n  ".join(offenders)
    )


def test_throughput_tests_have_docstrings():
    """Every test in ``test_full_pipeline_throughput.py`` must carry a
    docstring describing the pipeline contract it pins.
    Refutation: any throughput test missing a docstring.
    """
    # Arrange + Act
    offenders = _docstring_offenders(THROUGHPUT_FILE)

    # Assert
    assert not offenders, (
        "throughput tests missing docstrings:\n  " + "\n  ".join(offenders)
    )


def test_no_test_imports_real_api_clients():
    """No experiment-layer test imports the real ``anthropic`` or
    ``openai`` SDKs at the top level. All API interaction is mocked
    via ``unittest.mock`` and ``monkeypatch.setattr``.
    Refutation: any test file that imports a forbidden SDK.
    """
    # Arrange
    offenders: list[tuple[str, str]] = []

    # Act
    for path in ALL_TEST_FILES:
        tree = _parse(path)
        for mod in _imported_top_modules(tree) & FORBIDDEN_LLM_MODULES:
            offenders.append((path.name, mod))

    # Assert
    assert not offenders, (
        f"forbidden LLM imports in experiment tests: {offenders}"
    )
