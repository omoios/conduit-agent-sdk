"""Deterministic ACP sandbox-snoop agent for the conduit sandbox image.

Runs a real ``conduit_sdk`` ``AgentServer`` (ACP JSON-RPC over stdio) with NO
model, NO credentials, and NO network -- it only inspects a read-only-mounted
codebase (/work) using stdlib. The host SDK drives it via ``acp_agent``, proving
that the real SDK runs deterministically inside the sandbox environment (seed v1
"run the SDK inside the sandbox against a mounted volume": environment gathering +
codebase enumeration and marker verification).
"""

from __future__ import annotations

import json
import os
import platform
import uuid

from conduit_sdk import AgentServer

server = AgentServer(name="sandbox-snoop-agent", version="0.0.1")

_PRUNE_DIRS = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "target",
    "dist",
    ".mypy_cache",
})


def _snoop() -> dict:
    """Gather environment and workspace report using only stdlib."""
    work_dir = "/work"
    work_mounted = os.path.isdir(work_dir)

    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cwd": os.getcwd(),
        "work_dir": work_dir,
        "work_mounted": work_mounted,
    }

    if not work_mounted:
        return {
            "environment": env,
            "workspace": {
                "top_level": [],
                "file_count": 0,
                "total_bytes": 0,
                "markers": {
                    "README.md": False,
                    "pyproject.toml": False,
                    "Cargo.toml": False,
                    "package.json": False,
                    "go.mod": False,
                    "requirements.txt": False,
                },
            },
        }

    # Top-level listing (dirs suffixed with "/")
    try:
        entries = sorted(
            name + "/" if os.path.isdir(os.path.join(work_dir, name)) else name
            for name in os.listdir(work_dir)
            if name not in _PRUNE_DIRS
        )
    except OSError:
        entries = []

    # Recursive walk with in-place pruning
    file_count = 0
    total_bytes = 0
    try:
        for dirpath, dirnames, filenames in os.walk(work_dir):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
            for fn in filenames:
                file_count += 1
                try:
                    total_bytes += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    except OSError:
        pass

    # Marker files at /work root
    markers = {
        "README.md": os.path.isfile(os.path.join(work_dir, "README.md")),
        "pyproject.toml": os.path.isfile(os.path.join(work_dir, "pyproject.toml")),
        "Cargo.toml": os.path.isfile(os.path.join(work_dir, "Cargo.toml")),
        "package.json": os.path.isfile(os.path.join(work_dir, "package.json")),
        "go.mod": os.path.isfile(os.path.join(work_dir, "go.mod")),
        "requirements.txt": os.path.isfile(os.path.join(work_dir, "requirements.txt")),
    }

    return {
        "environment": env,
        "workspace": {
            "top_level": entries,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "markers": markers,
        },
    }


@server.on_new_session
async def new_session(params):
    return uuid.uuid4().hex


@server.on_prompt
async def snoop(ctx, session_id, content):
    report = _snoop()
    await ctx.send_text(
        "SANDBOX SNOOP REPORT\n" + json.dumps(report, indent=2, sort_keys=True)
    )
    return "end_turn"


@server.on_session_delete
async def on_delete(params):
    return {}


if __name__ == "__main__":
    server.run()
