"""Tests for conductor command construction and proxy-chain wiring.

These tests verify the pure-Python conductor adapter layer without
requiring the ``agent-client-protocol-conductor`` binary on PATH.
"""

from __future__ import annotations

import asyncio

import pytest

from conduit_sdk.exceptions import ProxyError
from conduit_sdk.proxy import (
    ContextInjector,
    ProxyChain,
    conductor_available,
    conductor_command,
)


class TestConductorAvailable:
    """conductor_available() contract."""

    def test_returns_bool(self) -> None:
        result = conductor_available()
        assert isinstance(result, bool)

    def test_returns_bool_with_custom_name(self) -> None:
        result = conductor_available(
            conductor="this-binary-definitely-does-not-exist-12345",
        )
        assert isinstance(result, bool)
        assert result is False


class TestConductorCommand:
    """conductor_command() contract."""

    def test_one_proxy(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="be helpful"))
        argv = conductor_command(
            base_command=["uv", "run", "python", "-m", "my_agent"],
            chain=chain,
        )
        # Expected: [conductor, "agent", shlex.join(proxy_cmd), shlex.join(base_cmd)]
        assert argv[0] == "agent-client-protocol-conductor"
        assert argv[1] == "agent"
        assert argv[2] == "conduit-proxy-context"
        assert argv[3] == "uv run python -m my_agent"
        assert len(argv) == 4

    def test_two_proxies(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="be concise"))
        chain.add(ContextInjector(context="use tools", cmd=["my-proxy", "--opt=1"]))
        argv = conductor_command(
            base_command=["python", "-c", "print(1)"],
            chain=chain,
        )
        assert argv[0] == "agent-client-protocol-conductor"
        assert argv[1] == "agent"
        # shlex.join(["my-proxy", "--opt=1"]) → "my-proxy --opt=1" (no quoting needed)
        assert argv[2] == "conduit-proxy-context"
        assert argv[3] == "my-proxy --opt=1"
        # shlex.join(["python", "-c", "print(1)"]) → "python -c print(1)"
        assert argv[4] == "python -c 'print(1)'"
        assert len(argv) == 5

    def test_empty_chain_raises(self) -> None:
        chain = ProxyChain()
        with pytest.raises(ProxyError, match="proxy chain is empty"):
            conductor_command(base_command=["agent"], chain=chain)

    def test_custom_conductor_name(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="test"))
        argv = conductor_command(
            base_command=["agent"],
            chain=chain,
            conductor="/custom/path/conductor",
        )
        assert argv[0] == "/custom/path/conductor"
        assert argv[2] == "conduit-proxy-context"
        assert argv[3] == "agent"

    def test_complex_base_command_quoting(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="x"))
        argv = conductor_command(
            base_command=[
                "my-agent", "--model", "claude-3.5", "--path", "/tmp/test dir",
            ],
            chain=chain,
        )
        # shlex.join quotes only args that need it (spaces in "/tmp/test dir")
        assert argv[3] == "my-agent --model claude-3.5 --path '/tmp/test dir'"


class TestProxyChainBuild:
    """ProxyChain.build() with the new conductor path."""

    def test_build_with_base_command_returns_list(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="helpful"))

        async def run() -> list[str] | None:
            return await chain.build(base_command=["python", "agent.py"])

        result = asyncio.run(run())
        assert isinstance(result, list)
        assert len(result) == 4
        assert result[0] == "agent-client-protocol-conductor"
        assert result[3] == "python agent.py"

    def test_build_empty_chain_raises(self) -> None:
        chain = ProxyChain()

        async def run() -> None:
            with pytest.raises(ProxyError, match="cannot build an empty proxy chain"):
                await chain.build(base_command=["agent"])

        asyncio.run(run())

    def test_build_require_binary_raises_when_missing(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="test"))

        async def run() -> None:
            with pytest.raises(
                ProxyError,
                match="required for live proxy chaining",
            ):
                await chain.build(
                    base_command=["agent"],
                    require_binary=True,
                )

        asyncio.run(run())


class TestProxyChainWrapCommand:
    """ProxyChain.wrap_command() contract."""

    def test_wrap_command_returns_proper_argv(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="x"))
        argv = chain.wrap_command(["python", "agent.py"])
        assert isinstance(argv, list)
        assert argv[3] == "python agent.py"

    def test_wrap_command_empty_chain_raises(self) -> None:
        chain = ProxyChain()
        with pytest.raises(ProxyError, match="cannot build an empty proxy chain"):
            chain.wrap_command(["agent"])

    def test_wrap_command_require_binary_raises(self) -> None:
        chain = ProxyChain()
        chain.add(ContextInjector(context="x"))
        with pytest.raises(
            ProxyError,
            match="required for live proxy chaining",
        ):
            chain.wrap_command(["agent"], require_binary=True)
