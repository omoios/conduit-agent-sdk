# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""13 — OpenCode Direct: End-to-end ACP communication with OpenCode.

Bypasses the Rust stubs (which have TODOs for JSON-RPC wiring) and
talks the ACP protocol directly over subprocess stdio. This validates
the full pipeline: registry resolution → subprocess spawn → ACP
initialize → session/new → session/prompt → stream response.

    uv run python examples/13_opencode_direct.py
"""

from __future__ import annotations

import asyncio
import json
import os

from conduit_sdk import Registry

_ID = 0


def _next_id() -> int:
    global _ID
    _ID += 1
    return _ID


async def send_request(
    proc: asyncio.subprocess.Process,
    method: str,
    params: dict,
) -> dict:
    """Send a JSON-RPC request and read the response (skipping notifications)."""
    rid = _next_id()
    msg = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    proc.stdin.write((msg + "\n").encode())
    await proc.stdin.drain()

    while True:
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError("Agent closed stdout unexpectedly")
        data = json.loads(line)
        if data.get("id") == rid:
            if "error" in data:
                raise RuntimeError(f"RPC error: {data['error']}")
            return data


async def send_prompt_and_stream(
    proc: asyncio.subprocess.Process,
    session_id: str,
    text: str,
) -> None:
    """Send session/prompt and stream response until the result arrives."""
    rid = _next_id()
    msg = json.dumps({
        "jsonrpc": "2.0",
        "id": rid,
        "method": "session/prompt",
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        },
    })
    proc.stdin.write((msg + "\n").encode())
    await proc.stdin.drain()

    got_message = False

    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
        if not line:
            break

        data = json.loads(line)

        # Streaming notification.
        if data.get("method") == "session/update":
            update = data["params"]["update"]
            utype = update.get("sessionUpdate", "")
            content = update.get("content", {})

            if utype == "agent_message_chunk" and isinstance(content, dict):
                txt = content.get("text", "")
                if txt:
                    got_message = True
                    print(txt, end="", flush=True)

            elif utype == "agent_thought_chunk" and isinstance(content, dict):
                # Some agents (e.g. OpenCode with extended thinking) send
                # all output as thought chunks.
                txt = content.get("text", "")
                if txt and not got_message:
                    print(txt, end="", flush=True)

        # Final result.
        elif data.get("id") == rid:
            result = data.get("result", {})
            usage = result.get("usage", {})
            print(f"\n\n[stop_reason={result.get('stopReason', '?')}, "
                  f"tokens={usage.get('totalTokens', '?')}]")
            return


async def main():
    # Step 1: Resolve opencode from the registry.
    registry = Registry()
    await registry.fetch()
    cmd, env = await registry.resolve_command("opencode")
    print(f"Resolved: {' '.join(cmd)}")

    # Step 2: Spawn the subprocess.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=10 * 1024 * 1024,  # 10 MB buffer for large session payloads
    )
    print("Spawned agent process")

    try:
        # Step 3: ACP initialize handshake.
        init_resp = await send_request(proc, "initialize", {
            "protocolVersion": 1,
            "capabilities": {},
            "clientInfo": {"name": "conduit-sdk", "version": "0.1.0"},
        })
        agent_info = init_resp["result"].get("agentInfo", {})
        caps = init_resp["result"].get("agentCapabilities", {})
        print(f"Connected to {agent_info.get('name')} v{agent_info.get('version')}")
        print(f"  sessions={bool(caps.get('loadSession'))}, "
              f"mcp={bool(caps.get('mcpCapabilities'))}, "
              f"images={caps.get('promptCapabilities', {}).get('image', False)}")

        # Step 4: Create a new session.
        session_resp = await send_request(proc, "session/new", {
            "cwd": os.getcwd(),
            "mcpServers": [],
        })
        session_id = session_resp["result"]["sessionId"]
        print(f"Session: {session_id}\n")

        # Step 5: Send a prompt and stream the response.
        prompt_text = "List the Python files in this project. Just the filenames."
        print(f">>> {prompt_text}\n")
        await send_prompt_and_stream(proc, session_id, prompt_text)

    finally:
        proc.stdin.close()
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError):
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
