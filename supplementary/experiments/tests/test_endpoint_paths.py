"""Endpoint-path tests for the experiment channels.

These tests exercise every API-error branch in the Anthropic and OpenRouter
channels WITHOUT making any real network calls. The pattern is:

  1. Build a minimal fake client whose streaming entry point either yields
     synthetic chunks or raises a synthetic exception.
  2. Wrap it in the channel's own ``_run_single_trial`` coroutine.
  3. Assert the resulting ObservationPacket carries the expected ``error``
     value (or, on the malformed-chunk paths, that no exception escaped).

Mocking strategy (per the task brief):
  * ``client.messages.stream(**kwargs)`` is mocked with an async context
    manager whose async iterator yields synthetic chunk events. We use
    plain classes rather than unittest.mock.AsyncMock for the context
    manager itself because async-iterator semantics are simpler to reason
    about with explicit fakes.
  * For exception paths we set ``side_effect`` on the entry-point method.
  * No test imports the real ``anthropic`` or ``openai`` SDK.

Counters on the fake clients let us measure retry behaviour for E2/E3.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import List, Optional
from unittest import mock

import pytest

# Make sibling experiment modules importable without going through the
# ``supplementary.experiments`` package (it is not a real package on disk
# at the channel level — the channels rely on a sys.path tweak).
EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

from channels import anthropic_channel  # noqa: E402
from channels import openrouter_channel  # noqa: E402
from code_constraint_tasks import TASKS  # noqa: E402


# --------------------------------------------------------------------- #
# Tiny fakes (kept local; not reused outside this file)
# --------------------------------------------------------------------- #


class _FakeEncoder:
    """Returns a constant 4-vector. Channel only needs ``.embed(text)``."""

    def embed(self, text: str):
        import numpy as np
        # length-conditioned so successive chunks produce non-zero displacement
        return np.asarray([float(len(text)), 0.0, 0.0, 0.0], dtype=float)


class _FakeDelta:
    def __init__(self, text: Optional[str]):
        self.text = text


class _FakeEvent:
    """Anthropic-shaped streaming event."""

    def __init__(self, text: Optional[str]):
        self.type = "content_block_delta"
        self.delta = _FakeDelta(text)


class _FakeFinalMessage:
    def __init__(self, model: str = "claude-test-snapshot"):
        self.model = model


class _AnthropicFakeStream:
    """Async context manager + async iterator yielding the events list."""

    def __init__(self, events, raise_on_iter: Optional[Exception] = None):
        self._events = list(events)
        self._raise = raise_on_iter

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        if self._raise is not None:
            raise self._raise
        for ev in self._events:
            yield ev

    async def get_final_message(self):
        return _FakeFinalMessage()


class _AnthropicFakeMessages:
    """Captures call count to verify retry behaviour."""

    def __init__(self, *, raise_each: Optional[Exception] = None,
                 events_each=None):
        self.raise_each = raise_each
        self.events_each = list(events_each or [_FakeEvent("ok")])
        self.call_count = 0

    def stream(self, **kwargs):
        self.call_count += 1
        if self.raise_each is not None:
            return _AnthropicFakeStream([], raise_on_iter=self.raise_each)
        return _AnthropicFakeStream(self.events_each)


class _AnthropicFakeClient:
    def __init__(self, messages: _AnthropicFakeMessages):
        self.messages = messages


# --------------------------------------------------------------------- #
# OpenRouter fakes (the chat.completions.create returns an async iterable)
# --------------------------------------------------------------------- #


class _ORDelta:
    def __init__(self, content):
        self.content = content


class _ORChoice:
    def __init__(self, content):
        self.delta = _ORDelta(content)


class _OREvent:
    """OpenRouter SSE-shaped event with .choices, .model, .usage."""

    def __init__(self, content="ok", model="qwen/test-snap", total_tokens=10):
        self.choices = [_ORChoice(content)] if content is not None else []
        self.model = model

        class _U:
            pass
        self.usage = _U()
        self.usage.total_tokens = total_tokens


class _OREventBadDelta:
    """Event whose delta is missing or non-conforming (parse-error path)."""

    def __init__(self):
        self.choices = [object()]  # object() has no `.delta` attribute
        self.model = "qwen/test"
        self.usage = None


class _ORStream:
    def __init__(self, events, raise_on_iter: Optional[Exception] = None):
        self._events = list(events)
        self._raise = raise_on_iter

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        if self._raise is not None:
            raise self._raise
        for ev in self._events:
            yield ev


class _ORChatCompletions:
    def __init__(self, *, raise_each=None, events_each=None,
                 raise_mid_iter=None):
        self.raise_each = raise_each
        self.events_each = list(events_each or [_OREvent("hello ")])
        self.raise_mid_iter = raise_mid_iter
        self.call_count = 0

    async def create(self, **kwargs):
        self.call_count += 1
        if self.raise_each is not None:
            raise self.raise_each
        if self.raise_mid_iter is not None:
            return _ORStream([], raise_on_iter=self.raise_mid_iter)
        return _ORStream(self.events_each)


class _ORChat:
    def __init__(self, completions):
        self.completions = completions


class _OpenRouterFakeClient:
    def __init__(self, completions: _ORChatCompletions):
        self.chat = _ORChat(completions)


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _anthropic_cell():
    return anthropic_channel._Cell(
        model="claude-haiku-4-5",
        task=TASKS[0],
        tier="control",
        trial_idx=0,
    )


def _openrouter_cell():
    return openrouter_channel._Cell(
        model="qwen/qwen-2.5-coder-32b-instruct",
        task=TASKS[0],
        tier="control",
        trial_idx=0,
    )


def _run_anthropic(client) -> dict:
    return asyncio.run(
        anthropic_channel._run_single_trial(
            client=client,
            encoder=_FakeEncoder(),
            cell=_anthropic_cell(),
            sem=asyncio.Semaphore(1),
            pre_reg_hash="pre",
            verifier_hash="ver",
            manifest_hash="man",
            encoder_pkg_version="0.0.0-test",
        )
    )


def _run_openrouter(client) -> dict:
    return asyncio.run(
        openrouter_channel._run_single_trial(
            client=client,
            encoder=_FakeEncoder(),
            cell=_openrouter_cell(),
            sem=asyncio.Semaphore(1),
            pre_reg_hash="pre",
            verifier_hash="ver",
            manifest_hash="man",
            encoder_pkg_version="0.0.0-test",
        )
    )


# --------------------------------------------------------------------- #
# E1..E4 — Anthropic endpoint paths
# --------------------------------------------------------------------- #


def test_endpoint_E1_anthropic_401_unauthorized_marks_packet_as_error(monkeypatch):
    """E1: a 401-shaped exception from the Anthropic stream produces a packet
    whose ``error`` field is populated. Auth errors are non-retryable per
    the channel's ``_retryable`` keyword filter.
    Refutation: a normal-shape packet with ``error is None``.
    """
    # Arrange — message intentionally omits any retryable keyword
    # ('401', '429', etc.) so the non-retryable branch is exercised.
    err = RuntimeError("AuthenticationError Unauthorized: bad api key")
    messages = _AnthropicFakeMessages(raise_each=err)
    client = _AnthropicFakeClient(messages)

    # Act
    packet = _run_anthropic(client)

    # Assert
    assert packet["error"] is not None, "auth error must produce non-null error"
    assert "Auth" in packet["error"] or "Unauthorized" in packet["error"]
    # Non-retryable: should have been called exactly once.
    assert messages.call_count == 1
    # Verifier was skipped because api errored.
    assert packet["verifier_result"]["msg_a"].startswith("skipped:")


def test_endpoint_E2_anthropic_429_rate_limit_triggers_retry_then_records_error(monkeypatch):
    """E2: a 429 rate-limit error is retryable (the keyword 'ratelimit' or
    '429' triggers backoff). After the retry budget is exhausted, the packet
    carries the final error string.
    Refutation: call_count == 1 (no retry) or error is None.
    """
    # Arrange — keep test fast: monkeypatch sleep to no-op.
    monkeypatch.setattr(anthropic_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))
    err = RuntimeError("RateLimitError 429: too many requests")
    messages = _AnthropicFakeMessages(raise_each=err)
    client = _AnthropicFakeClient(messages)

    # Act
    packet = _run_anthropic(client)

    # Assert
    assert packet["error"] is not None
    assert "429" in packet["error"] or "RateLimit" in packet["error"]
    # Retry budget is 3 attempts (range(3)) — rate limit triggers retries.
    assert messages.call_count == 3, (
        f"Expected 3 attempts on retryable error; got {messages.call_count}"
    )


def test_endpoint_E3_anthropic_503_server_error_handled_as_transient(monkeypatch):
    """E3: a 503 server error matches the 'overloaded'/'503' keyword set and
    is retried up to 3 times, then recorded as an error packet.
    Refutation: <3 attempts (treated as fatal).
    """
    # Arrange
    monkeypatch.setattr(anthropic_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))
    err = RuntimeError("APIStatusError 503 Service Unavailable / overloaded")
    messages = _AnthropicFakeMessages(raise_each=err)
    client = _AnthropicFakeClient(messages)

    # Act
    packet = _run_anthropic(client)

    # Assert
    assert packet["error"] is not None
    assert "503" in packet["error"] or "overloaded" in packet["error"].lower()
    assert messages.call_count == 3


def test_endpoint_E4_anthropic_streaming_chunk_malformed_handled_gracefully(monkeypatch):
    """E4: a streaming event whose delta carries no text (None/empty) is
    skipped silently; the channel does not crash and emits a normal packet.
    Refutation: an exception escapes, or packet error is non-null.
    """
    # Arrange — interleave a None-text chunk with a real chunk.
    events = [
        _FakeEvent(None),       # malformed: no text attr value
        _FakeEvent(""),         # empty string
        _FakeEvent("def f():\n    pass\n"),
    ]
    messages = _AnthropicFakeMessages(events_each=events)
    client = _AnthropicFakeClient(messages)

    # Act — must not raise.
    packet = _run_anthropic(client)

    # Assert
    assert packet["error"] is None, (
        f"Malformed chunk leaked an error: {packet['error']!r}"
    )
    # Only the real chunk contributed to running_text.
    assert "def f" in packet["output_text"]
    assert packet["n_chunks"] == 1


# --------------------------------------------------------------------- #
# E5..E7 — OpenRouter endpoint paths
# --------------------------------------------------------------------- #


def test_endpoint_E5_openrouter_400_bad_model_marks_packet_as_error(monkeypatch):
    """E5: a 400 bad-model error from OpenRouter is recorded on the packet.
    OpenRouter retries any exception (no retryable filter), so the call
    count climbs to RETRIES.
    Refutation: error is None or no retry attempts.
    """
    # Arrange
    monkeypatch.setattr(openrouter_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))
    err = RuntimeError("BadRequestError 400: model not found")
    completions = _ORChatCompletions(raise_each=err)
    client = _OpenRouterFakeClient(completions)

    # Act
    packet = _run_openrouter(client)

    # Assert
    assert packet["error"] is not None
    assert "400" in packet["error"] or "BadRequest" in packet["error"]
    assert completions.call_count == openrouter_channel.RETRIES


def test_endpoint_E6_openrouter_timeout_marks_packet_as_error(monkeypatch):
    """E6: ``asyncio.TimeoutError`` from the OpenRouter stream-create call
    is caught, retried RETRIES times, then recorded.
    Refutation: timeout escapes the retry loop.
    """
    # Arrange
    monkeypatch.setattr(openrouter_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))
    completions = _ORChatCompletions(raise_each=asyncio.TimeoutError())
    client = _OpenRouterFakeClient(completions)

    # Act
    packet = _run_openrouter(client)

    # Assert
    assert packet["error"] is not None
    assert "TimeoutError" in packet["error"]
    assert completions.call_count == openrouter_channel.RETRIES
    # Empty-output sentinel verifier result.
    assert packet["verifier_result"]["msg_a"].startswith("no output")


def test_endpoint_E7_openrouter_streaming_response_parse_error_handled(monkeypatch):
    """E7: a malformed SSE chunk (no content / missing delta) is skipped
    silently inside ``_stream_once``; no exception escapes; packet is
    emitted (error may or may not be None depending on whether ANY chunk
    was usable, but the loop must not crash).
    Refutation: an exception leaks past the channel.
    """
    # Arrange — stream yields only the bad-delta event followed by a good one.
    monkeypatch.setattr(openrouter_channel.asyncio, "sleep",
                        mock.AsyncMock(return_value=None))
    completions = _ORChatCompletions(events_each=[
        _OREventBadDelta(),
        _OREvent("hello"),
    ])
    client = _OpenRouterFakeClient(completions)

    # Act — must not raise (the channel's getattr-guarded loop swallows
    # the missing-delta case via a `continue`).
    packet = _run_openrouter(client)

    # Assert — at least one good chunk made it through; channel is alive.
    assert packet is not None
    assert packet["output_text"] == "hello"
    # Single retry — the loop succeeded on first attempt.
    assert completions.call_count == 1
