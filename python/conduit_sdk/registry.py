"""ACP agent registry client.

Fetches and caches the agent registry from ``cdn.agentclientprotocol.com``,
resolves agent IDs to runnable shell commands, and detects available runtimes.

Usage::

    from conduit_sdk.registry import Registry

    registry = Registry()
    await registry.fetch()
    agents = await registry.list_agents()

    cmd, env = await registry.resolve_command("claude-acp")
    # cmd = ["npx", "@zed-industries/claude-agent-acp@0.18.0"]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conduit_sdk.exceptions import (
    AgentNotFoundError,
    DistributionError,
    RegistryError,
    RuntimeNotFoundError,
)

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY_URL = (
    "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
)


def _default_cache_dir() -> Path:
    """Return platform-appropriate cache directory."""
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg) / "conduit-sdk"
    return Path.home() / ".cache" / "conduit-sdk"


def detect_platform() -> str:
    """Detect the current platform in registry format.

    Returns a string like ``"darwin-aarch64"`` or ``"linux-x86_64"``.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalise OS name.
    os_name = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(system)
    if os_name is None:
        return f"{system}-{machine}"

    # Normalise architecture.
    arch_map: dict[str, str] = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    arch = arch_map.get(machine, machine)

    return f"{os_name}-{arch}"


def find_runtime(name: str) -> str | None:
    """Find a runtime executable on ``PATH``.

    Returns the absolute path if found, otherwise ``None``.
    """
    return shutil.which(name)


@dataclass(frozen=True)
class AgentInfo:
    """Metadata for a single agent in the registry."""

    id: str
    name: str
    version: str
    description: str
    repository: str = ""
    authors: list[str] = field(default_factory=list)
    license: str = ""
    icon: str = ""
    distribution: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentInfo:
        """Construct from a registry JSON entry."""
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            description=data.get("description", ""),
            repository=data.get("repository", ""),
            authors=data.get("authors", []),
            license=data.get("license", ""),
            icon=data.get("icon", ""),
            distribution=data.get("distribution", {}),
        )


