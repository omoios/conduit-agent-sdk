"""In-process redaction proxy stage for conduit-agent-sdk.

Provides an async event filter that scrubs known secret patterns from
:class:`AgentEvent` payload and summary fields, then propagates the
redaction status downstream.

Example::

    from conduit_sdk.redaction import redact_patterns, redact_events

    f = redact_patterns(r"sk-[A-Za-z0-9_\\-]{20,}")
    async for event in redact_events(run.events(), f):
        print(event.redaction_status)  # "redacted" if secrets found
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator

from conduit_sdk.runlayer import AgentEvent


__all__ = [
    "DEFAULT_SECRET_PATTERNS",
    "RedactionFilter",
    "redact_events",
    "redact_patterns",
    "redact_record",
]

# ---------------------------------------------------------------------------
# Default secret patterns (best-effort, not exhaustive)
# ---------------------------------------------------------------------------

DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    # OpenAI / Anthropic / generic API keys
    r"sk-[A-Za-z0-9_\-]{20,}",
    # GitHub tokens (ghp_ personal, gho_ OAuth, ghu_ user, ghs_ server, ghr_ refresh)
    r"gh[pousr]_[A-Za-z0-9]{36,}",
    # AWS access key ID
    r"AKIA[0-9A-Z]{16}",
    # PEM-encoded private keys (multi-line)
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----",
    # Generic "Bearer" tokens in Authorization headers / values
    r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}",
)


# ---------------------------------------------------------------------------
# Redaction filter
# ---------------------------------------------------------------------------


@dataclass
class RedactionFilter:
    """Compiled set of regex patterns used to scrub secret values.

    Build via :func:`redact_patterns`.
    """

    patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    replacement: str = "[REDACTED]"


def redact_patterns(
    *patterns: str,
    replacement: str = "[REDACTED]",
) -> RedactionFilter:
    """Compile one or more regex *patterns* into a :class:`RedactionFilter`.

    Each pattern is compiled with :const:`re.IGNORECASE` by default for
    broad matching. Pass already-compiled flags inside the regex string
    (e.g. ``(?-i)`` prefix) to opt out.
    """
    compiled = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
    return RedactionFilter(patterns=compiled, replacement=replacement)


# ---------------------------------------------------------------------------
# Deep-scrub helpers
# ---------------------------------------------------------------------------


def _deep_scrub_str(value: str, filter: RedactionFilter) -> tuple[str, bool]:
    """Apply every compiled pattern to a string value."""
    result = value
    for pat in filter.patterns:
        result = pat.sub(filter.replacement, result)
    return result, result != value


def _deep_scrub(
    value: Any,
    filter: RedactionFilter,
) -> tuple[Any, bool]:
    """Recursively walk a nested dict/list/str structure and scrub secrets.

    Returns ``(scrubbed_value, was_modified)``.  Scalar types that are
    not ``str``, ``dict``, or ``list`` are returned as-is with
    ``modified=False``.
    """
    if isinstance(value, str):
        return _deep_scrub_str(value, filter)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        modified = False
        for k, v in value.items():
            sv, sm = _deep_scrub(v, filter)
            result[k] = sv
            modified = modified or sm
        return result if modified else value, modified
    if isinstance(value, list):
        result: list[Any] = []
        modified = False
        for item in value:
            si, sm = _deep_scrub(item, filter)
            result.append(si)
            modified = modified or sm
        return result if modified else value, modified
    # int, float, bool, None, etc. — never modified
    return value, False

# ---------------------------------------------------------------------------
# Record scrub (persistence path)
# ---------------------------------------------------------------------------


def redact_record(record: dict[str, Any], filter: RedactionFilter) -> dict[str, Any]:
    """Scrub secrets from a canonical ``to_record`` dict before persistence.

    Walks the record with :func:`_deep_scrub` (dicts/lists/strings) and returns
    the scrubbed dict. The input is never mutated; if nothing changed the same
    object is returned.
    """
    scrubbed, _ = _deep_scrub(record, filter)
    return scrubbed


# ---------------------------------------------------------------------------
# Async event filter
# ---------------------------------------------------------------------------


async def redact_events(
    events: AsyncIterator[AgentEvent],
    filter: RedactionFilter,
) -> AsyncIterator[AgentEvent]:
    """Async generator that scrubs secrets from an *events* stream.

    For each incoming event:
    -   Recursively applies every compiled pattern to ``event.payload``
        and ``event.summary``.
    -   If any substitution occurred, yields a **new** :class:`AgentEvent`
        with ``redaction_status="redacted"``.  Otherwise yields a new event
        with the original redaction_status preserved.
    -   The original input event is never mutated.

    Args:
        events: Source stream of agent events.
        filter: The compiled redaction rules.

    Yields:
        Scubbed (or unchanged) :class:`AgentEvent` instances.
    """
    async for event in events:
        payload_modified = False
        payload_value: dict[str, Any] | None = None
        if event.payload is not None:
            payload_value, payload_modified = _deep_scrub(event.payload, filter)

        summary_modified = False
        summary_value: str | None = None
        if event.summary is not None:
            summary_value, summary_modified = _deep_scrub(event.summary, filter)

        if payload_modified or summary_modified:
            yield replace(
                event,
                redaction_status="redacted",
                payload=payload_value,
                summary=summary_value,
            )
        else:
            yield replace(
                event,
                redaction_status=event.redaction_status,
                payload=payload_value if event.payload is not None else None,
                summary=summary_value if event.summary is not None else None,
            )
