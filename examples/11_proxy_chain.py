# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""11 — Proxy Chain: Compose ContextInjector and ResponseFilter proxies.

Demonstrates proxy composition: inject system context into prompts
and filter/truncate responses from the agent.

    uv run examples/11_proxy_chain.py
"""

import asyncio

from conduit_sdk import Client, ContextInjector, ProxyChain, ResponseFilter


async def main():
    # Build a proxy chain.
    chain = ProxyChain()
    chain.add(ContextInjector(context="Always respond in formal English. Be concise."))
    chain.add(ResponseFilter(max_tokens=500))

    print(f"Proxy chain: {chain}")

    # In production, the chain would be passed to the client for activation.
    # For now, this demonstrates the API.
    client = await Client.from_registry("claude-acp")

    async with client:
        response = await client.prompt_sync("What is ACP?")
        for message in response:
            print(message.text())


if __name__ == "__main__":
    asyncio.run(main())