class Registry:
    """Client for the ACP agent registry.

    Parameters
    ----------
    registry_url:
        URL of the registry JSON file.
    cache_dir:
        Local directory for caching the registry.
    cache_ttl:
        Time-to-live in seconds for the cached registry file.
    """

    def __init__(
        self,
        *,
        registry_url: str = _DEFAULT_REGISTRY_URL,
        cache_dir: Path | str | None = None,
        cache_ttl: int = 3600,
    ) -> None:
        self._url = registry_url
        self._cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self._cache_ttl = cache_ttl
        self._agents: dict[str, AgentInfo] = {}
        self._raw: dict[str, Any] = {}
        self._fetched = False

    # -- Fetching & caching --------------------------------------------------

    @property
    def cache_path(self) -> Path:
        return self._cache_dir / "registry.json"

    def _cache_is_fresh(self) -> bool:
        """Check whether the cached file exists and is within TTL."""
        path = self.cache_path
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age < self._cache_ttl

    def _read_cache(self) -> dict[str, Any] | None:
        """Read cached registry JSON, or ``None`` if unavailable."""
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, data: dict[str, Any]) -> None:
        """Write registry JSON to the cache directory."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data))

    async def fetch(self) -> None:
        """Fetch the registry, using cache when fresh.

        On network failure, falls back to a stale cache (with a warning).
        Raises :class:`RegistryError` if no data is available at all.
        """
        if self._cache_is_fresh():
            data = self._read_cache()
            if data is not None:
                self._load(data)
                return

        # Fetch from network in a thread to avoid blocking the event loop.
        loop = asyncio.get_running_loop()
        try:
            body: bytes = await loop.run_in_executor(None, self._http_get, self._url)
            data = json.loads(body)
            self._write_cache(data)
            self._load(data)
        except Exception as exc:
            # Fall back to stale cache.
            stale = self._read_cache()
            if stale is not None:
                logger.warning(
                    "Registry fetch failed (%s); using stale cache", exc
                )
                self._load(stale)
            else:
                raise RegistryError(
                    f"Failed to fetch registry and no cache available: {exc}"
                ) from exc

    @staticmethod
    def _http_get(url: str) -> bytes:
        """Blocking HTTP GET — runs inside ``run_in_executor``."""
        req = urllib.request.Request(url, headers={"User-Agent": "conduit-sdk/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def _load(self, data: dict[str, Any]) -> None:
        """Parse raw registry JSON into :class:`AgentInfo` objects."""
        self._raw = data
        self._agents = {}
        for entry in data.get("agents", []):
            try:
                agent = AgentInfo.from_dict(entry)
                self._agents[agent.id] = agent
            except (KeyError, TypeError) as exc:
                logger.debug("Skipping malformed registry entry: %s", exc)
        self._fetched = True

    # -- Query ---------------------------------------------------------------

    def _ensure_fetched(self) -> None:
        if not self._fetched:
            raise RegistryError("Registry not loaded — call await registry.fetch() first")

    async def list_agents(self) -> list[AgentInfo]:
        """Return all agents in the registry."""
        self._ensure_fetched()
        return list(self._agents.values())

    async def get_agent(self, agent_id: str) -> AgentInfo:
        """Look up a single agent by ID.

        Raises :class:`AgentNotFoundError` if the ID is not in the registry.
        """
        self._ensure_fetched()
        if agent_id not in self._agents:
            available = ", ".join(sorted(self._agents.keys()))
            raise AgentNotFoundError(
                f"Agent {agent_id!r} not found in registry. "
                f"Available: {available}"
            )
        return self._agents[agent_id]

    def search(self, keyword: str) -> list[AgentInfo]:
        """Filter agents whose ID, name, or description contains *keyword*.

        Case-insensitive. The registry must already be fetched.
        """
        self._ensure_fetched()
        kw = keyword.lower()
        return [
            a for a in self._agents.values()
            if kw in a.id.lower()
            or kw in a.name.lower()
            or kw in a.description.lower()
        ]

    # -- Resolution ----------------------------------------------------------

    async def resolve_command(
        self,
        agent_id: str,
        *,
        prefer: str | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Resolve an agent ID to a shell command and environment variables.

        Parameters
        ----------
        agent_id:
            Registry agent identifier (e.g. ``"claude-acp"``).
        prefer:
            Preferred distribution type: ``"npx"``, ``"uvx"``, or
            ``"binary"``. Falls through to the next available type
            if the preferred one is not available.

        Returns
        -------
        A tuple of ``(command_list, env_dict)``.

        Raises
        ------
        AgentNotFoundError
            If the agent is not in the registry.
        DistributionError
            If no compatible distribution is found.
        RuntimeNotFoundError
            If the required runtime (npx/uvx) is not on PATH.
        """
        agent = await self.get_agent(agent_id)
        dist = agent.distribution

        if not dist:
            raise DistributionError(
                f"Agent {agent_id!r} has no distribution metadata"
            )

        # Build ordered list of distribution types to try.
        default_order = ["npx", "uvx", "binary"]
        if prefer and prefer in default_order:
            order = [prefer] + [t for t in default_order if t != prefer]
        else:
            order = default_order

        plat = detect_platform()

        for dtype in order:
            if dtype not in dist:
                continue

            try:
                if dtype in ("npx", "uvx"):
                    return self._resolve_package(agent_id, dtype, dist[dtype])

                if dtype == "binary":
                    return self._resolve_binary(agent_id, dist[dtype], plat)
            except (DistributionError, RuntimeNotFoundError):
                # This distribution type didn't work — try the next one.
                continue

        raise DistributionError(
            f"No compatible distribution for agent {agent_id!r} "
            f"(platform={plat}, tried={order})"
        )

    @staticmethod
    def _resolve_package(
        agent_id: str,
        runtime_name: str,
        config: dict[str, Any],
    ) -> tuple[list[str], dict[str, str]]:
        """Resolve an npx/uvx distribution to a command."""
        runtime_path = find_runtime(runtime_name)
        if runtime_path is None:
            raise RuntimeNotFoundError(
                f"Agent {agent_id!r} requires {runtime_name!r} but it is not on PATH"
            )

        package = config.get("package", "")
        if not package:
            raise DistributionError(
                f"Agent {agent_id!r} {runtime_name} distribution missing 'package'"
            )

        cmd = [runtime_path, package]
        args = config.get("args", [])
        if args:
            cmd.extend(args)

        env = dict(config.get("env", {}))
        return cmd, env

    @staticmethod
    def _resolve_binary(
        agent_id: str,
        config: dict[str, Any],
        plat: str,
    ) -> tuple[list[str], dict[str, str]]:
        """Resolve a binary distribution to a command.

        Note: This resolves the *command* but does not download the archive.
        The caller is responsible for ensuring the binary is available locally.
        """
        if plat not in config:
            available = ", ".join(sorted(config.keys()))
            raise DistributionError(
                f"Agent {agent_id!r} has no binary for platform {plat!r}. "
                f"Available: {available}"
            )

        plat_config = config[plat]
        cmd_str = plat_config.get("cmd", "")
        if not cmd_str:
            raise DistributionError(
                f"Agent {agent_id!r} binary for {plat!r} missing 'cmd'"
            )

        cmd = [cmd_str]
        args = plat_config.get("args", [])
        if args:
            cmd.extend(args)

        env = dict(plat_config.get("env", {}))
        return cmd, env
