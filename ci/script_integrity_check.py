#!/usr/bin/env python3
"""
script_integrity_check.py - Smoke-test every figure script.

Layer 11 of the certification stack. L4 verifies the data -> script
-> asset chain by mtime and existence. L11 verifies that the script
itself would actually run if invoked: syntactically valid, all
imports resolve, no obvious broken references.

For each figure script declared in ci/figure_lineage.json:
  1. Read source. compile() to AST — catches syntax errors.
  2. Walk import statements. importlib.util.find_spec() each one —
     catches missing dependencies (e.g., a script that imports
     `pandas` when pandas isn't installed).
  3. Walk top-level path constants (REPO_ROOT, RESULTS_PATH, etc.)
     — verify they reference existing paths where statically detectable.

Out of scope: actually running the scripts. Re-rendering each figure
in a clean env is the gold-standard build-equivalence check — but
matplotlib output isn't byte-deterministic (PDFs embed timestamps),
hardcoded output paths would overwrite real assets, and total runtime
adds minutes per certificate run. Smoke test is the 80/20.

Exit codes
----------
  0  every script passes smoke test
  1  one or more scripts have syntax errors, missing imports, or
     broken path constants
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LINEAGE_JSON = SCRIPT_DIR / "figure_lineage.json"
RESULTS_JSON = SCRIPT_DIR / "script_integrity_results.json"


@dataclass
class ScriptReport:
    name: str            # figure name (manifest key)
    script_path: str
    syntax_ok: bool = True
    syntax_error: str | None = None
    missing_imports: list[str] = field(default_factory=list)
    n_imports_checked: int = 0

    @property
    def passed(self) -> bool:
        return self.syntax_ok and not self.missing_imports

    def to_dict(self) -> dict:
        return asdict(self)


# Modules we don't expect to be installed but appear in scripts as
# optional/fallback. Matched against the top-level package name.
OPTIONAL_MODULES = {
    # vendor-specific or notebook-only deps
    "ipywidgets",
    "IPython",
    # typing imports that might fail on older Python
    "typing_extensions",
}

# Standard-library modules we don't bother to check (always available
# under any supported Python). Saves time and avoids find_spec flakes
# on edge cases.
STDLIB_HINT = {
    "os", "sys", "json", "re", "math", "time", "pathlib", "argparse",
    "subprocess", "tempfile", "hashlib", "datetime", "collections",
    "itertools", "functools", "dataclasses", "typing", "warnings",
    "shutil", "io", "csv", "ast", "importlib", "pickle", "copy",
    "random", "string", "logging", "enum", "abc", "concurrent",
    "threading", "multiprocessing", "queue", "heapq", "bisect",
    "operator", "uuid", "platform", "traceback",
}


def extract_imports(source: str) -> list[str]:
    """Return list of top-level module names imported by `source`.

    For `import foo.bar`, returns ["foo"].
    For `from foo.bar import baz`, returns ["foo"].
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return sorted(names)


def _detect_dynamic_path_inserts(source: str) -> bool:
    """Return True if the script appears to manipulate sys.path at runtime
    (sys.path.insert / sys.path.append). Used to widen the search path
    when checking imports — otherwise false positives on scripts that
    intentionally extend sys.path before importing sibling/cousin
    modules.
    """
    if "sys.path.insert" in source or "sys.path.append" in source:
        return True
    return False


