"""Exception hierarchy for conduit-agent-sdk.

All exceptions inherit from :class:`ConduitError` so callers can
catch broadly or narrowly as needed.
"""

from __future__ import annotations


class ConduitError(Exception):
    """Base exception for all conduit SDK errors."""


class ConnectionError(ConduitError):
    """Failed to connect to or communicate with the agent process."""


class SessionError(ConduitError):
    """Session lifecycle error (create, load, fork, resume)."""


class TransportError(ConduitError):
    """Low-level transport / I/O failure."""


class ProtocolError(ConduitError):
    """ACP protocol violation or unexpected message format."""


class ToolError(ConduitError):
    """Error during tool registration or invocation."""


class HookError(ConduitError):
    """Error in a lifecycle hook callback."""


class ProxyError(ConduitError):
    """Error building or running the proxy chain."""


class TimeoutError(ConduitError):
    """Operation exceeded the configured timeout."""


class PermissionError(ConduitError):
    """A tool use request was denied by the permission policy."""


class CancelledError(ConduitError):
    """Operation was cancelled."""


# -- Registry errors ---------------------------------------------------------


class RegistryError(ConduitError):
    """Base exception for agent registry operations."""


class AgentNotFoundError(RegistryError):
    """The requested agent ID does not exist in the registry."""


class DistributionError(RegistryError):
    """No compatible distribution found for the current platform."""


class RuntimeNotFoundError(RegistryError):
    """A required runtime (npx, uvx) is not available on PATH."""
