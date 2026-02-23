"""Tests for conduit_sdk.activate (query function)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from conduit_sdk.activate import query


SAMPLE_REGISTRY = {
    "version": "1.0.0",
    "agents": [
        {
            "id": "test-agent",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "A test agent",
            "distribution": {
                "npx": {
                    "package": "@test/agent@1.0.0",
                }
            },
        },
    ],
    "extensions": [],
}


def _mock_urlopen():
    """Return a mock for urllib.request.urlopen that serves SAMPLE_REGISTRY."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(SAMPLE_REGISTRY).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_resolves_and_creates_client(self, tmp_path):
        """Verify that query() does registry fetch + client creation."""
        mock_message = MagicMock()
        mock_message.text.return_value = "Hello from agent!"

        with (
            patch("conduit_sdk.registry.urllib.request.urlopen", return_value=_mock_urlopen()),
            patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/npx"),
            patch("conduit_sdk.registry._default_cache_dir", return_value=tmp_path),
            patch("conduit_sdk.client.Client.__init__", return_value=None) as mock_init,
            patch("conduit_sdk.client.Client.__aenter__") as mock_enter,
            patch("conduit_sdk.client.Client.__aexit__") as mock_exit,
            patch("conduit_sdk.client.Client.prompt") as mock_prompt,
        ):
            mock_enter.return_value = MagicMock()
            mock_exit.return_value = False

            async def _prompt(text):
                yield mock_message

            mock_prompt.side_effect = _prompt

            messages = []
            async for msg in query(prompt="Hello!", agent="test-agent"):
                messages.append(msg)

            assert len(messages) == 1
            assert messages[0].text() == "Hello from agent!"

            # Verify Client was constructed with the resolved command.
            mock_init.assert_called_once()
            call_args = mock_init.call_args
            assert call_args[0][0] == ["/usr/local/bin/npx", "@test/agent@1.0.0"]

    @pytest.mark.asyncio
    async def test_query_custom_registry_url(self, tmp_path):
        """Verify custom registry_url is passed through."""
        with (
            patch("conduit_sdk.registry.urllib.request.urlopen", return_value=_mock_urlopen()) as mock_urlopen,
            patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/npx"),
            patch("conduit_sdk.registry._default_cache_dir", return_value=tmp_path),
            patch("conduit_sdk.client.Client.__init__", return_value=None),
            patch("conduit_sdk.client.Client.__aenter__") as mock_enter,
            patch("conduit_sdk.client.Client.__aexit__", return_value=False),
            patch("conduit_sdk.client.Client.prompt") as mock_prompt,
        ):
            mock_enter.return_value = MagicMock()

            async def _prompt(text):
                return
                yield  # make it a generator

            mock_prompt.side_effect = _prompt

            async for _ in query(
                prompt="Hi",
                agent="test-agent",
                registry_url="https://custom.example.com/registry.json",
            ):
                pass

            # Verify the custom URL was used in the HTTP request.
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args
            assert "custom.example.com" in call_args[0][0].full_url

    @pytest.mark.asyncio
    async def test_query_passes_timeout(self, tmp_path):
        """Verify timeout is forwarded to Client."""
        with (
            patch("conduit_sdk.registry.urllib.request.urlopen", return_value=_mock_urlopen()),
            patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/npx"),
            patch("conduit_sdk.registry._default_cache_dir", return_value=tmp_path),
            patch("conduit_sdk.client.Client.__init__", return_value=None) as mock_init,
            patch("conduit_sdk.client.Client.__aenter__") as mock_enter,
            patch("conduit_sdk.client.Client.__aexit__", return_value=False),
            patch("conduit_sdk.client.Client.prompt") as mock_prompt,
        ):
            mock_enter.return_value = MagicMock()

            async def _prompt(text):
                return
                yield

            mock_prompt.side_effect = _prompt

            async for _ in query(
                prompt="Hi",
                agent="test-agent",
                timeout=120,
            ):
                pass

            call_kwargs = mock_init.call_args[1]
            assert call_kwargs["timeout"] == 120
