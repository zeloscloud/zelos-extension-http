"""Trace naming: the one layout rule, and the grammar for names a user types."""

from __future__ import annotations

import zelos_sdk

#: Default `advanced.prefix`: the manifest name.
DEFAULT_PREFIX = "HTTP"

#: Source the extension's logs take when the prefix is cleared; with a prefix
#: they land in it as `<prefix>/log`. Both names are reserved as server names.
LOG_SOURCE_NAME = "http_log"
RESERVED_NAMES = ("log", LOG_SOURCE_NAME)

#: Per-server health event. Sanitized names never start with `_`, so no path,
#: JSON key or map event can take it.
HEALTH_EVENT = "_server"


def trace_layout(prefix: str, server: str) -> tuple[str, str]:
    """The one trace-naming rule: (source name, event prefix) for a server.

    With a prefix, one shared source carries every server and a server's events
    read `<server>/<event>`. Cleared, each server owns its source and the events
    are unprefixed.
    """
    if prefix:
        return prefix, f"{server}/"
    return server, ""


def name_error(value: str, label: str, reserved: tuple[str, ...] = ()) -> str | None:
    """Why user-typed `value` is not a legal trace name, or None if it is.

    Names become trace path segments, so a separator (`/ . @ :`) would silently
    re-nest the tree. Anything the SDK sanitizer rewrites is rejected, not renamed.
    """
    if not value:
        return None
    if value in reserved:
        return f"Invalid {label} {value!r}: reserved for the extension's own logs."
    clean = zelos_sdk.sanitize_name(value, kind="source")
    if clean == value:
        return None
    offender = next((c for c, ok in zip(value, clean, strict=False) if c != ok), value[-1])
    return (
        f"Invalid {label} {value!r}: {offender!r} is not allowed. "
        "Use letters, digits, space, '_' or '-'."
    )
