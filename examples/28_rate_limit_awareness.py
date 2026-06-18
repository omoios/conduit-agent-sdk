#!/usr/bin/env python3
"""Example 28 — Rate-limit awareness.

Demonstrates how to detect and react to rate-limit extension
notifications that some ACP agents (e.g. Claude) emit during a session.

The ``RateLimit`` SessionEvent carries structured fields
for direct access.

Usage:
    uv run examples/28_rate_limit_awareness.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from conduit_sdk import Client
from conduit_sdk.events import TextDelta, RateLimit, Done


async def main() -> None:
    agent = os.environ.get("CONDUIT_AGENT", "claude-acp")
    print(f"Connecting to {agent}...")

    async with await Client.from_registry(agent) as client:
        session = await client.new_session()
        print(f"Session: {session.session_id}")

        text_parts: list[str] = []

        async for event in client.prompt_stream(
            "Write a short haiku about code.",
            session_id=session.session_id,
        ):
            if isinstance(event, TextDelta):
                text_parts.append(event.text or "")
                print(event.text or "", end="", flush=True)

            elif isinstance(event, RateLimit):
                print(f"\n⚠️  Rate limit event:")
                print(f"   Status:      {event.status}")
                print(f"   Utilization: {event.utilization:.0%}")
                print(f"   Type:        {event.rate_limit_type}")
                print(f"   Resets at:   {event.resets_at}")
                if event.utilization >= 0.9:
                    print("   🚨 WARNING: Very close to rate limit!")

            elif isinstance(event, Done):
                print(f"\n--- Done (stop_reason={event.stop_reason}) ---")
                break

        if text_parts:
            print(f"\nFull response ({len(''.join(text_parts))} chars)")


if __name__ == "__main__":
    asyncio.run(main())
