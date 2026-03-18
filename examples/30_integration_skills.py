# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""30 — Integration: Skills Discovery & Activation (live agent test).

End-to-end integration that connects to a real ACP agent, discovers all
available slash commands, activates individual skills, and runs a batch
activation — proving the full pipeline works.

    uv run examples/30_integration_skills.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from conduit_sdk import Agent, Client, SkillResult
from conduit_sdk._conduit_sdk import UpdateKind


async def main() -> None:
    print("=" * 70)
    print("  Conduit SDK — Skills Integration Test")
    print("=" * 70)

    # --- Phase 1: Connect via registry ---
    # Agent enum resolves to the registry ID automatically.
    # Agent.OPENCODE = "opencode", Agent.CLAUDE = "claude-acp", etc.
    # You can also pass a plain string: Client.from_registry("opencode")
    agent = Agent.OPENCODE
    print(f"\n[1] Connecting to {agent.name} via registry (id={agent.value!r})...")
    t0 = time.monotonic()
    client = await Client.from_registry(agent)
    async with client:
        elapsed = time.monotonic() - t0
        print(f"    Connected in {elapsed:.1f}s")

        # Show agent info
        info = await client.agent_info
        if info:
            print(f"    Agent: {info.get('name', '?')} v{info.get('version', '?')}")

        # --- Phase 2: Discover commands via streaming ---
        print("\n[2] Discovering available commands via streaming...")
        discovered_commands: list[dict] = []
        text_response = []

        async for update in client.prompt_stream("Say hi in one sentence."):
            if update.kind == UpdateKind.CommandsUpdate and update.commands_json:
                discovered_commands = json.loads(update.commands_json)
            elif update.kind == UpdateKind.TextDelta and update.text:
                text_response.append(update.text)
            elif update.kind == UpdateKind.Done:
                break

        greeting = "".join(text_response).strip()
        if greeting:
            print(f"    Agent says: {greeting[:120]}")

        # --- Phase 3: List all discovered skills ---
        print(f"\n[3] Discovered {len(discovered_commands)} slash commands:")
        if discovered_commands:
            # Sort by name for readability
            sorted_cmds = sorted(
                discovered_commands, key=lambda c: c.get("name", c.get("id", ""))
            )
            for i, cmd in enumerate(sorted_cmds, 1):
                name = cmd.get("name", cmd.get("id", "unknown"))
                desc = cmd.get("description", cmd.get("userDescription", ""))
                # Truncate long descriptions
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                print(f"    {i:3d}. {name:<30s} {desc}")
            print()

            # Show raw JSON of first command for schema visibility
            print("    Raw schema of first command:")
            print(f"    {json.dumps(sorted_cmds[0], indent=2)[:400]}")
        else:
            print("    (No commands received via CommandsUpdate)")
            print("    Note: some agents send commands later or on mode changes.")

        # --- Phase 4: activate_skill() — single command ---
        print('\n[4] Testing activate_skill("/help")...')
        t0 = time.monotonic()
        try:
            help_text = await client.activate_skill("/help")
            elapsed = time.monotonic() - t0
            lines = help_text.strip().split("\n")
            print(f"    Got {len(lines)} lines in {elapsed:.1f}s")
            # Show first 10 lines
            for line in lines[:10]:
                print(f"    | {line}")
            if len(lines) > 10:
                print(f"    | ... ({len(lines) - 10} more lines)")
        except Exception as exc:
            print(f"    ERROR: {exc}")

        # --- Phase 5: activate_skill() — without / prefix (normalization test) ---
        print('\n[5] Testing activate_skill("cost") — auto-prefix normalization...')
        t0 = time.monotonic()
        try:
            cost_text = await client.activate_skill("cost")
            elapsed = time.monotonic() - t0
            preview = cost_text.strip()[:200]
            print(f"    Response ({elapsed:.1f}s): {preview}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

        # --- Phase 6: activate_skills() — batch ---
        print("\n[6] Testing activate_skills(['/help', 'cost']) — batch...")
        t0 = time.monotonic()
        try:
            results = await client.activate_skills(["/help", "cost"])
            elapsed = time.monotonic() - t0
            print(f"    Completed {len(results)} skills in {elapsed:.1f}s")
            for r in results:
                status = "✓" if r.success else "✗"
                preview = r.text.strip()[:80] if r.text else "(empty)"
                error_note = f" — {r.error}" if r.error else ""
                print(f"    {status} {r.command:<15s} {preview}{error_note}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

    # --- Summary ---
    print("\n" + "=" * 70)
    print("  Integration test complete.")
    print(f"  Commands discovered: {len(discovered_commands)}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
