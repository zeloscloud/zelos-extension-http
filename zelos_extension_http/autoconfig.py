"""Check the config form's servers for its Auto-configure button.

Read-only: one GET per server with the form's TLS settings, PROBE_TIMEOUT each.
With no servers there is nothing to look for: HTTP has no canonical port, and
the extension never sweeps a network.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any
from urllib.parse import urlparse

import aiohttp

from zelos_extension_http.cli.app import ADVANCED_DEFAULTS, SERVER_DEFAULTS, inherit
from zelos_extension_http.client import (
    HEADERS,
    default_server_name,
    describe_error,
    has_userinfo,
    tls_context,
)
from zelos_extension_http.discovery import event_name, flatten

PROBE_TIMEOUT = 2.0

NO_SERVERS = "Add a server with its base URL, then run Auto-configure."


async def check_servers(servers: list[dict[str, Any]], advanced: dict[str, Any]) -> dict[str, Any]:
    """The form's servers, each checked and kept as entered."""
    advanced = {**ADVANCED_DEFAULTS, **advanced}
    results = await asyncio.gather(*(_check({**SERVER_DEFAULTS, **s}, advanced) for s in servers))
    found = [clause for ok, clause in results if ok]
    problems = [clause for ok, clause in results if not ok]
    if not found:
        return {"status": "error", "message": " ".join(problems)}
    return {
        "status": "success",
        "message": " ".join([f"Found {'; '.join(found)}.", *problems]),
        "config": {"servers": [dict(s) for s in servers]},
    }


async def probe(advanced: dict[str, Any]) -> dict[str, Any]:
    """No servers in the form: nothing to probe."""
    return {"status": "error", "message": NO_SERVERS}


async def _check(server: dict[str, Any], advanced: dict[str, Any]) -> tuple[bool, str]:
    """(answered with JSON, the clause for the message). Never echoes a URL with credentials."""
    base = str(server["base_url"]).strip().rstrip("/")
    label = str(server["name"]).strip() or (default_server_name(base) if base else "A server")
    if not base:
        return False, f"{label}: set its Base URL."
    if has_userinfo(base):
        return False, (
            f"{label}: remove the user name / password from the Base URL; "
            "the extension sends no credentials."
        )
    if urlparse(base).scheme not in ("http", "https"):
        return False, f"{label}: the Base URL must start with http:// or https://."
    paths = [p.strip() for p in server["paths"] or [] if isinstance(p, str) and p.strip()]
    url = base + (paths[0] if paths else "")
    effective = inherit(server, advanced)
    try:
        ctx = tls_context(
            server["verify_tls"],
            effective["ca_file"],
            effective["cert_file"],
            effective["key_file"],
        )
    except (ValueError, OSError, ssl.SSLError) as e:
        return False, f"{label}: cannot load its TLS files ({e})."
    try:
        async with (
            aiohttp.ClientSession(
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT),
                connector=aiohttp.TCPConnector(ssl=ctx),
            ) as session,
            session.get(url) as resp,
        ):
            status, raw = resp.status, await resp.read()
    except (aiohttp.ClientError, TimeoutError) as e:
        reason, fix = describe_error(e, PROBE_TIMEOUT)
        return False, f"Couldn't connect to {url} ({reason}); {fix}."
    if not 200 <= status < 300:
        return False, f"{url} answered HTTP {status}."
    try:
        body = json.loads(raw)
    except ValueError:
        return False, f"{url} answered HTTP {status}, not JSON."
    unverified = "" if server["verify_tls"] else ", TLS not verified"
    if not paths:
        then = (
            "its endpoint map picks the signals"
            if server["endpoint_map_file"]
            else "add a path under Paths to trace it"
        )
        return True, f"{label} at {url} (HTTP {status}, JSON{unverified}; {then})"
    rows, _ = flatten(event_name(paths[0]), body)
    events = ", ".join(sorted(rows)) or "no fields yet"
    return True, f"{label} at {url} (HTTP {status}, JSON{unverified}; traces {events})"
