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
