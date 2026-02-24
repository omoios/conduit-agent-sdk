# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""19 — Cancel Session: Cancel a running prompt mid-execution.

Demonstrates using cancel() to stop a prompt that's taking too long
or needs to be aborted. The cancellation is sent as an ACP
CancelNotification to the agent.

    uv run examples/19_cancel_session.py
"""

import asyncio

from conduit_sdk import Client


async def prompt_with_timeout(session, text: str, timeout_seconds: float = 2.0):
    """Run a prompt with a timeout, cancelling if it takes too long."""
    messages = []

    async def collect():
        nonlocal messages
        messages = await session.prompt(text)

    task = asyncio.create_task(collect())

    try:
        await asyncio.wait_for(task, timeout=timeout_seconds)
        return messages, False  # Not cancelled
    except asyncio.TimeoutError:
        # Cancel the session prompt.
        await session.cancel()
        return messages, True  # Was cancelled


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        session = await client.new_session()
        print(f"Session created: {session.session_id}\n")

        # Try a prompt with a very short timeout.
        print("Attempting prompt with 2 second timeout...")
        messages, was_cancelled = await prompt_with_timeout(
            session,
            "Write a detailed essay about the history of computing.",
            timeout_seconds=2.0,
        )

        if was_cancelled:
            print("\n[Prompt was cancelled due to timeout]")
            if messages:
                print("Partial response received:")
                for msg in messages:
                    print(msg.text())
        else:
            print("Prompt completed within timeout:")
            for msg in messages:
                print(msg.text())

        # Also demonstrate client.cancel() directly.
        print("\n--- Using client.cancel() directly ---")
        print("Creating another session...")

        session2 = await client.new_session()
        print(f"Session 2 created: {session2.session_id}")

        # Start a task that will be cancelled.
        async def long_prompt():
            return await session2.prompt(
                "Count from 1 to 1000, explaining each number."
            )

        task = asyncio.create_task(long_prompt())

        # Wait a moment then cancel via client.
        await asyncio.sleep(0.5)
        print("Cancelling via client.cancel()...")
        await client.cancel(session2.session_id)

        try:
            await task
        except Exception as e:
            print(f"Task was cancelled: {type(e).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
