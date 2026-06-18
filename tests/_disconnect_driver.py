"""Connect to the pipe-holder agent, prompt, and disconnect, then exit.

Run as a subprocess by test_disconnect_teardown. If teardown hangs (a descendant
holds the ACP pipe open and is not killed), this process never exits.
"""

import asyncio
import os
import sys

from conduit_sdk import Client

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = [sys.executable, os.path.join(HERE, "_pipe_holder_agent_app.py")]


async def main() -> None:
    async with Client(AGENT, timeout=15) as client:
        await client.new_session()
        await client.prompt_sync("hi")
    # Reaching here means connect + prompt + disconnect all returned.
    print("clean exit", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
