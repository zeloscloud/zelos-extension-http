"""App mode: run from the Zelos App's saved config, or demo mode.

Startup problems are one logger.error line plus exit 1; tracebacks are for bugs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import ssl
import sys
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlparse

import zelos_sdk
from zelos_sdk.extensions import ConfigValidationError, load_config
from zelos_sdk.hooks.logging import TraceLoggingHandler

from zelos_extension_http import ACTION_PREFIX
from zelos_extension_http import actions as http_actions
from zelos_extension_http.client import (
    HttpClient,
    HttpRunner,
    default_server_name,
    has_userinfo,
    tls_context,
    url_port,
)
from zelos_extension_http.constants import (
    DEFAULT_PREFIX,
    LOG_SOURCE_NAME,
    RESERVED_NAMES,
    name_error,
)
from zelos_extension_http.endpoint_map import EndpointMap

logger = logging.getLogger(__name__)

DEMO_HOST = "127.0.0.1"
DEMO_PORT = 8090

#: Defaults of the schema's `advanced` object: settings shared by every server.
ADVANCED_DEFAULTS: dict[str, Any] = {
    "prefix": DEFAULT_PREFIX,
    "log_level": "INFO",
    "retry_unreachable_at_start": False,
    "timeout": 5.0,
    "ca_file": "",
    "cert_file": "",
    "key_file": "",
}

#: Defaults of one `servers[]` entry. "" / None inherit from `advanced`.
SERVER_DEFAULTS: dict[str, Any] = {
    "name": "",
    "base_url": "",
    "paths": [],
    "endpoint_map_file": "",
    "poll_interval": 1.0,
    "timeout": None,
    "verify_tls": True,
    "ca_file": "",
    "cert_file": "",
    "key_file": "",
}

# `demo`: the removed Demo Mode toggle, still in older saved configs; ignored.
TOP_LEVEL_KEYS = frozenset({"demo", "servers", "advanced"})


def _exit_on(error: str | None) -> None:
    if error:
        _fail(error)


def _fail(error: str) -> NoReturn:
    logger.error("%s", error)
    sys.exit(1)


def resolve_advanced(config: dict[str, Any]) -> dict[str, Any]:
    """`advanced` over its defaults; an absent `prefix` is the default, an empty one clears it."""
    return {**ADVANCED_DEFAULTS, **(config.get("advanced") or {})}


def read_raw_config() -> dict[str, Any]:
    """config.json as saved: an old config fails the schema without saying the format changed."""
    path = Path(os.environ.get("ZELOS_CONFIG_PATH") or "config.json")
    return json.loads(path.read_text()) if path.is_file() else {}


def reject_legacy_shape(config: dict[str, Any]) -> None:
    """Exit on a config from before `servers[]`, rather than misread it. Names the keys,
    never their values: an old `headers` held tokens."""
    legacy = sorted(k for k in config if k not in TOP_LEVEL_KEYS)
    if legacy:
        _fail(
            "Config format changed: servers are now listed under servers[] with shared "
            f"settings under advanced (found top-level {', '.join(legacy)}); "
            "reconfigure the extension"
        )


def inherit(server: dict[str, Any], advanced: dict[str, Any]) -> dict[str, Any]:
    """A server's timeout and TLS files: its own, else ("" / None) the advanced one.

    The client certificate and key inherit as a pair: a mix would fail as a key mismatch.
    """
    pair = ("cert_file", "key_file")
    own_pair = any(server[k] for k in pair)
    return {
        "timeout": advanced["timeout"] if server["timeout"] in ("", None) else server["timeout"],
        "ca_file": server["ca_file"] or advanced["ca_file"],
        **{k: (server if own_pair else advanced)[k] for k in pair},
    }


def resolve_servers(config: dict[str, Any], advanced: dict[str, Any]) -> list[dict[str, Any]]:
    """HttpClient arguments per configured server, or exit.

    Typed names must be legal trace names; default names are the sanitized host,
    `_<port>` when two share a host. Duplicates and reserved names exit.
    """
    reject_legacy_shape(config)
    entries = config.get("servers") or []
    if not entries:
        _fail("No servers configured: add one under Servers")

    servers: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        server = {**SERVER_DEFAULTS, **entry}
        name = str(server["name"]).strip()
        _exit_on(name_error(name, "server Name", RESERVED_NAMES))
        base_url = str(server["base_url"]).strip()
        if not base_url:
            _fail(f"servers[{i}] has no Base URL")
        label = name or default_server_name(base_url)
        # Never echo the URL here: it holds the password.
        if has_userinfo(base_url):
            _fail(
                f"Server '{label}': remove the user name / password from the Base URL; "
                "the extension sends no credentials"
            )
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            _fail(f"Server '{label}': Base URL '{base_url}' must be http:// or https:// and a host")
        servers.append({**server, "name": name, "base_url": base_url})

    defaults = [default_server_name(s["base_url"]) for s in servers if not s["name"]]
    for s in servers:
        if not s["name"]:
            name = default_server_name(s["base_url"])
            s["name"] = f"{name}_{url_port(s['base_url'])}" if defaults.count(name) > 1 else name

    seen: set[str] = set()
    for s in servers:
        if s["name"] in RESERVED_NAMES:
            _fail(f"Server name '{s['name']}' is reserved for the extension's logs; set Name")
        if s["name"] in seen:
            _fail(f"Duplicate server name '{s['name']}'; set Name on one of them.")
        seen.add(s["name"])

    return [
        {
            "name": s["name"],
            "base_url": s["base_url"],
            # A blank entry is what the form's Add leaves behind; nothing to poll.
            "paths": [str(p).strip() for p in s["paths"] or [] if str(p).strip()],
            "endpoint_map_file": s["endpoint_map_file"],
            "poll_interval": s["poll_interval"],
            "verify_tls": s["verify_tls"],
            **inherit(s, advanced),
        }
        for s in servers
    ]


def demo_server_kwargs(advanced: dict[str, Any]) -> dict[str, Any]:
    """The built-in simulator as the one server `demo`, traced through the demo map."""
    return {
        "name": "demo",
        "base_url": f"http://{DEMO_HOST}:{DEMO_PORT}",
        "paths": [],
        "endpoint_map_file": str(get_demo_map_path()),
        "poll_interval": 1.0,
        "verify_tls": True,
        "timeout": advanced["timeout"],
        "ca_file": "",
        "cert_file": "",
        "key_file": "",
    }


def build_clients(servers: list[dict[str, Any]]) -> list[HttpClient]:
    """One client per server, or exit on a server's map, TLS files or paths."""
    clients = []
    for kwargs in servers:
        kwargs = dict(kwargs)
        name = kwargs["name"]
        endpoint_map = load_endpoint_map(kwargs.pop("endpoint_map_file"), name)
        if not kwargs["paths"] and not (endpoint_map and endpoint_map.endpoints):
            _fail(f"Server '{name}': nothing to poll; add Paths or an Endpoint Map File")
        try:
            ctx = tls_context(
                kwargs["verify_tls"],
                kwargs.pop("ca_file"),
                kwargs.pop("cert_file"),
                kwargs.pop("key_file"),
            )
        except (ValueError, OSError, ssl.SSLError) as e:
            _fail(f"Server '{name}': cannot load its TLS files: {e}")
        try:
            clients.append(HttpClient(endpoint_map=endpoint_map, ssl_context=ctx, **kwargs))
        except ValueError as e:
            _fail(f"Server '{name}': invalid configuration: {e}")
    return clients


