"""Programmatic check: the audit substrate has zero LLM imports."""

import ast
import pathlib

FORBIDDEN_MODULES = {
    "anthropic",
    "openai",
    "openrouter",
    "transformers",
    "torch",
}

AUDIT_DIR = pathlib.Path(__file__).resolve().parent.parent


def _audit_py_files():
    return sorted(AUDIT_DIR.rglob("*.py"))


def _imported_modules(source: str):
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.add(node.module.split(".")[0])
    return names


def _scan_for_offenders(py_files):
    offenders = {}
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        imported = _imported_modules(source)
        bad = imported & FORBIDDEN_MODULES
        offenders[str(path)] = sorted(bad)
    return {p: b for p, b in offenders.items() if b}


def test_audit_substrate_imports_no_llm_libraries():
    # Arrange
    py_files = _audit_py_files()
    assert py_files, "expected to find .py files under ci/audit"

    # Act
    offenders = _scan_for_offenders(py_files)

    # Assert
    assert offenders == {}, f"forbidden LLM imports found: {offenders}"


def test_imported_modules_handles_relative_import_with_no_module():
    # Arrange: `from . import X` produces an ImportFrom with module=None
    source = "from . import relation_classes\n"

    # Act
    names = _imported_modules(source)

    # Assert: such a node contributes nothing to the imported-name set
    assert names == set()


def test_scan_for_offenders_flags_synthetic_forbidden_imports(tmp_path):
    # Arrange: drop a synthetic offender file in a temp dir and scan it
    fake = tmp_path / "fake_offender.py"
    fake.write_text("import torch\nimport openai\n", encoding="utf-8")

    # Act
    offenders = _scan_for_offenders([fake])

    # Assert
    assert offenders == {str(fake): ["openai", "torch"]}


def test_scan_for_offenders_returns_empty_for_clean_file(tmp_path):
    # Arrange: a clean file produces no offenders
    clean = tmp_path / "clean.py"
    clean.write_text("import json\nimport math\n", encoding="utf-8")

    # Act
    offenders = _scan_for_offenders([clean])

    # Assert
    assert offenders == {}


def test_purity_check_covers_every_py_file_in_audit():
    # Arrange
    py_files = _audit_py_files()

    # Act
    file_names = {p.name for p in py_files}

    # Assert
    expected = {
        "__init__.py",
        "relation_classes.py",
        "observation_packet.py",
        "audit_result.py",
        "observer.py",
        "audit_observer.py",
        "test_relation_classes.py",
        "test_observation_packet.py",
        "test_audit_result.py",
        "test_observer.py",
        "test_audit_observer.py",
        "test_purity.py",
    }
    assert expected.issubset(file_names)
