# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""23 — Streaming Updates: Stream all update types from the agent.

Demonstrates prompt_stream() and inspecting each SessionEvent variant
to handle Text, Thought, ToolUseUpdate, ModeChange, Plan, ConfigUpdate,
Usage, SessionInfo, CommandsUpdate, and Done updates.

    uv run examples/23_streaming_updates.py
"""

import asyncio

from conduit_sdk import Client, AgentOptions
from conduit_sdk.events import TextDelta, ThoughtDelta, ToolCallUpdate, ModeChange, Plan, ConfigUpdate, Usage, SessionInfo, AvailableCommands, Done


async def main():
    options = AgentOptions(system_prompt="Write a short poem about coding.")

    client = Client(["claude", "--agent"], options=options)

    async with client:
        print("Streaming updates for a prompt...\n")

        async for event in client.prompt_stream("Write a haiku about Python."):
            if isinstance(event, TextDelta):
                print(f"[Text] {event.text}", end="")
            elif isinstance(event, ThoughtDelta):
                print(f"[Thought] {event.text}")
            elif isinstance(event, ToolCallUpdate):
                print(f"[ToolUse] status={event.status}, output={event.output}" if event.output else f"[ToolUse] status={event.status}")
            elif isinstance(event, ModeChange):
                print(f"[ModeChange] mode_id={event.mode_id}")
            elif isinstance(event, Plan):
                print(f"[Plan] {event.entries}")
            elif isinstance(event, ConfigUpdate):
                print(f"[ConfigUpdate] {event.config}")
            elif isinstance(event, Usage):
                print(f"[Usage] used={event.used}, size={event.size}")
            elif isinstance(event, SessionInfo):
                print(f"[SessionInfo] title={event.title}")
            elif isinstance(event, AvailableCommands):
                print(f"[Commands] {event.commands}")
            elif isinstance(event, Done):
                print(f"\n[Done] stop_reason={event.stop_reason}")

        print("\nStreaming complete.")


if __name__ == "__main__":
    asyncio.run(main())
