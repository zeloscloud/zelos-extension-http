"""HTTP actions, free functions registered under `<ACTION_PREFIX>/<name>`.

Free functions, not client methods: one surface whether or not a client runs.
`server` may be omitted with one server. Failures raise: the protocol reads its
verdict from an exception, so {"success": False} would report a successful run.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from typing import TYPE_CHECKING, Any

import zelos_sdk

from .client import parse_json
from .endpoint_map import Endpoint, parse_value

if TYPE_CHECKING:
    from zelos_sdk.actions import ActionsRegistry

    from .client import HttpClient, HttpRunner

logger = logging.getLogger(__name__)

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

_runner: HttpRunner | None = None


def set_runner(runner: HttpRunner | None) -> None:
    """Bind the runner whose clients every action in this module operates on."""
    global _runner
    _runner = runner


def _get_runner() -> HttpRunner:
    if _runner is None:
        raise RuntimeError("No HTTP server is configured on this extension")
    return _runner


def _get_client(server: str = "") -> HttpClient:
    """The named server's client; the only one when `server` is empty."""
    clients = _get_runner().clients
    names = ", ".join(clients)
    if not server:
        if len(clients) == 1:
            return next(iter(clients.values()))
        raise ValueError(f"Several servers are configured; set server to one of: {names}")
    # Sanitized like default names, so `192.168.1.10` finds `192_168_1_10`.
    client = clients.get(zelos_sdk.sanitize_name(server.strip(), kind="source"))
    if client is None:
        raise ValueError(f"Unknown server '{server}'. Servers: {names}")
    return client


def _clients_for(server: str) -> list[HttpClient]:
    """The named server, or every server when omitted."""
    return [_get_client(server)] if server else list(_get_runner().clients.values())


def _get_endpoint(client: HttpClient, name: str) -> tuple[str, Endpoint]:
    if not client.endpoint_map:
        raise ValueError(f"No endpoint map on server '{client.name}' - set its Endpoint Map File")
    found = client.endpoint_map.get_by_name(name)
    if found is None:
        raise ValueError(f"Unknown endpoint '{name}'. Use list_endpoints to see the names.")
    return found


def _check_path(path: str) -> str:
    path = path.strip()
    if not path.startswith("/"):
        raise ValueError(f"Path '{path}' must start with '/', e.g. /api/sensors")
    return path


def _run(client: HttpClient, coro: Any) -> Any:
    """Dispatch into the polling loop. The request's own timeout fires first; the margin
    lets its message, not a bare dispatch timeout, reach the caller."""
    return _get_runner().run_coro(coro, client.timeout + 1.0)


def _server_field(func: Any) -> Any:
    return zelos_sdk.action.text(
        "server",
        title="Server",
        required=False,
        default="",
        description="Server name; may be omitted when only one is configured",
    )(func)


# ─── Status and listing ─────────────────────────────────────────────────────


@zelos_sdk.action(
    "Get Status",
    "Reachability, last HTTP status, latency and error count; every server when omitted",
    read_only=True,
    hidden=True,
)
@_server_field
def get_status(server: str = "") -> dict[str, Any]:
    if server:
        return _get_client(server).status()
    statuses = [c.status() for c in _get_runner().clients.values()]
    return {"servers": statuses, "count": len(statuses)}


@zelos_sdk.action(
    "List Endpoints",
    "Mapped endpoints, and discovered paths with the fields traced so far and their types; "
    "every server's when omitted",
    read_only=True,
    hidden=True,
)
@_server_field
def list_endpoints(server: str = "") -> dict[str, Any]:
    endpoints: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    for client in _clients_for(server):
        mapped, discovered = client.listing()
        endpoints += mapped
        paths += discovered
    return {"endpoints": endpoints, "paths": paths, "count": len(endpoints) + len(paths)}


# ─── Read / write ───────────────────────────────────────────────────────────


@zelos_sdk.action(
    "Read Endpoint",
    "Read a mapped endpoint by name, or GET a path and show the events it traces as",
    read_only=True,
)
@_server_field
@zelos_sdk.action.text(
    "name",
    title="Endpoint Name",
    required=False,
    default="",
    description="A mapped endpoint, or <event>/<name>",
)
@zelos_sdk.action.text(
    "path",
    title="Path",
    required=False,
    default="",
    description="Instead of a name: a URL path, e.g. /api/sensors",
)
def read_endpoint(name: str = "", path: str = "", server: str = "") -> dict[str, Any]:
    name, path = name.strip(), path.strip()
    if name and path:
        raise ValueError("Set name or path, not both")
    if not name and not path:
        raise ValueError("Set name (a mapped endpoint) or path (e.g. /api/sensors)")
    client = _get_client(server)
    if path:
        path = _check_path(path)
        return {"server": client.name, "path": path, **_run(client, client.read_path(path))}
    event, ep = _get_endpoint(client, name)
    return {
        "server": client.name,
        "event": event,
        "name": ep.name,
        "path": ep.path,
        "datatype": ep.datatype,
        "unit": ep.unit,
        "value": _run(client, client.read_endpoint(ep)),
    }