def load_endpoint_map(map_file: str | None, server: str) -> EndpointMap | None:
    """The configured endpoint map, or exit: a map that does not load would trace nothing."""
    if not map_file:
        return None
    try:
        endpoint_map = EndpointMap.from_file(Path(map_file))
    except Exception as e:
        _fail(f"[{server}] Failed to load endpoint map '{map_file}': {e}")
    logger.info(
        "[%s] Loaded endpoint map '%s' with %d endpoints",
        server,
        endpoint_map.name,
        len(endpoint_map.endpoints),
    )
    return endpoint_map


def get_demo_map_path() -> Path:
    """Path to the bundled demo endpoint map."""
    with resources.as_file(
        resources.files("zelos_extension_http.demo").joinpath("demo_device.json")
    ) as path:
        return path


def start_demo_server() -> None:
    """Start the weather station simulator in a background thread, or exit."""
    from zelos_extension_http.demo.simulator import start_demo_server_thread

    try:
        start_demo_server_thread(DEMO_HOST, DEMO_PORT)
    except RuntimeError as e:
        _fail(str(e))
    logger.info("Demo server started on http://%s:%d", DEMO_HOST, DEMO_PORT)


def apply_log_level(level_name: str) -> None:
    """Apply the configured log level, falling back to INFO on a bad value."""
    level = getattr(logging, str(level_name).upper(), None)
    if not isinstance(level, int):
        logger.warning("Invalid log level '%s', using INFO", level_name)
        level = logging.INFO
    logging.getLogger().setLevel(level)
    # Library chatter (the simulator's access log, connection details) only at DEBUG.
    logging.getLogger("aiohttp").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )


