# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""23 — Streaming Updates: Stream all update types from the agent.

Demonstrates prompt_stream() and inspecting each SessionUpdate's kind field
to handle Text, Thought, ToolUseUpdate, ModeChange, Plan, ConfigUpdate,
Usage, SessionInfo, CommandsUpdate, and Done updates.

    uv run examples/23_streaming_updates.py
"""

import asyncio

from conduit_sdk import Client, AgentOptions
from conduit_sdk._conduit_sdk import UpdateKind


async def main():
    options = AgentOptions(system_prompt="Write a short poem about coding.")

    client = Client(["claude", "--agent"], options=options)

    async with client:
        print("Streaming updates for a prompt...\n")

        async for update in client.prompt_stream("Write a haiku about Python."):
            kind = update.kind

            if kind == UpdateKind.Text:
                print(f"[Text] {update.text}", end="")
            elif kind == UpdateKind.Thought:
                print(f"[Thought] {update.text}")
            elif kind == UpdateKind.ToolUseUpdate:
                print(f"[ToolUse] status={update.tool_status}, kind={update.tool_kind}")
            elif kind == UpdateKind.ModeChange:
                print(f"[ModeChange] mode_id={update.mode_id}")
            elif kind == UpdateKind.Plan:
                print(f"[Plan] {update.plan_json}")
            elif kind == UpdateKind.ConfigUpdate:
                print(f"[ConfigUpdate] {update.config_json}")
            elif kind == UpdateKind.Usage:
                print(f"[Usage] {update.usage_json}")
            elif kind == UpdateKind.SessionInfo:
                print(f"[SessionInfo] {update.session_info_json}")
            elif kind == UpdateKind.CommandsUpdate:
                print(f"[Commands] {update.commands_json}")
            elif kind == UpdateKind.Done:
                print(f"\n[Done] stop_reason={update.stop_reason}")

        print("\nStreaming complete.")


if __name__ == "__main__":
    asyncio.run(main())
