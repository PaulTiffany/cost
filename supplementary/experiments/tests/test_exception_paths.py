"""Direct exception-path tests for the verifier and orchestrator.

Each test targets one ``try/except`` branch in ``code_constraint_verifier.py``
or ``audit_experiment_orchestrator.py``. The goal is branch coverage on the
defensive code that the existing audit-substrate tests cannot reach
(the experiment-side modules sit outside ``ci/audit/``).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

import code_constraint_verifier as verifier  # noqa: E402
from channels import anthropic_channel  # noqa: E402
from channels import openrouter_channel  # noqa: E402
from code_constraint_tasks import TASKS  # noqa: E402


# --------------------------------------------------------------------- #
# Verifier exception paths
# --------------------------------------------------------------------- #


def test_exception_X1_verifier_subprocess_timeout_returns_failure_with_clean_message(monkeypatch):
    """X1: ``subprocess.TimeoutExpired`` is caught by the verifier and
    converted into ``(False, "Timeout after Xs")``. The returned message
    must NOT embed any local temp path (PII discipline).
    Refutation: the call hangs OR returns True OR leaks a path.
    """
    # Arrange — force subprocess.run to raise TimeoutExpired directly so
    # the test does not depend on actually spawning a child process.
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=0.5)

    monkeypatch.setattr(verifier.subprocess, "run", _raise_timeout)
    code = "while True:\n    pass\n"

    # Act
    passed, msg = verifier.verify_functional(code, "", timeout=0.5)

    # Assert
    assert passed is False
    assert msg.startswith("Timeout after"), f"unexpected message: {msg!r}"
    assert "C:\\Users\\" not in msg
    assert "/tmp/" not in msg


def test_exception_X2_verifier_general_exception_returns_failure_with_scrubbed_message(monkeypatch):
    """X2: a non-timeout subprocess error path returns a scrubbed failure.
    We force ``subprocess.run`` to raise a generic exception; the verifier's
    ``except Exception`` arm should catch it and produce a scrubbed message.
    Refutation: the exception escapes OR the path is not scrubbed.
    """
    # Arrange — simulate an unexpected subprocess failure.
    def _boom(*args, **kwargs):
        raise OSError(r"failed to spawn at C:\Users\paulc\AppData\Local\Temp\foo.py")

    monkeypatch.setattr(verifier.subprocess, "run", _boom)

    # Act
    passed, msg = verifier.verify_functional("def f(): pass\n", "", timeout=1.0)

    # Assert
    assert passed is False
    assert "Execution error" in msg
    # Path scrubber must have replaced the username path.
    assert "paulc" not in msg
    assert "<tmp>" in msg or "AppData" not in msg


def test_exception_X3_verifier_temp_file_cleanup_handles_unlink_failure(monkeypatch):
    """X3: the ``finally`` clause around ``os.unlink`` swallows any unlink
    error. We stub ``subprocess.run`` to a fake successful CompletedProcess
    (no real spawn) and force ``os.unlink`` to raise PermissionError. The
    verifier must still return its normal (passed, msg) tuple without
    re-raising.
    Refutation: PermissionError escapes.
    """
    # Arrange — fake a successful subprocess result (returncode=0).
    class _FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(verifier.subprocess, "run",
                        mock.Mock(return_value=_FakeProc()))
    monkeypatch.setattr(verifier.os, "unlink",
                        mock.Mock(side_effect=PermissionError("locked")))

    # Act — must not raise.
    passed, msg = verifier.verify_functional("x = 1\n", "", timeout=2.0)

    # Assert
    assert passed is True
    assert "passed" in msg.lower()


def test_exception_X4_verifier_ast_parse_syntax_error_returns_failure():
    """X4: invalid Python passed to ``verify_format`` triggers ``ast.parse``
    SyntaxError; the verifier catches it and returns ``(False, "Syntax error: ...")``
    without raising.
    Refutation: SyntaxError escapes.
    """
    # Arrange — clearly broken syntax.
    bad_code = "def f( :\n    return 1\n"
    rules = verifier.FORMAT_TIERS["control"]

    # Act
    passed, msg = verifier.verify_format(bad_code, rules)

    # Assert
    assert passed is False
    assert msg.startswith("Syntax error"), f"unexpected msg: {msg!r}"


# --------------------------------------------------------------------- #
# Orchestrator / channel env-load + timeout paths
# --------------------------------------------------------------------- #


def test_exception_X5_orchestrator_env_load_missing_file_handled(monkeypatch, tmp_path):
    """X5: when no env var is set and the fallback env-file path either is
    not set or points to a missing file, ``load_anthropic_key`` and
    ``load_openrouter_key`` raise ``RuntimeError`` with a clean explanatory
    message rather than a generic stack trace from a downstream call.
    Refutation: a non-RuntimeError exception, a silent return of an empty
    string, or the orchestrator crashing before the missing-key check.
    """
    # Arrange. Unset the live API keys so the fallback path is exercised,
    # then point the fallback file path at a guaranteed-missing location.
    missing = tmp_path / "definitely_not_here.env"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AUDIT_ANTHROPIC_ENV_FILE", str(missing))
    monkeypatch.setenv("AUDIT_OPENROUTER_ENV_FILE", str(missing))

    # Act + Assert (Anthropic side)
    with pytest.raises(RuntimeError) as exc_a:
        anthropic_channel.load_anthropic_key()
    assert "ANTHROPIC_API_KEY" in str(exc_a.value)

    # Act + Assert (OpenRouter side)
    with pytest.raises(RuntimeError) as exc_o:
        openrouter_channel.load_openrouter_key()
    assert "OPENROUTER_API_KEY" in str(exc_o.value)


def test_exception_X6_orchestrator_asyncio_timeout_per_trial_handled(monkeypatch):
    """X6: a deliberately-too-short ``asyncio.wait_for`` around a trial
    surfaces ``asyncio.TimeoutError``. This documents the contract: the
    orchestrator's ``asyncio.gather(..., return_exceptions=True)`` is the
    layer that traps such errors — at the per-trial layer the exception
    is allowed to propagate so the orchestrator can record it.
    Refutation: no TimeoutError raised.
    """
    # Arrange — stub the channel's stream to block forever. Use a never-set
    # Event rather than asyncio.sleep so the in-channel sleep patch (below)
    # does not collapse this block into an instant return.
    async def _slow_stream(*args, **kwargs):
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(openrouter_channel, "_stream_once", _slow_stream)

    cell = openrouter_channel._Cell(
        model="qwen/anything",
        task=TASKS[0],
        tier="control",
        trial_idx=0,
    )

    # Patch the in-channel sleep so the retry loop does not actually wait.
    monkeypatch.setattr(openrouter_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))

    async def _runner():
        return await asyncio.wait_for(
            openrouter_channel._run_single_trial(
                client=mock.MagicMock(),
                encoder=mock.MagicMock(),
                cell=cell,
                sem=asyncio.Semaphore(1),
                pre_reg_hash="p",
                verifier_hash="v",
                manifest_hash="m",
                encoder_pkg_version="0.0.0",
            ),
            timeout=0.05,
        )

    # Act + Assert
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        asyncio.run(_runner())


def test_exception_X7_anthropic_channel_chunk_processing_exception_does_not_crash_loop(monkeypatch):
    """X7: an exception raised mid-stream (during chunk iteration) is
    captured by the per-trial ``except Exception`` arm. The trial returns
    a packet with a populated error — the surrounding gather() loop is
    never reached because the exception was swallowed at trial scope.
    Refutation: the exception escapes ``_run_single_trial``.
    """
    # Arrange — fake stream that raises on the second chunk.
    monkeypatch.setattr(anthropic_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))

    class _BoomStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("mid-chunk parse error")
            yield  # unreachable; makes _iter an async generator

        async def get_final_message(self):
            return None

    class _Msgs:
        def __init__(self):
            self.call_count = 0

        def stream(self, **kw):
            self.call_count += 1
            return _BoomStream()

    msgs = _Msgs()
    client = mock.MagicMock()
    client.messages = msgs

    class _Enc:
        def embed(self, t):
            import numpy as np
            return np.asarray([1.0, 0.0], dtype=float)

    cell = anthropic_channel._Cell(
        model="claude-haiku-4-5",
        task=TASKS[0],
        tier="control",
        trial_idx=0,
    )

    # Act — must not raise.
    packet = asyncio.run(
        anthropic_channel._run_single_trial(
            client=client,
            encoder=_Enc(),
            cell=cell,
            sem=asyncio.Semaphore(1),
            pre_reg_hash="p",
            verifier_hash="v",
            manifest_hash="m",
            encoder_pkg_version="0.0.0",
        )
    )

    # Assert — the failure was isolated to one packet, not propagated.
    assert packet["error"] is not None
    assert "mid-chunk" in packet["error"]


def test_exception_X8_openrouter_channel_chunk_processing_exception_isolated(monkeypatch):
    """X8: same isolation property for the OpenRouter channel. Mid-stream
    iteration error is captured by the trial-level ``except Exception``
    and the packet records the error.
    Refutation: the exception escapes ``_run_single_trial``.
    """
    # Arrange
    monkeypatch.setattr(openrouter_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))

    class _BoomStream:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            raise RuntimeError("openrouter mid-chunk explosion")
            yield  # unreachable; makes _iter an async generator

    class _Comp:
        def __init__(self):
            self.call_count = 0

        async def create(self, **kw):
            self.call_count += 1
            return _BoomStream()

    comp = _Comp()
    client = mock.MagicMock()
    client.chat.completions = comp

    class _Enc:
        def embed(self, t):
            import numpy as np
            return np.asarray([1.0, 0.0], dtype=float)

    cell = openrouter_channel._Cell(
        model="qwen/anything",
        task=TASKS[0],
        tier="control",
        trial_idx=0,
    )

    # Act
    packet = asyncio.run(
        openrouter_channel._run_single_trial(
            client=client,
            encoder=_Enc(),
            cell=cell,
            sem=asyncio.Semaphore(1),
            pre_reg_hash="p",
            verifier_hash="v",
            manifest_hash="m",
            encoder_pkg_version="0.0.0",
        )
    )

    # Assert
    assert packet["error"] is not None
    assert "explosion" in packet["error"]
    # All retries consumed (none succeeded).
    assert comp.call_count == openrouter_channel.RETRIES
