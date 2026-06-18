"""End-to-end integration test: conduit_sdk.Client <-> real ``omp acp`` agent.

Opt-in only — skipped unless ``CONDUIT_E2E_OMP=1`` is set AND ``omp`` is on
PATH, so it never runs in the default suite. Exercises the full client path
(spawn -> initialize -> new session -> prompt -> stream -> result) against a
live ACP server.

    CONDUIT_E2E_OMP=1 uv run pytest tests/test_omp_acp_e2e.py -q
"""

from __future__ import annotations

import os
import shutil

import pytest

from conduit_sdk import Client

_OMP = shutil.which("omp")
_ENABLED = os.environ.get("CONDUIT_E2E_OMP") == "1"

pytestmark = pytest.mark.skipif(
    not (_OMP and _ENABLED),
    reason="set CONDUIT_E2E_OMP=1 with `omp` on PATH to run the omp acp e2e test",
)


@pytest.mark.asyncio
async def test_omp_acp_roundtrip():
    async with Client([_OMP, "acp"], timeout=60) as client:
        session = await client.new_session()
        assert session.session_id

        out = await client.prompt_sync(
            "Reply with exactly the word PONG and nothing else."
        )
        text = "".join(m.text() for m in out if m.text()).strip().upper()
        assert "PONG" in text, f"expected PONG in agent reply, got: {text!r}"

@pytest.mark.asyncio
async def test_omp_acp_agent_info():
    """agent_info returns a dict with a 'name' key."""
    async with Client([_OMP, "acp"], timeout=60) as client:
        info = await client.agent_info
        assert info is not None, "agent_info returned None"
        assert isinstance(info, dict), f"expected dict, got {type(info).__name__}"
        assert "name" in info, f"'name' key missing from agent_info: {info}"


@pytest.mark.asyncio
async def test_omp_acp_multi_turn():
    """Text-only multi-turn: prompts within one session preserve context."""
    async with Client([_OMP, "acp"], timeout=60) as client:
        session = await client.new_session()
        assert session.session_id

        out1 = await client.prompt_sync(
            "Remember the word BANANA.",
            session_id=session.session_id,
        )
        # First turn should succeed (no assertion on content — model varies).
        assert out1 is not None

        out2 = await client.prompt_sync(
            "What word did I ask you to remember? Reply with just that word.",
            session_id=session.session_id,
        )
        text2 = "".join(m.text() for m in out2 if m.text())
        assert "banana" in text2.lower(), (
            f"expected 'BANANA' in second-turn reply, got: {text2!r}"
        )


@pytest.mark.asyncio
async def test_omp_acp_two_session_isolation():
    """Each new_session() yields a distinct session ID."""
    async with Client([_OMP, "acp"], timeout=60) as client:
        s1 = await client.new_session()
        s2 = await client.new_session()
        assert s1.session_id is not None
        assert s2.session_id is not None
        assert s1.session_id != s2.session_id, (
            f"expected distinct session ids, got identical: {s1.session_id!r}"
        )
