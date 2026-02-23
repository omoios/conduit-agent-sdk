"""Tests for conduit_sdk type definitions."""

from __future__ import annotations

import pytest

from conduit_sdk import (
    Capabilities,
    ClientConfig,
    ContentBlock,
    ContentType,
    Message,
    MessageRole,
    SessionUpdate,
    ToolDefinition,
    ToolSchema,
    UpdateKind,
)


class TestCapabilities:
    def test_defaults(self):
        caps = Capabilities()
        assert caps.sessions is False
        assert caps.tools is False
        assert caps.proxy is False
        assert caps.modes == []
        assert caps.models == []

    def test_custom_values(self):
        caps = Capabilities(
            sessions=True,
            tools=True,
            proxy=False,
            modes=["ask", "code"],
            models=["claude-4"],
        )
        assert caps.sessions is True
        assert caps.modes == ["ask", "code"]

    def test_repr(self):
        caps = Capabilities()
        assert "Capabilities" in repr(caps)


class TestMessage:
    def test_text_extraction(self):
        blocks = [
            ContentBlock(ContentType.Text, text="Hello "),
            ContentBlock(ContentType.Text, text="world"),
        ]
        msg = Message(MessageRole.Assistant, blocks)
        assert msg.text() == "Hello world"

    def test_text_ignores_non_text_blocks(self):
        blocks = [
            ContentBlock(ContentType.Text, text="Hello"),
            ContentBlock(ContentType.ToolUse, tool_name="read_file"),
        ]
        msg = Message(MessageRole.Assistant, blocks)
        assert msg.text() == "Hello"

    def test_empty_message(self):
        msg = Message(MessageRole.User, [])
        assert msg.text() == ""

    def test_session_id(self):
        msg = Message(MessageRole.User, [], session_id="abc-123")
        assert msg.session_id == "abc-123"


class TestContentBlock:
    def test_text_block(self):
        block = ContentBlock(ContentType.Text, text="Hello")
        assert block.content_type == ContentType.Text
        assert block.text == "Hello"
        assert block.tool_name is None

    def test_tool_use_block(self):
        block = ContentBlock(
            ContentType.ToolUse,
            tool_name="read_file",
            tool_input='{"path": "/tmp/test"}',
            tool_use_id="tu_123",
        )
        assert block.tool_name == "read_file"
        assert block.tool_use_id == "tu_123"


class TestSessionUpdate:
    def test_text_delta(self):
        update = SessionUpdate(UpdateKind.TextDelta, text="chunk")
        assert update.kind == UpdateKind.TextDelta
        assert update.text == "chunk"

    def test_error(self):
        update = SessionUpdate(UpdateKind.Error, error="something broke")
        assert update.error == "something broke"


class TestClientConfig:
    def test_minimal(self):
        config = ClientConfig(command=["claude", "--agent"])
        assert config.command == ["claude", "--agent"]
        assert config.cwd is None
        assert config.env == {}
        assert config.timeout_secs == 30

    def test_full(self):
        config = ClientConfig(
            command=["goose"],
            cwd="/tmp",
            env={"GOOSE_MODEL": "claude-4"},
            timeout_secs=60,
        )
        assert config.cwd == "/tmp"
        assert config.env["GOOSE_MODEL"] == "claude-4"


class TestToolDefinition:
    def test_creation(self):
        defn = ToolDefinition(
            name="read_file",
            description="Read a file",
            input_schema='{"type": "object"}',
        )
        assert defn.name == "read_file"
        assert "read_file" in repr(defn)


class TestToolSchema:
    def test_to_json(self):
        schema = ToolSchema(
            properties={"path": {"type": "string"}},
            required=["path"],
        )
        import json

        parsed = json.loads(schema.to_json())
        assert parsed["type"] == "object"
        assert "path" in parsed["properties"]
        assert "path" in parsed["required"]
