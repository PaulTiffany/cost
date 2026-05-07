"""UTF-8-safe pytest wrapper for Cosmic Ray sessions.

Cosmic Ray's testing.py decodes captured subprocess output with
``decode("utf-8")`` (no errors handler). On Windows the test output can
contain cp1252 bytes (0x97 em-dash, etc.) emitted by pytest assertion
diffs or third-party library messages. The strict decode raises
UnicodeDecodeError, which Cosmic Ray catches and downgrades the result
to ``incompetent`` — losing the kill record.

This wrapper runs pytest with the same args, captures both streams,
re-encodes them to UTF-8 with ``errors="replace"``, prints them to
stdout/stderr, and propagates the exit code. From Cosmic Ray's view the
subprocess output is always valid UTF-8.

Usage in cosmic_ray_config.toml:

    test-command = "python ci/audit/_run_tests_utf8.py ci/audit/tests/ -x -q --deselect ci/audit/tests/test_aaa_gold_standard.py"
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    pytest_args = sys.argv[1:]
    if not pytest_args:
        pytest_args = ["ci/audit/tests/", "-x", "-q"]

    # Force UTF-8 in the child Python; harmless if already set.
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", *pytest_args],
        capture_output=True,
        env=env,
    )

    # Re-encode both streams to UTF-8 with errors="replace" before forwarding.
    out_text = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    err_text = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    sys.stdout.write(out_text)
    sys.stderr.write(err_text)
    sys.stdout.flush()
    sys.stderr.flush()
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
