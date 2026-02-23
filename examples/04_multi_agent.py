# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""04 — Multi-Agent: Talk to different ACP agents.

Connects to multiple agents from the registry and sends each
the same prompt, printing their responses side by side.

    uv run examples/04_multi_agent.py
"""

import asyncio

from conduit_sdk import Client


AGENTS = ["claude-acp", "codex-acp", "opencode"]
PROMPT = "What is your name and what can you do? Answer in one sentence."


async def ask_agent(agent_id: str) -> str:
    """Send a prompt to one agent and return its text response."""
    try:
        client = await Client.from_registry(agent_id)
        async with client:
            parts: list[str] = []
            async for message in client.prompt(PROMPT):
                parts.append(message.text())
            return "".join(parts)
    except Exception as exc:
        return f"[error: {exc}]"


async def main():
    print(f"Asking {len(AGENTS)} agents: {PROMPT!r}\n")

    # Ask all agents sequentially (see example 12 for parallel).
    for agent_id in AGENTS:
        print(f"--- {agent_id} ---")
        response = await ask_agent(agent_id)
        print(response)
        print()


if __name__ == "__main__":
    asyncio.run(main())
