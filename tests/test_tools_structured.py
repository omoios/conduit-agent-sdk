"""Tests for conduit_sdk.tools: StructuredOutput validation (@constrained_tool).

Covers:
- stdlib-only JSON Schema subset validator (_validate_against_schema)
- @constrained_tool decorator registration & validation
- MCP e2e dispatch (valid / invalid output → isError)
"""

from __future__ import annotations

import json

import pytest

from conduit_sdk.exceptions import ToolError
from conduit_sdk.tools import (
    McpSdkServerConfig,
    StructuredOutputValidationError,
    _validate_against_schema,
    constrained_tool,
    create_sdk_mcp_server,
    tool,
)


# ===== _validate_against_schema unit tests ==============================


class TestValidateAgainstSchema:
    """Direct unit tests for the stdlib-only JSON Schema validator."""

    def test_valid_object(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "active": {"type": "boolean"},
                "score": {"type": "number"},
            },
            "required": ["name", "age"],
        }
        value = {"name": "Alice", "age": 30, "active": True, "score": 95.5}
        assert _validate_against_schema(value, schema) == []

    def test_missing_required_field(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
        }
        value = {"name": "Alice"}
        errors = _validate_against_schema(value, schema)
        assert len(errors) == 1
        assert "/age: is required" in errors

    def test_wrong_type(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        value = {"count": "not a number"}
        errors = _validate_against_schema(value, schema)
        assert len(errors) == 1
        assert "/count: must be integer" in errors

    def test_enum_violation(self):
        schema = {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["red", "green", "blue"]},
            },
            "required": ["color"],
        }
        value = {"color": "yellow"}
        errors = _validate_against_schema(value, schema)
        assert len(errors) == 1
        assert "/color: must be one of" in errors[0]

    def test_additional_properties_false(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
            "required": ["name"],
        }
        value = {"name": "Alice", "extra": "nope"}
        errors = _validate_against_schema(value, schema)
        assert len(errors) == 1
        assert "/extra: unexpected property" in errors

    def test_nested_object_error_path(self):
        schema = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {
                        "zip": {"type": "string"},
                    },
                    "required": ["zip"],
                },
            },
        }
        value = {"address": {}}
        errors = _validate_against_schema(value, schema)
        assert len(errors) == 1
        assert "/address/zip: is required" in errors

    def test_integer_validates_as_number(self):
        """An integer value should pass a 'number' schema type."""
        schema = {"type": "number"}
        assert _validate_against_schema(42, schema) == []

    def test_float_validates_as_number(self):
        schema = {"type": "number"}
        assert _validate_against_schema(3.14, schema) == []

    def test_bool_does_not_validate_as_integer(self):
        """Python bool is a subclass of int — must be explicitly rejected."""
        schema = {"type": "integer"}
        errors = _validate_against_schema(True, schema)
        assert len(errors) == 1

    def test_minimum_and_maximum(self):
        schema = {"type": "integer", "minimum": 1, "maximum": 100}
        assert _validate_against_schema(50, schema) == []
        errors_low = _validate_against_schema(0, schema)
        assert len(errors_low) == 1
        assert "must be >= 1" in errors_low[0]
        errors_high = _validate_against_schema(200, schema)
        assert len(errors_high) == 1
        assert "must be <= 100" in errors_high[0]

    def test_string_min_max_length(self):
        schema = {"type": "string", "minLength": 2, "maxLength": 5}
        assert _validate_against_schema("abc", schema) == []
        errors_short = _validate_against_schema("a", schema)
        assert len(errors_short) == 1
        assert "length must be >= 2" in errors_short[0]
        errors_long = _validate_against_schema("abcdef", schema)
        assert len(errors_long) == 1
        assert "length must be <= 5" in errors_long[0]

    def test_pattern(self):
        schema = {"type": "string", "pattern": r"^[A-Z]\w+"}
        assert _validate_against_schema("Alice", schema) == []
        errors = _validate_against_schema("alice", schema)
        assert len(errors) == 1
        assert "must match pattern" in errors[0]

    def test_ref_ignored_gracefully(self):
        """$ref is unsupported, so we treat it as valid rather than crashing."""
        schema = {"$ref": "#/components/schemas/Foo"}
        assert _validate_against_schema({"anything": 42}, schema) == []

    def test_array_validation(self):
        schema = {
            "type": "array",
            "items": {"type": "integer"},
        }
        assert _validate_against_schema([1, 2, 3], schema) == []
        errors = _validate_against_schema([1, "two", 3], schema)
        assert len(errors) == 1
        assert "/1: must be integer" in errors[0]

    def test_multiple_errors_collected(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "string"},
            },
            "required": ["a", "b", "c"],
        }
        value = {"a": "x", "b": 42}
        errors = _validate_against_schema(value, schema)
        # c required + a wrong type + b wrong type = 3
        assert len(errors) == 3

    def test_null_type(self):
        schema = {"type": "null"}
        assert _validate_against_schema(None, schema) == []
        assert _validate_against_schema("nope", schema) != []

    def test_boolean_type(self):
        schema = {"type": "boolean"}
        assert _validate_against_schema(True, schema) == []
        assert _validate_against_schema(False, schema) == []
        errors = _validate_against_schema(1, schema)
        assert len(errors) == 1

    def test_array_items_allows_tuple(self):
        schema = {"type": "array", "items": {"type": "string"}}
        assert _validate_against_schema(("a", "b"), schema) == []


