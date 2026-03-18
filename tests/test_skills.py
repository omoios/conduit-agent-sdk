"""Tests for skill activation (activate_skill / activate_skills)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from conduit_sdk import Agent, Client, SkillResult
from conduit_sdk.client import Client as ClientClass
from conduit_sdk.exceptions import SessionError
from conduit_sdk.session import Session


# ---------------------------------------------------------------------------
# Command normalization
# ---------------------------------------------------------------------------


class TestNormalizeCommand:
    def test_already_prefixed(self):
        assert Client._normalize_command("/help") == "/help"

    def test_adds_prefix(self):
        assert Client._normalize_command("help") == "/help"

    def test_strips_whitespace(self):
        assert Client._normalize_command("  help  ") == "/help"

    def test_already_prefixed_with_whitespace(self):
        assert Client._normalize_command("  /compact  ") == "/compact"

    def test_command_with_arguments(self):
        assert Client._normalize_command("model claude-4") == "/model claude-4"

    def test_prefixed_command_with_arguments(self):
        assert Client._normalize_command("/model claude-4") == "/model claude-4"

    def test_empty_string(self):
        assert Client._normalize_command("") == "/"

    def test_slash_only(self):
        assert Client._normalize_command("/") == "/"


# ---------------------------------------------------------------------------
# SkillResult dataclass
# ---------------------------------------------------------------------------


class TestSkillResult:
    def test_defaults(self):
        r = SkillResult(command="/help")
        assert r.command == "/help"
        assert r.text == ""
        assert r.success is True
        assert r.error is None

    def test_success_result(self):
        r = SkillResult(command="/help", text="Help text here", success=True)
        assert r.text == "Help text here"
        assert r.success is True

    def test_failure_result(self):
        r = SkillResult(command="/bad", text="", success=False, error="something broke")
        assert r.success is False
        assert r.error == "something broke"


# ---------------------------------------------------------------------------
# Client.activate_skill
# ---------------------------------------------------------------------------


class TestClientActivateSkill:
    @pytest.mark.asyncio
    async def test_normalizes_and_sends(self):
        """activate_skill should add / prefix and call prompt_sync."""
        client = Client(["echo", "hi"])
        client._connected = True

        mock_msg = AsyncMock()
        mock_msg.text = lambda: "response text"

        with patch.object(
            client, "prompt_sync", new_callable=AsyncMock, return_value=[mock_msg]
        ):
            result = await client.activate_skill("help")

        assert result == "response text"

    @pytest.mark.asyncio
    async def test_already_prefixed(self):
        """activate_skill should not double-prefix."""
        client = Client(["echo", "hi"])
        client._connected = True

        mock_msg = AsyncMock()
        mock_msg.text = lambda: "result"

        with patch.object(
            client, "prompt_sync", new_callable=AsyncMock, return_value=[mock_msg]
        ) as mock_prompt:
            await client.activate_skill("/help")

        mock_prompt.assert_called_once_with("/help", session_id=None)

    @pytest.mark.asyncio
    async def test_passes_session_id(self):
        """activate_skill should forward session_id."""
        client = Client(["echo", "hi"])
        client._connected = True

        with patch.object(
            client, "prompt_sync", new_callable=AsyncMock, return_value=[]
        ) as mock_prompt:
            await client.activate_skill("/help", session_id="sess-123")

        mock_prompt.assert_called_once_with("/help", session_id="sess-123")

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """activate_skill returns empty string when no messages."""
        client = Client(["echo", "hi"])
        client._connected = True

        with patch.object(
            client, "prompt_sync", new_callable=AsyncMock, return_value=[]
        ):
            result = await client.activate_skill("/help")

        assert result == ""

    @pytest.mark.asyncio
    async def test_multiple_messages_concatenated(self):
        """activate_skill joins text from multiple messages."""
        client = Client(["echo", "hi"])
        client._connected = True

        msg1 = AsyncMock()
        msg1.text = lambda: "part1 "
        msg2 = AsyncMock()
        msg2.text = lambda: "part2"

        with patch.object(
            client, "prompt_sync", new_callable=AsyncMock, return_value=[msg1, msg2]
        ):
            result = await client.activate_skill("/help")

        assert result == "part1 part2"


# ---------------------------------------------------------------------------
# Client.activate_skills (batch)
# ---------------------------------------------------------------------------


class TestClientActivateSkills:
    @pytest.mark.asyncio
    async def test_multiple_commands(self):
        """activate_skills runs each command and returns SkillResults."""
        client = Client(["echo", "hi"])
        client._connected = True

        call_count = 0

        async def fake_activate(cmd, *, session_id=None):
            nonlocal call_count
            call_count += 1
            return f"response-{call_count}"

        with patch.object(client, "activate_skill", side_effect=fake_activate):
            results = await client.activate_skills(["/help", "compact", "/cost"])

        assert len(results) == 3
        assert results[0].command == "/help"
        assert results[0].text == "response-1"
        assert results[0].success is True
        assert results[1].command == "/compact"
        assert results[1].text == "response-2"
        assert results[2].command == "/cost"
        assert results[2].text == "response-3"

    @pytest.mark.asyncio
    async def test_error_in_one_command(self):
        """activate_skills captures errors per-command without stopping."""
        client = Client(["echo", "hi"])
        client._connected = True

        async def fake_activate(cmd, *, session_id=None):
            if cmd == "/bad":
                raise RuntimeError("skill failed")
            return "ok"

        with patch.object(client, "activate_skill", side_effect=fake_activate):
            results = await client.activate_skills(["/help", "/bad", "/cost"])

        assert len(results) == 3
        assert results[0].success is True
        assert results[0].text == "ok"
        assert results[1].success is False
        assert results[1].error == "skill failed"
        assert results[2].success is True
        assert results[2].text == "ok"

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """activate_skills with empty list returns empty list."""
        client = Client(["echo", "hi"])
        client._connected = True

        results = await client.activate_skills([])
        assert results == []

    @pytest.mark.asyncio
    async def test_passes_session_id(self):
        """activate_skills forwards session_id to each call."""
        client = Client(["echo", "hi"])
        client._connected = True

        calls = []

        async def fake_activate(cmd, *, session_id=None):
            calls.append((cmd, session_id))
            return ""

        with patch.object(client, "activate_skill", side_effect=fake_activate):
            await client.activate_skills(["/a", "/b"], session_id="sess-x")

        assert calls == [("/a", "sess-x"), ("/b", "sess-x")]


# ---------------------------------------------------------------------------
# Session wrappers
# ---------------------------------------------------------------------------


class TestSessionActivateSkill:
    @pytest.mark.asyncio
    async def test_requires_session_id(self):
        """Session.activate_skill raises if session not created."""
        client = Client(["echo"])
        session = Session(client)

        with pytest.raises(SessionError, match="session not created"):
            await session.activate_skill("/help")

    @pytest.mark.asyncio
    async def test_delegates_to_client(self):
        """Session.activate_skill delegates to client with session_id."""
        client = Client(["echo"])
        session = Session(client)
        session._session_id = "sess-42"

        with patch.object(
            client, "activate_skill", new_callable=AsyncMock, return_value="result"
        ) as mock:
            result = await session.activate_skill("/help")

        mock.assert_called_once_with("/help", session_id="sess-42")
        assert result == "result"


class TestSessionActivateSkills:
    @pytest.mark.asyncio
    async def test_requires_session_id(self):
        """Session.activate_skills raises if session not created."""
        client = Client(["echo"])
        session = Session(client)

        with pytest.raises(SessionError, match="session not created"):
            await session.activate_skills(["/help"])

    @pytest.mark.asyncio
    async def test_delegates_to_client(self):
        """Session.activate_skills delegates to client with session_id."""
        client = Client(["echo"])
        session = Session(client)
        session._session_id = "sess-42"

        expected = [SkillResult(command="/help", text="ok")]
        with patch.object(
            client, "activate_skills", new_callable=AsyncMock, return_value=expected
        ) as mock:
            result = await session.activate_skills(["/help"])

        mock.assert_called_once_with(["/help"], session_id="sess-42")
        assert result == expected


# ---------------------------------------------------------------------------
# Agent enum
# ---------------------------------------------------------------------------


class TestAgentEnum:
    def test_registry_agents(self):
        """Registry agents have simple string values (no spaces)."""
        assert Agent.CLAUDE == "claude-acp"
        assert Agent.OPENCODE == "opencode"
        assert Agent.GOOSE == "goose"
        assert Agent.CODEX == "codex-acp"
        assert Agent.AUGGIE == "auggie"
        assert Agent.GEMINI == "gemini"

    def test_local_agents(self):
        """Local-only agents have space-separated command values."""
        assert Agent.HERMES == "hermes acp"

    def test_is_string(self):
        """Agent enum members are str subclass instances."""
        assert isinstance(Agent.OPENCODE, str)
        assert isinstance(Agent.HERMES, str)

    def test_to_command(self):
        """to_command() splits value into a command list."""
        assert Agent.HERMES.to_command() == ["hermes", "acp"]
        assert Agent.OPENCODE.to_command() == ["opencode"]
        assert Agent.CLAUDE.to_command() == ["claude-acp"]

    def test_is_registry(self):
        """is_registry distinguishes registry vs local-only agents."""
        assert Agent.CLAUDE.is_registry is True
        assert Agent.OPENCODE.is_registry is True
        assert Agent.HERMES.is_registry is False

    def test_name_attribute(self):
        assert Agent.CLAUDE.name == "CLAUDE"
        assert Agent.HERMES.name == "HERMES"
