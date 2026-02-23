"""Proxy chain builder for conduit-agent-sdk.

Proxies intercept and transform ACP messages between the client and
agent. They compose into ordered chains managed by a conductor.

Example::

    chain = ProxyChain()
    chain.add(ContextInjector(context="You are helpful."))
    chain.add(ResponseFilter(max_tokens=1000))
    await chain.build()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from conduit_sdk._conduit_sdk import ProxyConfig, RustProxyChain
from conduit_sdk.exceptions import ProxyError


class Proxy(ABC):
    """Base class for custom ACP proxies.

    Subclass this to create proxies that transform messages flowing
    between the client and agent. Each proxy must define a ``command``
    that launches its subprocess.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name for this proxy."""

    @property
    @abstractmethod
    def command(self) -> list[str]:
        """Shell command to spawn this proxy's subprocess."""

    def to_config(self) -> ProxyConfig:
        """Convert to the Rust-side proxy configuration."""
        return ProxyConfig(name=self.name, command=self.command)


class ProxyChain:
    """Builder for composing an ordered chain of proxies.

    The chain is built and activated via :meth:`build`, which spawns
    each proxy subprocess and connects them using the sacp-conductor.
    """

    def __init__(self) -> None:
        self._rust_chain = RustProxyChain()
        self._proxies: list[Proxy] = []

    def add(self, proxy: Proxy) -> ProxyChain:
        """Append a proxy to the end of the chain. Returns self for chaining."""
        self._proxies.append(proxy)
        return self

    def insert(self, index: int, proxy: Proxy) -> ProxyChain:
        """Insert a proxy at the given position. Returns self for chaining."""
        self._proxies.insert(index, proxy)
        return self

    async def build(self) -> None:
        """Build and activate the proxy chain.

        Spawns each proxy subprocess and connects them via the conductor.
        """
        if not self._proxies:
            raise ProxyError("cannot build an empty proxy chain")

        for proxy in self._proxies:
            await self._rust_chain.add(proxy.to_config())
        await self._rust_chain.build()

    @property
    def proxies(self) -> list[Proxy]:
        """The current ordered list of proxies."""
        return list(self._proxies)

    def __repr__(self) -> str:
        names = [p.name for p in self._proxies]
        return f"ProxyChain({' -> '.join(names) or 'empty'})"


# ---------------------------------------------------------------------------
# Built-in proxy implementations
# ---------------------------------------------------------------------------


class ContextInjector(Proxy):
    """Proxy that injects system context into prompts sent to the agent."""

    def __init__(self, context: str, *, cmd: list[str] | None = None) -> None:
        self._context = context
        self._cmd = cmd or ["conduit-proxy-context"]

    @property
    def name(self) -> str:
        return "context-injector"

    @property
    def command(self) -> list[str]:
        return self._cmd

    @property
    def context(self) -> str:
        return self._context


class ResponseFilter(Proxy):
    """Proxy that filters or truncates agent responses."""

    def __init__(self, *, max_tokens: int = 0, cmd: list[str] | None = None) -> None:
        self._max_tokens = max_tokens
        self._cmd = cmd or ["conduit-proxy-filter"]

    @property
    def name(self) -> str:
        return "response-filter"

    @property
    def command(self) -> list[str]:
        return self._cmd

    @property
    def max_tokens(self) -> int:
        return self._max_tokens
