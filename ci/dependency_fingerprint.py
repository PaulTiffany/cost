"""
dependency_fingerprint.py -- Record the environment versions the cert was generated
under. Output is structured for embedding in cert provenance.

Captures:
  - Python version + executable path
  - Platform info
  - Key Python packages (numpy, scipy, pandas, matplotlib, transformers,
    sentence-transformers, openai)
  - pdftotext availability + version
  - LaTeX (pdflatex) availability + version
  - git version

Output:
  - Printed to stdout as JSON
  - Written to ci/dependency_fingerprint.json

Exit 0 always (informational).

Usage:
  python ci/dependency_fingerprint.py
"""

import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_DIR = REPO_ROOT / "ci"


def _run_first_line(cmd: list[str]) -> str:
    """Run a command and return the first non-empty line of combined output, or ''."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        text = result.stdout.decode(errors="replace").strip()
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line
    except Exception:
        pass
    return ""


def get_python_info() -> dict:
    return {
        "version": sys.version,
        "version_short": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "executable": sys.executable,
        "implementation": platform.python_implementation(),
    }


def get_platform_info() -> dict:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "architecture": platform.architecture()[0],
    }


def get_package_version(name: str) -> str:
    """Try to import and return __version__. Returns 'absent' on failure."""
    import_name = name.replace("-", "_")
    # special cases
    aliases = {
        "sentence_transformers": "sentence_transformers",
        "sentence-transformers": "sentence_transformers",
        "scikit_learn": "sklearn",
        "scikit-learn": "sklearn",
    }
    mod_name = aliases.get(import_name, import_name)
    try:
        mod = __import__(mod_name)
        ver = getattr(mod, "__version__", None)
        if ver is None:
            # some packages expose version differently
            try:
                import importlib.metadata as im
                ver = im.version(name)
            except Exception:
                ver = "unknown"
        return str(ver)
    except ImportError:
        return "absent"
    except Exception as exc:
        return f"error:{exc}"


def get_packages_info() -> dict:
    packages = [
        "numpy",
        "scipy",
        "pandas",
        "matplotlib",
        "transformers",
        "sentence-transformers",
        "openai",
    ]
    return {pkg: get_package_version(pkg) for pkg in packages}


def get_tools_info() -> dict:
    tools = {}

    # pdftotext
    pdftotext_path = shutil.which("pdftotext")
    if pdftotext_path:
        version_line = _run_first_line(["pdftotext", "-v"])
        tools["pdftotext"] = {
            "path": pdftotext_path,
            "version_line": version_line or "unavailable",
        }
    else:
        tools["pdftotext"] = {"path": None, "version_line": "not found"}

    # pdflatex
    pdflatex_path = shutil.which("pdflatex")
    if pdflatex_path:
        version_line = _run_first_line(["pdflatex", "--version"])
        tools["pdflatex"] = {
            "path": pdflatex_path,
            "version_line": version_line or "unavailable",
        }
    else:
        tools["pdflatex"] = {"path": None, "version_line": "not found"}

    # git
    git_path = shutil.which("git")
    if git_path:
        version_line = _run_first_line(["git", "--version"])
        tools["git"] = {
            "path": git_path,
            "version_line": version_line or "unavailable",
        }
    else:
        tools["git"] = {"path": None, "version_line": "not found"}

    return tools


def _redact_user_paths(value):
    """Recursively replace user-home path prefixes with <HOME> to protect anonymity.

    The cert ships with the supplementary; absolute paths like C:\\Users\\paulc\\
    or /home/paulc/ would deanonymize. We keep tool/library names but scrub the
    user-specific prefix.
    """
    import os
    if isinstance(value, dict):
        return {k: _redact_user_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_user_paths(v) for v in value]
    if isinstance(value, str):
        s = value
        # Windows: C:\Users\<name>\... -> C:\Users\<HOME>\...
        s = re.sub(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", r"<HOME>", s)
        # Unix: /home/<name>/... or /Users/<name>/...
        s = re.sub(r"/(?:home|Users)/[^/\s]+", r"<HOME>", s)
        # Generic os.path.expanduser fallback
        home = os.path.expanduser("~")
        if home and home != "~":
            s = s.replace(home, "<HOME>")
        return s
    return value


def collect_fingerprint() -> dict:
    """Build the env fingerprint dict (importable from claim_certificate.py).

    User-home paths are redacted so the cert can ship without deanonymizing.
    """
    raw = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "python": get_python_info(),
        "platform": get_platform_info(),
        "packages": get_packages_info(),
        "tools": get_tools_info(),
    }
    return _redact_user_paths(raw)


def main() -> int:
    fingerprint = collect_fingerprint()

    out_str = json.dumps(fingerprint, indent=2)
    print(out_str)

    out_path = CI_DIR / "dependency_fingerprint.json"
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(out_str)
        fh.write("\n")

    print(f"\n# Written to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
