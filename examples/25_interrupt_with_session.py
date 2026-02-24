# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""25 — Interrupt with Session: Use interrupt() with session_id parameter.

Demonstrates the session_id parameter on interrupt() which sends an
ACP CancelNotification for a specific session. This is useful for
fine-grained control over which session to interrupt when working
with multiple sessions.

    uv run examples/25_interrupt_with_session.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        # Create two sessions.
        session1 = await client.new_session()
        session2 = await client.new_session()
        print(f"Session 1: {session1.session_id}")
        print(f"Session 2: {session2.session_id}\n")

        # Start a long-running prompt on session 1.
        async def long_task(session, name):
            try:
                messages = await session.prompt(
                    "Count from 1 to 100, saying each number with a fun fact."
                )
                return name, messages, False
            except Exception as e:
                return name, None, str(e)

        task1 = asyncio.create_task(long_task(session1, "Session 1"))
        task2 = asyncio.create_task(long_task(session2, "Session 2"))

        # Wait a moment for both to start.
        await asyncio.sleep(0.5)

        # Interrupt only session 1 using the session_id parameter.
        print("Interrupting Session 1 specifically...")
        await client.interrupt(session_id=session1.session_id)

        # Wait for both tasks to complete.
        result1 = await task1
        result2 = await task2

        print(f"\n--- Results ---")
        print(f"Session 1: ", end="")
        if result1[2]:
            print(f"Interrupted/Exception: {result1[2]}")
        else:
            print("Completed normally")

        print(f"Session 2: ", end="")
        if result2[2]:
            print(f"Interrupted/Exception: {result2[2]}")
        else:
            print("Completed normally")

        # Demonstrate that session 2 can still be used.
        print("\n--- Session 2 still works ---")
        messages = await session2.prompt("What is 2 + 2?")
        for msg in messages:
            print(msg.text())

        # Also show the interrupt without session_id (global interrupt).
        print("\n--- Global interrupt (no session_id) ---")
        print("Calling interrupt() without session_id sends control-protocol interrupt")
        await client.interrupt()
        print("Global interrupt sent")


if __name__ == "__main__":
    asyncio.run(main())
