# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""12 — Parallel Agents: Query multiple agents concurrently.

Uses ``asyncio.gather()`` to send the same prompt to several ACP
agents in parallel and collect their responses.

    uv run examples/12_parallel_agents.py
"""

import asyncio

from conduit_sdk import Client


PROMPT = "In one sentence, what makes you unique as a coding agent?"


async def ask_agent(agent_id: str) -> tuple[str, str]:
    """Query a single agent and return (agent_id, response_text)."""
    try:
        client = await Client.from_registry(agent_id)
        async with client:
            parts: list[str] = []
            async for message in client.prompt(PROMPT):
                parts.append(message.text())
            return agent_id, "".join(parts)
    except Exception as exc:
        return agent_id, f"[error: {exc}]"


async def main():
    agents = ["claude-acp", "codex-acp", "opencode"]
    print(f"Querying {len(agents)} agents in parallel...\n")

    results = await asyncio.gather(*(ask_agent(aid) for aid in agents))

    for agent_id, response in results:
        print(f"--- {agent_id} ---")
        print(response)
        print()


if __name__ == "__main__":
    asyncio.run(main())
