#!/usr/bin/env python3
"""Example 28 — Rate-limit awareness.

Demonstrates how to detect and react to rate-limit extension
notifications that some ACP agents (e.g. Claude) emit during a session.

The ``UpdateKind.RateLimit`` event carries a JSON payload with
utilization data that you can parse into a ``RateLimitInfo`` object
for structured access.

Usage:
    uv run examples/28_rate_limit_awareness.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from conduit_sdk import Client, RateLimitInfo, UpdateKind


async def main() -> None:
    agent = os.environ.get("CONDUIT_AGENT", "claude-acp")
    print(f"Connecting to {agent}...")

    async with await Client.from_registry(agent) as client:
        session = await client.new_session()
        print(f"Session: {session.session_id}")

        # Send a prompt and watch for rate-limit events alongside normal output.
        await client.prompt_stream(
            "Write a short haiku about code.",
            session_id=session.session_id,
        )

        text_parts: list[str] = []

        while True:
            update = await client.recv_update()
            if update is None:
                break

            if update.kind == UpdateKind.TextDelta:
                text_parts.append(update.text or "")
                print(update.text or "", end="", flush=True)

            elif update.kind == UpdateKind.RateLimit:
                # Parse the structured rate-limit info.
                info = RateLimitInfo.from_json(update.rate_limit_json or "{}")
                print(f"\n⚠️  Rate limit event:")
                print(f"   Status:      {info.status}")
                print(f"   Utilization: {info.utilization:.0%}")
                print(f"   Type:        {info.rate_limit_type}")
                print(f"   Resets at:   {info.resets_at}")
                if info.utilization >= 0.9:
                    print("   🚨 WARNING: Very close to rate limit!")

            elif update.kind == UpdateKind.Done:
                print(f"\n--- Done (stop_reason={update.stop_reason}) ---")
                break

        if text_parts:
            print(f"\nFull response ({len(''.join(text_parts))} chars)")


if __name__ == "__main__":
    asyncio.run(main())
