"""Tests for the in-process redaction proxy stage.

All tests construct :class:`AgentEvent` instances directly and feed them
through ``redact_events`` — no subprocess, no conductor binary required.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from conduit_sdk.redaction import (
    DEFAULT_SECRET_PATTERNS,
    RedactionFilter,
    redact_events,
    redact_patterns,
)
from conduit_sdk.runlayer import AgentEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    payload: dict[str, Any] | None = None,
    summary: str | None = None,
    redaction_status: str = "none",
) -> AgentEvent:
    return AgentEvent(
        id="evt-001",
        type="agent.update",
        run_id="run-001",
        sequence=1,
        timestamp="2026-01-01T00:00:00Z",
        source="sdk",
        redaction_status=redaction_status,
        payload=payload,
        summary=summary,
    )


async def _run_redact(
    events: list[AgentEvent],
    filter: RedactionFilter,
) -> list[AgentEvent]:
    """Collect the output of ``redact_events`` into a list for assertion."""

    async def _agen() -> AsyncIterator[AgentEvent]:
        for ev in events:
            yield ev

    return [ev async for ev in redact_events(_agen(), filter)]


# ---------------------------------------------------------------------------
# Default patterns — must match the defined list
# ---------------------------------------------------------------------------


class TestDefaultSecretPatterns:
    """DEFAULT_SECRET_PATTERNS contains at least the documented entries."""

    def test_contains_openai_key_pattern(self) -> None:
        assert any("sk-" in p for p in DEFAULT_SECRET_PATTERNS)

    def test_contains_github_token_pattern(self) -> None:
        # The actual regex contains `gh[pousr]_` which is the literal pattern
        assert any("gh[pousr]_" in p for p in DEFAULT_SECRET_PATTERNS)

    def test_contains_aws_key_pattern(self) -> None:
        assert any("AKIA" in p for p in DEFAULT_SECRET_PATTERNS)

    def test_contains_private_key_pattern(self) -> None:
        assert any("PRIVATE KEY" in p for p in DEFAULT_SECRET_PATTERNS)

    def test_contains_bearer_pattern(self) -> None:
        assert any("bearer" in p.lower() for p in DEFAULT_SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# redact_patterns helper
# ---------------------------------------------------------------------------


class TestRedactPatterns:
    """redact_patterns() builds a RedactionFilter."""

    def test_returns_redaction_filter(self) -> None:
        f = redact_patterns(r"sk-\w+")
        assert isinstance(f, RedactionFilter)
        assert len(f.patterns) == 1
        assert f.replacement == "[REDACTED]"

    def test_custom_replacement(self) -> None:
        f = redact_patterns(r"sk-\w+", replacement="***")
        assert f.replacement == "***"

    def test_multiple_patterns(self) -> None:
        f = redact_patterns(r"\d{4}-\d{4}", r"[A-Z]+_TOKEN")
        assert len(f.patterns) == 2


# ---------------------------------------------------------------------------
# Core redaction behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRedactEvents:
    """redact_events() scrubs secrets from AgentEvent payloads."""

    async def test_redacts_openai_key_in_payload_text(self) -> None:
        """An OpenAI-style key in a payload string value gets replaced."""
        events = [
            _make_event(
                payload={
                    "text": "My key is sk-abcDEFghijklmnopqrstuvwxyz123456",
                },
            ),
        ]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        assert len(result) == 1
        ev = result[0]
        assert ev.redaction_status == "redacted"
        assert "[REDACTED]" in ev.payload["text"]
        assert "sk-abcDEFghijklmnopqrstuvwxyz123456" not in ev.payload["text"]

    async def test_redacts_openai_key_default_pattern(self) -> None:
        """DEFAULT_SECRET_PATTERNS catches an OpenAI-style key."""
        events = [
            _make_event(
                payload={"text": "key=sk-abcDEFghijklmnopqrstuvwxyz123456"},
            ),
        ]
        f = redact_patterns(*DEFAULT_SECRET_PATTERNS)
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"
        assert "[REDACTED]" in result[0].payload["text"]

        events = [
            _make_event(
                payload={
                    "token": "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789X",
                },
            ),
        ]
        f = redact_patterns(r"gh[pousr]_[A-Za-z0-9]{36,}")
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"
        assert "[REDACTED]" in result[0].payload["token"]

    async def test_redacts_aws_key(self) -> None:
        f = redact_patterns(r"AKIA[0-9A-Z]{16}")
        events = [_make_event(payload={"access": "AKIA0123456789ABCDEF"})]
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"

    async def test_redacts_private_key_block(self) -> None:
        """A PEM private key embedded in a string gets redacted."""
        key_block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        events = [_make_event(payload={"key": key_block})]
        f = redact_patterns(
            r"-----BEGIN [A-Z ]+PRIVATE KEY-----"
            r"[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----",
        )
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"
        assert "[REDACTED]" in result[0].payload["key"]

    async def test_benign_text_preserved(self) -> None:
        """Strings that do not match any pattern are left unchanged."""
        events = [
            _make_event(payload={"text": "Just a normal message with no secrets."}),
        ]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "none"
        assert result[0].payload["text"] == "Just a normal message with no secrets."

    async def test_summary_also_redacted(self) -> None:
        """Secret patterns in the *summary* field are also scrubbed."""
        events = [
            _make_event(
                payload={"msg": "clean"},
                summary="API key: sk-abcDEFghijklmnopqrstuvwxyz123456",
            ),
        ]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"
        assert "[REDACTED]" in result[0].summary

    async def test_no_secrets_keeps_original_status(self) -> None:
        """An event with no secrets retains its original redaction_status."""
        events = [
            _make_event(payload={"msg": "hello"}, redaction_status="none"),
            _make_event(payload={"msg": "world"}, redaction_status="blocked"),
        ]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "none"
        assert result[1].redaction_status == "blocked"

    async def test_input_event_not_mutated(self) -> None:
        """The original event object is not modified by redaction."""
        original_text = "key=sk-abcDEFghijklmnopqrstuvwxyz123456"
        orig = _make_event(payload={"text": original_text})
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        await _run_redact([orig], f)
        assert orig.payload["text"] == original_text
        assert orig.redaction_status == "none"

    async def test_nested_dict_scrubbed(self) -> None:
        """Secrets in nested dict values are found and redacted."""
        payload = {
            "message": "Hello",
            "metadata": {
                "api_key": "sk-abcDEFghijklmnopqrstuvwxyz123456",
                "tags": ["important"],
            },
        }
        events = [_make_event(payload=payload)]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"
        assert "[REDACTED]" in result[0].payload["metadata"]["api_key"]
        # Unmatched values survive
        assert result[0].payload["message"] == "Hello"
        assert result[0].payload["metadata"]["tags"] == ["important"]

    async def test_list_payload_scrubbed(self) -> None:
        """Secrets inside list items are redacted."""
        payload = {
            "items": [
                "clean",
                "sk-abcDEFghijklmnopqrstuvwxyz123456",
                "also clean",
            ],
        }
        events = [_make_event(payload=payload)]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        assert result[0].redaction_status == "redacted"
        assert result[0].payload["items"][0] == "clean"
        assert "[REDACTED]" in result[0].payload["items"][1]
        assert result[0].payload["items"][2] == "also clean"

    async def test_null_payload_handled(self) -> None:
        """An event with payload=None does not crash."""
        events = [_make_event(payload=None, summary="clean summary")]
        f = redact_patterns(r"sk-\w+")
        result = await _run_redact(events, f)
        assert result[0].payload is None

    async def test_null_summary_handled(self) -> None:
        """An event with summary=None does not crash."""
        events = [_make_event(payload={"x": 1}, summary=None)]
        f = redact_patterns(r"sk-\w+")
        result = await _run_redact(events, f)
        assert result[0].summary is None

    async def test_non_string_payload_values_untouched(self) -> None:
        """int, float, bool, None values in payload pass through unchanged."""
        events = [
            _make_event(
                payload={
                    "count": 42,
                    "ratio": 3.14,
                    "active": True,
                    "empty": None,
                },
            ),
        ]
        f = redact_patterns(r"42")
        result = await _run_redact(events, f)
        # Numbers and booleans are not strings → not scrubbed
        assert result[0].redaction_status == "none"
        assert result[0].payload["count"] == 42
        assert result[0].payload["ratio"] == 3.14
        assert result[0].payload["active"] is True
        assert result[0].payload["empty"] is None

    async def test_envelope_fields_preserved(self) -> None:
        """Non-content fields (id, type, run_id, sequence, timestamp, source)
        are preserved verbatim on the yielded event."""
        events = [
            AgentEvent(
                id="evt-original",
                type="agent.message",
                run_id="run-abc",
                sequence=99,
                timestamp="2026-06-17T12:00:00Z",
                source="agent",
                redaction_status="none",
                payload={"text": "sk-abcDEFghijklmnopqrstuvwxyz123456"},
            ),
        ]
        f = redact_patterns(r"sk-[A-Za-z0-9_\-]{20,}")
        result = await _run_redact(events, f)
        ev = result[0]
        assert ev.id == "evt-original"
        assert ev.type == "agent.message"
        assert ev.run_id == "run-abc"
        assert ev.sequence == 99
        assert ev.timestamp == "2026-06-17T12:00:00Z"
        assert ev.source == "agent"