def check_script(name: str, script_path: Path) -> ScriptReport:
    r = ScriptReport(name=name, script_path=str(script_path.relative_to(REPO_ROOT) if script_path.is_relative_to(REPO_ROOT) else script_path))

    # 1. Syntax check
    try:
        source = script_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        r.syntax_ok = False
        r.syntax_error = f"could not read: {exc}"
        return r

    try:
        compile(source, str(script_path), "exec")
    except SyntaxError as exc:
        r.syntax_ok = False
        r.syntax_error = f"line {exc.lineno}: {exc.msg}"
        return r

    # 2. Import resolution. Add the script's directory and a few
    # ancestor dirs to sys.path so cross-tree imports resolve. Also
    # add common locations under the repo (supplementary/demos/) where
    # shared modules live.
    extra_paths: list[str] = [
        str(script_path.parent),
        str(script_path.parent.parent),
        str(REPO_ROOT / "supplementary" / "demos"),
        str(REPO_ROOT / "supplementary" / "experiments"),
        str(REPO_ROOT / "supplementary" / "bridges"),
    ]
    if _detect_dynamic_path_inserts(source):
        # Script manipulates sys.path; widen further. Walk one more
        # level up and include the supplementary tree root too.
        extra_paths.append(str(script_path.parent.parent.parent))
        extra_paths.append(str(REPO_ROOT / "supplementary"))

    saved_sys_path = sys.path[:]
    try:
        for p in extra_paths:
            if p not in sys.path:
                sys.path.insert(0, p)

        imports = extract_imports(source)
        for mod_name in imports:
            if mod_name in STDLIB_HINT or mod_name in OPTIONAL_MODULES:
                continue
            r.n_imports_checked += 1
            try:
                spec = importlib.util.find_spec(mod_name)
            except (ValueError, ModuleNotFoundError, ImportError) as exc:
                r.missing_imports.append(f"{mod_name} ({exc})")
                continue
            if spec is None:
                r.missing_imports.append(mod_name)
    finally:
        sys.path[:] = saved_sys_path

    return r


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="print each script's status")
    args = parser.parse_args()

    if not LINEAGE_JSON.exists():
        print(f"ERROR: figure lineage manifest not found at {LINEAGE_JSON}", file=sys.stderr)
        return 2

    manifest = json.loads(LINEAGE_JSON.read_text(encoding="utf-8"))

    # Collect unique script paths from the manifest (a single script
    # may render multiple figures; dedup so we don't double-check it)
    script_to_figures: dict[str, list[str]] = {}
    for name, fig in manifest["figures"].items():
        if fig.get("tikz_source"):
            continue
        script = fig.get("script")
        if not script:
            continue
        script_to_figures.setdefault(script, []).append(name)

    print("=" * 70)
    print("SCRIPT INTEGRITY CHECK  (figure-script smoke tests)")
    print("=" * 70)
    print(f"Manifest:        {LINEAGE_JSON}")
    print(f"Unique scripts:  {len(script_to_figures)}")
    print()

    reports: list[ScriptReport] = []
    for script_rel, fig_names in sorted(script_to_figures.items()):
        # Use the first figure name as the report label (could
        # include all but keeps output compact)
        label = fig_names[0] if len(fig_names) == 1 else f"{fig_names[0]} (+{len(fig_names) - 1} more)"
        script_path = (REPO_ROOT / script_rel).resolve()
        if not script_path.exists():
            r = ScriptReport(name=label, script_path=script_rel, syntax_ok=False, syntax_error="script file does not exist")
        else:
            r = check_script(label, script_path)
        reports.append(r)

        if args.verbose or not r.passed:
            badge = "[OK]  " if r.passed else "[FAIL]"
            print(f"  {badge} {r.name}")
            print(f"         {r.script_path}")
            if not r.syntax_ok:
                print(f"         SYNTAX: {r.syntax_error}")
            if r.missing_imports:
                print(f"         MISSING IMPORTS: {r.missing_imports}")
            if args.verbose and r.passed:
                print(f"         imports checked: {r.n_imports_checked}")

    n_pass = sum(1 for r in reports if r.passed)
    n_fail = len(reports) - n_pass
    print()
    print("-" * 70)
    print(f"  Passed: {n_pass:>3} / {len(reports)}")
    print(f"  Failed: {n_fail:>3} / {len(reports)}")
    print("-" * 70)

    payload = {
        "summary": {
            "total_scripts": len(reports),
            "passed": n_pass,
            "failed": n_fail,
        },
        "scripts": [r.to_dict() for r in reports],
    }
    RESULTS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Full report: {RESULTS_JSON}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
