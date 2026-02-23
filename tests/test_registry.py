"""Tests for conduit_sdk.registry."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from conduit_sdk.exceptions import (
    AgentNotFoundError,
    DistributionError,
    RegistryError,
)
from conduit_sdk.registry import (
    AgentInfo,
    Registry,
    detect_platform,
    find_runtime,
)

# ---------------------------------------------------------------------------
# Sample registry data
# ---------------------------------------------------------------------------

SAMPLE_REGISTRY = {
    "version": "1.0.0",
    "agents": [
        {
            "id": "claude-acp",
            "name": "Claude Agent",
            "version": "0.18.0",
            "description": "ACP wrapper for Anthropic's Claude",
            "repository": "https://github.com/zed-industries/claude-agent-acp",
            "authors": ["Anthropic"],
            "license": "proprietary",
            "icon": "https://cdn.example.com/claude-acp.svg",
            "distribution": {
                "npx": {
                    "package": "@zed-industries/claude-agent-acp@0.18.0",
                }
            },
        },
        {
            "id": "codex-acp",
            "name": "Codex CLI",
            "version": "0.9.4",
            "description": "ACP adapter for OpenAI's coding assistant",
            "repository": "https://github.com/zed-industries/codex-acp",
            "authors": ["OpenAI", "Zed Industries"],
            "license": "Apache-2.0",
            "distribution": {
                "binary": {
                    "darwin-aarch64": {
                        "archive": "https://example.com/codex-darwin-aarch64.tar.gz",
                        "cmd": "./codex-acp",
                    },
                    "linux-x86_64": {
                        "archive": "https://example.com/codex-linux-x86_64.tar.gz",
                        "cmd": "./codex-acp",
                    },
                },
                "npx": {
                    "package": "@zed-industries/codex-acp@0.9.4",
                },
            },
        },
        {
            "id": "auggie",
            "name": "Auggie CLI",
            "version": "0.16.2",
            "description": "Augment Code's powerful software agent",
            "distribution": {
                "npx": {
                    "package": "@augmentcode/auggie@0.16.2",
                    "args": ["--acp"],
                    "env": {"AUGMENT_DISABLE_AUTO_UPDATE": "1"},
                }
            },
        },
        {
            "id": "goose-acp",
            "name": "Goose Agent",
            "version": "1.0.0",
            "description": "ACP adapter for Block's Goose",
            "distribution": {
                "uvx": {
                    "package": "goose-acp",
                    "args": ["serve"],
                },
            },
        },
    ],
    "extensions": [],
}


def _make_registry(tmp_path) -> Registry:
    """Create a registry pre-loaded with sample data (no network)."""
    reg = Registry(cache_dir=tmp_path)
    reg._load(SAMPLE_REGISTRY)
    return reg


# ---------------------------------------------------------------------------
# AgentInfo
# ---------------------------------------------------------------------------


class TestAgentInfo:
    def test_from_dict_full(self):
        data = SAMPLE_REGISTRY["agents"][0]
        info = AgentInfo.from_dict(data)
        assert info.id == "claude-acp"
        assert info.name == "Claude Agent"
        assert info.version == "0.18.0"
        assert info.description == "ACP wrapper for Anthropic's Claude"
        assert info.authors == ["Anthropic"]
        assert "npx" in info.distribution

    def test_from_dict_minimal(self):
        data = {
            "id": "test",
            "name": "Test Agent",
            "version": "1.0.0",
        }
        info = AgentInfo.from_dict(data)
        assert info.id == "test"
        assert info.description == ""
        assert info.distribution == {}

    def test_frozen(self):
        info = AgentInfo.from_dict(SAMPLE_REGISTRY["agents"][0])
        with pytest.raises(AttributeError):
            info.id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    @patch("conduit_sdk.registry.platform.system", return_value="Darwin")
    @patch("conduit_sdk.registry.platform.machine", return_value="arm64")
    def test_darwin_aarch64(self, _machine, _system):
        assert detect_platform() == "darwin-aarch64"

    @patch("conduit_sdk.registry.platform.system", return_value="Darwin")
    @patch("conduit_sdk.registry.platform.machine", return_value="x86_64")
    def test_darwin_x86_64(self, _machine, _system):
        assert detect_platform() == "darwin-x86_64"

    @patch("conduit_sdk.registry.platform.system", return_value="Linux")
    @patch("conduit_sdk.registry.platform.machine", return_value="x86_64")
    def test_linux_x86_64(self, _machine, _system):
        assert detect_platform() == "linux-x86_64"

    @patch("conduit_sdk.registry.platform.system", return_value="Linux")
    @patch("conduit_sdk.registry.platform.machine", return_value="aarch64")
    def test_linux_aarch64(self, _machine, _system):
        assert detect_platform() == "linux-aarch64"

    @patch("conduit_sdk.registry.platform.system", return_value="Windows")
    @patch("conduit_sdk.registry.platform.machine", return_value="AMD64")
    def test_windows_x86_64(self, _machine, _system):
        assert detect_platform() == "windows-x86_64"

    @patch("conduit_sdk.registry.platform.system", return_value="FreeBSD")
    @patch("conduit_sdk.registry.platform.machine", return_value="riscv64")
    def test_unknown_platform(self, _machine, _system):
        assert detect_platform() == "freebsd-riscv64"


# ---------------------------------------------------------------------------
# find_runtime
# ---------------------------------------------------------------------------


class TestFindRuntime:
    def test_existing_runtime(self):
        # python3 should exist in test environments
        result = find_runtime("python3")
        assert result is not None or find_runtime("python") is not None

    def test_missing_runtime(self):
        assert find_runtime("__nonexistent_binary_xyz__") is None


# ---------------------------------------------------------------------------
# Registry — fetch & cache
# ---------------------------------------------------------------------------


class TestRegistryFetch:
    @pytest.mark.asyncio
    async def test_fetch_from_network(self, tmp_path):
        registry = Registry(cache_dir=tmp_path, cache_ttl=3600)

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(SAMPLE_REGISTRY).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("conduit_sdk.registry.urllib.request.urlopen", return_value=mock_response):
            await registry.fetch()

        agents = await registry.list_agents()
        assert len(agents) == 4
        assert registry.cache_path.exists()

    @pytest.mark.asyncio
    async def test_fetch_uses_fresh_cache(self, tmp_path):
        # Pre-populate cache.
        cache_file = tmp_path / "registry.json"
        cache_file.write_text(json.dumps(SAMPLE_REGISTRY))

        registry = Registry(cache_dir=tmp_path, cache_ttl=3600)

        # Should NOT hit the network.
        with patch("conduit_sdk.registry.urllib.request.urlopen") as mock_urlopen:
            await registry.fetch()
            mock_urlopen.assert_not_called()

        agents = await registry.list_agents()
        assert len(agents) == 4

    @pytest.mark.asyncio
    async def test_fetch_ignores_stale_cache(self, tmp_path):
        # Pre-populate cache with old mtime.
        cache_file = tmp_path / "registry.json"
        cache_file.write_text(json.dumps(SAMPLE_REGISTRY))
        old_time = time.time() - 7200  # 2 hours ago
        os.utime(cache_file, (old_time, old_time))

        registry = Registry(cache_dir=tmp_path, cache_ttl=3600)

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(SAMPLE_REGISTRY).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("conduit_sdk.registry.urllib.request.urlopen", return_value=mock_response):
            await registry.fetch()

        agents = await registry.list_agents()
        assert len(agents) == 4

    @pytest.mark.asyncio
    async def test_fetch_fallback_to_stale_cache(self, tmp_path):
        # Pre-populate stale cache.
        cache_file = tmp_path / "registry.json"
        cache_file.write_text(json.dumps(SAMPLE_REGISTRY))
        old_time = time.time() - 7200
        os.utime(cache_file, (old_time, old_time))

        registry = Registry(cache_dir=tmp_path, cache_ttl=3600)

        with patch("conduit_sdk.registry.urllib.request.urlopen", side_effect=OSError("Network down")):
            await registry.fetch()

        # Should have fallen back to stale cache.
        agents = await registry.list_agents()
        assert len(agents) == 4

    @pytest.mark.asyncio
    async def test_fetch_fails_no_cache(self, tmp_path):
        registry = Registry(cache_dir=tmp_path, cache_ttl=3600)

        with patch("conduit_sdk.registry.urllib.request.urlopen", side_effect=OSError("Network down")):
            with pytest.raises(RegistryError, match="no cache available"):
                await registry.fetch()


# ---------------------------------------------------------------------------
# Registry — query
# ---------------------------------------------------------------------------


class TestRegistryQuery:
    @pytest.mark.asyncio
    async def test_list_agents(self, tmp_path):
        registry = _make_registry(tmp_path)
        agents = await registry.list_agents()
        assert len(agents) == 4
        ids = {a.id for a in agents}
        assert "claude-acp" in ids
        assert "codex-acp" in ids

    @pytest.mark.asyncio
    async def test_get_agent(self, tmp_path):
        registry = _make_registry(tmp_path)
        agent = await registry.get_agent("claude-acp")
        assert agent.name == "Claude Agent"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(AgentNotFoundError, match="nonexistent"):
            await registry.get_agent("nonexistent")

    def test_search(self, tmp_path):
        registry = _make_registry(tmp_path)
        results = registry.search("claude")
        assert len(results) == 1
        assert results[0].id == "claude-acp"

    def test_search_case_insensitive(self, tmp_path):
        registry = _make_registry(tmp_path)
        results = registry.search("CODEX")
        assert len(results) == 1
        assert results[0].id == "codex-acp"

    def test_search_description(self, tmp_path):
        registry = _make_registry(tmp_path)
        results = registry.search("OpenAI")
        assert len(results) == 1

    def test_search_no_match(self, tmp_path):
        registry = _make_registry(tmp_path)
        results = registry.search("zzz_nonexistent_zzz")
        assert results == []

    @pytest.mark.asyncio
    async def test_not_fetched_raises(self, tmp_path):
        reg = Registry(cache_dir=tmp_path)
        with pytest.raises(RegistryError, match="not loaded"):
            await reg.list_agents()


# ---------------------------------------------------------------------------
# Registry — resolve_command
# ---------------------------------------------------------------------------


class TestRegistryResolve:
    @pytest.mark.asyncio
    async def test_resolve_npx(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/npx"):
            cmd, env = await registry.resolve_command("claude-acp")

        assert cmd == ["/usr/local/bin/npx", "@zed-industries/claude-agent-acp@0.18.0"]
        assert env == {}

    @pytest.mark.asyncio
    async def test_resolve_npx_with_args_and_env(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/npx"):
            cmd, env = await registry.resolve_command("auggie")

        assert cmd == ["/usr/local/bin/npx", "@augmentcode/auggie@0.16.2", "--acp"]
        assert env == {"AUGMENT_DISABLE_AUTO_UPDATE": "1"}

    @pytest.mark.asyncio
    async def test_resolve_uvx(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/uvx"):
            cmd, env = await registry.resolve_command("goose-acp")

        assert cmd == ["/usr/local/bin/uvx", "goose-acp", "serve"]
        assert env == {}

    @pytest.mark.asyncio
    async def test_resolve_prefer_binary(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch(
            "conduit_sdk.registry.detect_platform", return_value="darwin-aarch64"
        ):
            cmd, env = await registry.resolve_command("codex-acp", prefer="binary")

        assert cmd == ["./codex-acp"]
        assert env == {}

    @pytest.mark.asyncio
    async def test_resolve_binary_wrong_platform(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch(
            "conduit_sdk.registry.detect_platform", return_value="windows-aarch64"
        ), patch("conduit_sdk.registry.find_runtime", return_value="/usr/local/bin/npx"):
            # Should fall through to npx since binary isn't available for windows-aarch64
            cmd, env = await registry.resolve_command("codex-acp", prefer="binary")

        # Falls back to npx
        assert cmd[0] == "/usr/local/bin/npx"

    @pytest.mark.asyncio
    async def test_resolve_npx_not_on_path(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch("conduit_sdk.registry.find_runtime", return_value=None):
            # When the only distribution (npx) fails because the runtime is
            # missing, all types are exhausted and DistributionError is raised.
            with pytest.raises(DistributionError, match="No compatible distribution"):
                await registry.resolve_command("claude-acp")

    @pytest.mark.asyncio
    async def test_resolve_agent_not_found(self, tmp_path):
        registry = _make_registry(tmp_path)
        with pytest.raises(AgentNotFoundError):
            await registry.resolve_command("nonexistent")

    @pytest.mark.asyncio
    async def test_resolve_no_distribution(self, tmp_path):
        registry = _make_registry(tmp_path)
        # Add an agent with empty distribution.
        registry._agents["empty"] = AgentInfo(
            id="empty",
            name="Empty Agent",
            version="1.0.0",
            description="No distribution",
            distribution={},
        )
        with pytest.raises(DistributionError, match="no distribution"):
            await registry.resolve_command("empty")