@zelos_sdk.action("Write Endpoint", "Write a value to a writable mapped endpoint (PUT)")
@_server_field
@zelos_sdk.action.text(
    "name", title="Endpoint Name", description="A mapped endpoint marked writable"
)
@zelos_sdk.action.text(
    "value",
    title="Value",
    description="In the map's units and datatype (divided by its scale on the wire); "
    "true/false for bools",
)
def write_endpoint(name: str, value: str, server: str = "") -> dict[str, Any]:
    client = _get_client(server)
    event, ep = _get_endpoint(client, name)
    # Before dispatch: a connect failure would report the wrong reason.
    if not ep.writable:
        raise ValueError(f"Endpoint '{name}' is not writable; set \"writable\": true in the map")
    typed = parse_value(str(value), ep.datatype)
    status = _run(client, client.write_endpoint(ep, typed))
    return {
        "server": client.name,
        "event": event,
        "name": ep.name,
        "path": ep.path,
        "value": typed,
        "status": status,
    }


@zelos_sdk.action("Send Request", "Send one HTTP request and show the response")
@_server_field
@zelos_sdk.action.select("method", choices=METHODS, default="GET", title="Method", required=False)
@zelos_sdk.action.text("path", title="Path", description="Below the base URL, e.g. /api/config")
@zelos_sdk.action.text(
    "body", title="Body (JSON)", required=False, default="", description="Optional JSON body"
)
def send_request(method: str, path: str, body: str = "", server: str = "") -> dict[str, Any]:
    method = method.upper()
    if method not in METHODS:
        raise ValueError(f"Method must be one of {', '.join(METHODS)}")
    path = _check_path(path)
    payload = None
    if body.strip():
        try:
            payload = json.loads(body)
        except ValueError as e:
            raise ValueError(f"Body is not JSON: {e}") from None
    client = _get_client(server)
    status, headers, raw = _run(client, client.request(method, path, payload))
    try:
        parsed: Any = parse_json(raw, path) if raw else None
    except ValueError:
        parsed = raw.decode(errors="replace")
    return {
        "server": client.name,
        "method": method,
        "path": path,
        "status": status,
        "headers": headers,
        "body": parsed,
    }


# ─── Config-form hook (standalone: runs with the extension stopped) ─────────


@zelos_sdk.action(
    "Auto-configure",
    "Check each server in the form (unsaved edits included; older apps: the saved "
    "config): GET its first path, or its base URL, and report the status and the events "
    "a JSON response traces as. Review, then save and start.",
    # Read-only, and the form wants it before a first start.
    standalone=True,
    hidden=True,
    read_only=True,
)
@zelos_sdk.action.object(
    "config",
    properties={},
    title="Config",
    description="The config form's current (possibly unsaved) data. Empty: the saved config",
    required=False,
)
def auto_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """`config` keys replace the form's: only `servers`, so Advanced survives.

    Its own asyncio.run: called from the actions thread, never the polling loop.
    """
    from .autoconfig import check_servers, probe

    if config is None:
        config = _saved_config()
    servers = [s for s in config.get("servers") or [] if isinstance(s, dict)]
    advanced = config.get("advanced") or {}
    return asyncio.run(check_servers(servers, advanced) if servers else probe(advanced))


def _saved_config() -> dict[str, Any]:
    """The saved config, or {} when there is none."""
    try:
        from zelos_sdk.extensions import load_config

        return load_config() or {}
    except Exception:  # no config yet, or it does not validate
        return {}


# ─── Registration helper ────────────────────────────────────────────────────


def register_actions(registry: ActionsRegistry) -> list[str]:
    """Register every @action function here by bare name; `init(name=ACTION_PREFIX)`
    adds the `HTTP/` prefix. Returns the registered names."""
    module = sys.modules[__name__]
    registered: list[str] = []
    for name, obj in inspect.getmembers(module):
        if name.startswith("_"):
            continue
        if inspect.isfunction(obj) and hasattr(obj, "_action"):
            registry.register(obj, name=name)
            registered.append(name)
    logger.info("Registered %d HTTP actions", len(registered))
    return registered