def open_sources(prefix: str) -> zelos_sdk.TraceSource | None:
    """The prefix's shared source with the log on its `log` event; cleared, None and the
    log on its own source. Earlier records reach stderr only.

    The SDK's global source, created here first, so `init()` reuses it instead of
    adding an empty source named after the action namespace.
    """
    source = zelos_sdk.init_global_source(prefix or LOG_SOURCE_NAME)
    handler = TraceLoggingHandler(source)
    # INFO and above: DEBUG would flood the trace with library chatter.
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    if prefix:
        logger.info("Trace prefix: %s", prefix)
        return source
    logger.info("Trace prefix cleared: one trace source per server")
    return None


def recording_path(file: Path | None) -> Path | None:
    """`--file` as a path; given without a name, a UTC timestamp."""
    if file is not None and str(file) == ".":
        return Path(f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.trz")
    return file


def serve(
    clients: list[HttpClient],
    prefix: str,
    retry_unreachable: bool = False,
    record: Path | None = None,
) -> None:
    """Publish the action surface, then poll every server until shutdown.

    Actions register before `init()`: registered later they may never be
    advertised. A recording opens first, so it holds every event declaration.
    """
    runner = HttpRunner(clients, retry_unreachable)
    http_actions.set_runner(runner)
    http_actions.register_actions(zelos_sdk.actions_registry)
    with contextlib.ExitStack() as stack:
        if record:
            logger.info("Recording trace to %s", record)
            stack.enter_context(zelos_sdk.TraceWriter(str(record)))
        shared = open_sources(prefix)
        zelos_sdk.init(name=ACTION_PREFIX, actions=True)
        for client in clients:
            client.start(shared)
        runner.run()
    if runner.failed_at_start:
        sys.exit(1)


def run_config(config: dict[str, Any], demo: bool = False, record: Path | None = None) -> None:
    """Resolve an app-shaped config (servers[], advanced) and serve it; `demo` (the CLI's
    `demo` / `--demo`) replaces the servers with the built-in simulator."""
    advanced = resolve_advanced(config)
    apply_log_level(advanced["log_level"])
    prefix = str(advanced["prefix"] or "").strip()
    _exit_on(name_error(prefix, "Prefix"))

    if demo:
        logger.info("Demo mode: using built-in weather station simulator")
        start_demo_server()
        servers = [demo_server_kwargs(advanced)]
    else:
        servers = resolve_servers(config, advanced)

    serve(
        build_clients(servers),
        prefix,
        bool(advanced["retry_unreachable_at_start"]),
        recording_path(record),
    )


def run_app_mode(demo: bool = False, record: Path | None = None) -> None:
    """Run the extension with configuration from the Zelos App.

    Args:
        demo: If True, run the built-in weather station simulator as the only server
        record: Also record the trace to this .trz file ("." names it by UTC time)
    """
    reject_legacy_shape(read_raw_config())
    try:
        config = load_config()
    except ConfigValidationError as e:
        _fail(f"Invalid configuration: {'; '.join(m.strip(' •') for m in e.errors)}")
    run_config(config, demo, record)
