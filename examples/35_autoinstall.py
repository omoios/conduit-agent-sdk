"""Autoinstall demo: plan-then-verify-by-execution over a repo.

Stage 1: omp explores the repo and writes a setup manifest.
Stage 2: a fresh omp sets it up; the orchestrator verifies by re-running the
target commands (retry up to 3x).

Usage:
  uv run python examples/35_autoinstall.py <path-to-repo>
  uv run python examples/35_autoinstall.py            # defaults to this SDK's own repo
"""

from __future__ import annotations

import asyncio
import os
import sys

from conduit_sdk.autoinstall import autoinstall

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _print_event(ev, *, stage, attempt):
    t = ev.type
    p = ev.payload or {}
    tag = f"[s{stage}.{attempt}]"
    if t in ("file.read", "file.write_requested", "file.edited"):
        # Show the file path (fallback to tool title / input snippet).
        where = p.get("path") or p.get("toolName") or ""
        if not where and isinstance(p.get("inputPreview"), dict):
            where = str(p["inputPreview"])[:60]
        print(f"    {tag} {t} {where}")
    elif t in ("tool.started", "tool.completed"):
        name = p.get("toolName") or ""
        print(f"    {tag} {t} {name}")
    elif t == "command.started":
        print(f"    {tag} command {p.get('command','')}")
    elif t == "agent.message.completed":
        print(f"    {tag} msg: {p.get('text','')[:90]!r}")
    elif t == "run.completed":
        print(f"    {tag} {t}")


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else REPO_ROOT
    print(f"Autoinstalling: {target}")
    print("(Stage 1: omp plans → setup-manifest.json; Stage 2: omp executes → verify)\n")

    res = await autoinstall(
        target, timeout=900, max_attempts=3, on_event=_print_event, keep_workdir=False,
    )

    print("\n" + "=" * 60)
    print(f"verified : {res.verified}")
    print(f"attempts : {res.attempts}")
    if res.error:
        print(f"error    : {res.error}")
    if res.manifest:
        tgts = res.manifest.get("targetCommands", [])
        print(f"targets  : {[t.get('command') for t in tgts]}")
        print(f"outcome  : {res.manifest.get('expectedOutcome','')[:120]}")
    print("\nverify:")
    for v in res.verify:
        mark = "PASS" if v.passed else "FAIL"
        print(f"  [{mark}] {v.command}  (exit {v.exit_code})")
        if not v.passed and v.stdout:
            print(f"         tail: {v.stdout[-200:]!r}")
    return 0 if res.verified else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