# ===== constrained_tool decorator unit tests ============================


class TestConstrainedToolDecorator:
    """Tests for the @constrained_tool decorator's registration and behaviour."""

    async def _good_fn_a(self, x: int) -> dict:
        return {"result": x * 2}

    async def _good_fn_b(self, x: int) -> dict:
        return {"result": x * 2}

    def test_registers_tool_definition(self):
        output_schema = {
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
        }

        @constrained_tool(output_schema, description="Double an int")
        async def double_it(x: int) -> dict:
            return {"result": x * 2}

        assert hasattr(double_it, "_tool_definition")
        assert double_it._tool_definition.name == "double_it"
        assert double_it._tool_definition.description == "Double an int"

    def test_registers_output_schema(self):
        output_schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }

        @constrained_tool(output_schema)
        async def check() -> dict:
            return {"ok": True}

        assert hasattr(check, "_output_schema")
        assert check._output_schema is output_schema

    @pytest.mark.asyncio
    async def test_valid_output_returns_value(self):
        @constrained_tool(
            {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        )
        async def greeter(name: str) -> dict:
            return {"msg": f"Hello, {name}"}

        result = await greeter(name="World")
        assert result == {"msg": "Hello, World"}

    @pytest.mark.asyncio
    async def test_invalid_output_raises_error(self):
        @constrained_tool(
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        )
        async def bad_fn() -> dict:
            return {"value": "not an int"}

        with pytest.raises(StructuredOutputValidationError) as excinfo:
            await bad_fn()
        errors = excinfo.value.errors
        assert len(errors) >= 1
        assert "must be integer" in errors[0]

    @pytest.mark.asyncio
    async def test_none_output_becomes_empty_dict(self):
        """A tool that returns None is validated as {} for leniency."""
        @constrained_tool(
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        async def noop() -> dict | None:
            return None

        result = await noop()
        assert result == {}

    def test_appears_in_get_tool_definitions(self):
        output_schema = {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        }

        @constrained_tool(output_schema, description="Get ID")
        async def get_id() -> dict:
            return {"id": 1}

        server = create_sdk_mcp_server("test", tools=[get_id])
        defs = server.get_tool_definitions()
        names = [d["name"] for d in defs]
        assert "get_id" in names


# ===== MCP e2e dispatch =================================================


@pytest.fixture()
def structured_server():
    """MCP server with one constrained tool (validating) and one plain tool."""

    @tool(description="return a fixed dict")
    async def plain_tool() -> dict:
        return {"x": 1}

    @constrained_tool(
        {
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        description="safe math",
    )
    async def safe_add(a: int) -> dict:
        return {"result": a * 2}

    @constrained_tool(
        {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        description="sometimes bad",
    )
    async def sometimes_bad() -> dict:
        return {"value": "oops"}

    return create_sdk_mcp_server("structured", tools=[plain_tool, safe_add, sometimes_bad])


@pytest.mark.asyncio
async def test_tools_list_exposes_constrained_tools(structured_server):
    resp = await structured_server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert resp.get("result") is not None
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "safe_add" in names
    assert "sometimes_bad" in names
    assert "plain_tool" in names


@pytest.mark.asyncio
async def test_valid_structured_call_returns_success(structured_server):
    resp = await structured_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "safe_add", "arguments": {"a": 21}},
        }
    )
    result = resp.get("result", {})
    assert result.get("isError") is False
    assert result.get("content") == [{"type": "text", "text": '{"result": 42}'}]


@pytest.mark.asyncio
async def test_invalid_structured_call_returns_isError(structured_server):
    """When the fn returns a schema-violating dict, the result has isError=True."""
    resp = await structured_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "sometimes_bad", "arguments": {}},
        }
    )
    result = resp.get("result", {})
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "StructuredOutputValidationError" in text
    assert "must be integer" in text


@pytest.mark.asyncio
async def test_plain_tool_still_works(structured_server):
    """Regular @tool functions are unaffected."""
    resp = await structured_server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "plain_tool", "arguments": {}},
        }
    )
    result = resp.get("result", {})
    assert result.get("isError") is False
