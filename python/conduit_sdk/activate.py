"""Top-level convenience function for one-shot agent queries.

Usage::

    from conduit_sdk import query

    async for message in query(prompt="Hello!", agent="claude-acp"):
        print(message.text())
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from conduit_sdk.options import AgentOptions
from conduit_sdk.registry import Registry
from conduit_sdk.types import Message


async def query(
    *,
    prompt: str,
    agent: str,
    prefer: str | None = None,
    registry_url: str | None = None,
    options: AgentOptions | None = None,
    timeout: int = 30,
) -> AsyncIterator[Message]:
    """Send a single prompt to a registry agent and stream the response.

    This is the simplest way to talk to an ACP agent. It handles registry
    lookup, client creation, connection, prompting, and cleanup.

    Parameters
    ----------
    prompt:
        The text to send to the agent.
    agent:
        Registry agent ID (e.g. ``"claude-acp"``).
    prefer:
        Preferred distribution type (``"npx"``, ``"uvx"``, ``"binary"``).
    registry_url:
        Custom registry URL. Uses the default ACP registry if ``None``.
    options:
        Additional :class:`AgentOptions` for the client.
    timeout:
        Connection timeout in seconds.

    Yields
    ------
    :class:`Message`
        Response messages as they arrive from the agent.
    """
    # Import here to avoid circular dependency.
    from conduit_sdk.client import Client

    registry_kwargs: dict[str, Any] = {}
    if registry_url is not None:
        registry_kwargs["registry_url"] = registry_url

    registry = Registry(**registry_kwargs)
    await registry.fetch()

    cmd, env = await registry.resolve_command(agent, prefer=prefer)

    merged_env = dict(env)
    if options and options.env:
        merged_env.update(options.env)

    client = Client(
        cmd,
        env=merged_env or None,
        timeout=timeout,
        options=options,
    )

    async with client:
        async for message in client.prompt(prompt):
            yield message
