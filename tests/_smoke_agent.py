"""Minimal hermetic ACP agent for the step-1 port smoke test.

Speaks JSON-RPC 2.0 (newline-delimited) over stdio. Handles initialize,
session/new, session/prompt (streams one agent text chunk), and ignores
session/cancel. Used by tests/test_smoke_port.py to exercise the ported
agent-client-protocol v0.14 client end-to-end without a real agent binary.
"""
from __future__ import annotations

import json
import sys

try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": 1,
                    "agentCapabilities": {"loadSession": True},
                    "agentInfo": {
                        "name": "smoke-agent",
                        "version": "0.0.1",
                        "title": "Smoke Agent",
                    },
                },
            })
        elif method == "session/new":
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"sessionId": "smoke-session-1"},
            })
        elif method == "session/prompt":
            session_id = params.get("sessionId", "smoke-session-1")
            send({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "Hello from smoke agent"},
                    },
                },
            })
            send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"stopReason": "end_turn"},
            })
        elif method == "session/cancel":
            pass
        else:
            if req_id is not None:
                send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "method not found"},
                })


if __name__ == "__main__":
    main()
