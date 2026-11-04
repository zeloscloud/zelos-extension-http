#!/usr/bin/env python3
"""Zelos HTTP Extension - CLI entry point.

Modes:

1. App mode (default): configuration comes from the Zelos App
2. Demo mode: built-in weather station simulator, no server needed
3. CLI trace mode: one server's base URL, paths and endpoint map on the command line

Examples:
    uv run main.py                                          # app mode
    uv run main.py --file                                   # app mode, also record a .trz
    uv run main.py demo                                     # simulated weather station
    uv run main.py demo-server --port 8090                  # standalone simulator
    uv run main.py trace http://192.168.1.100:8080 -p /api/status
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import rich_click as click

from zelos_extension_http import ACTION_PREFIX as _ACTION_PREFIX
from zelos_extension_http.cli import app as app_mode
from zelos_extension_http.client import default_server_name

#: Re-exported so a packaging-time action inventory - which reads this entry
#: module - sees the same namespace the live registration uses. See the
#: definition in `zelos_extension_http/__init__.py`.
ACTION_PREFIX = _ACTION_PREFIX

# UTC ISO 8601 with ms, matching the SDK's Rust tracing lines in the same log stream
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03dZ %(levelname)5s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# aiohttp logs connection details and the simulator's access lines; only DEBUG
# re-opens them - see cli/app.apply_log_level.
logging.getLogger("aiohttp").setLevel(logging.WARNING)


@click.group(invoke_without_command=True)
@click.option("--demo", is_flag=True, help="Run against the built-in weather station simulator")
@click.option(
    "--file",
    type=click.Path(path_type=Path),
    default=None,
    is_flag=False,
    flag_value=".",
    help="Record trace to .trz file (defaults to UTC.trz if no filename specified)",
)
@click.pass_context
def cli(ctx: click.Context, demo: bool, file: Path | None) -> None:
    """Zelos HTTP Extension - poll REST APIs and trace their JSON responses.

    Without a subcommand this starts in app mode using the configuration saved
    by the Zelos App. Use --demo or the 'demo' subcommand for a simulated
    weather station, and 'trace' for direct CLI access.
    """
    if ctx.invoked_subcommand is None:
        app_mode.run_app_mode(demo=demo, record=file)


@cli.command()
def demo() -> None:
    """Run against the built-in weather station simulator.

    Starts a local REST API with simulated environment, power, system and
    writable config endpoints, then traces it through the demo endpoint map.
    No server required.
    """
    app_mode.run_app_mode(demo=True)


@cli.command("demo-server")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address")
@click.option("--port", type=int, default=app_mode.DEMO_PORT, show_default=True, help="TCP port")
def demo_server(host: str, port: int) -> None:
    """Run the weather station simulator standalone until Ctrl-C.

    \b
    Endpoints:
        GET /api/sensors   temperature, humidity, pressure, wind {direction, gust}
        GET /api/power     battery, solar, charging, cell_voltages [4]
        GET /api/system    uptime, CPU temperature, memory, firmware, hostname
        GET/PUT /api/config  sample_rate, alarm_threshold, station_name
    """
    import asyncio

    from zelos_extension_http.demo.simulator import run_sim

    try:
        asyncio.run(run_sim(host, port))
    except OSError as e:
        raise click.ClickException(
            f"Could not start simulator on {host}:{port} ({e}); one may already be running there"
        ) from e


@cli.command()
@click.argument("base_url", type=str)
@click.argument("endpoint_map_file", type=click.Path(exists=True), required=False)
@click.option(
    "--path",
    "-p",
    "paths",
    multiple=True,
    help="Path polled without a map, its JSON traced as it comes (repeatable)",
)
@click.option("--interval", "-i", type=float, default=1.0, help="Poll interval in seconds")
@click.option("--timeout", type=float, default=5.0, help="Request timeout in seconds")
@click.option("--ca-file", default="", help="CA certificates (PEM) that verify the server")
@click.option("--cert-file", default="", help="Client certificate (PEM), for mutual TLS")
@click.option("--key-file", default="", help="Client private key (PEM)")
@click.option("--no-verify-tls", is_flag=True, help="Accept any server certificate (warns)")
def trace(
    base_url: str,
    endpoint_map_file: str | None,
    paths: tuple[str, ...],
    interval: float,
    timeout: float,
    ca_file: str,
    cert_file: str,
    key_file: str,
    no_verify_tls: bool,
) -> None:
    """Trace one HTTP server from the command line.

    BASE_URL is the server (e.g. http://192.168.1.100:8080).
    ENDPOINT_MAP_FILE is an optional JSON endpoint map.

    \b
    Examples:
        uv run main.py trace http://192.168.1.100:8080 endpoints.json
        uv run main.py trace http://127.0.0.1:8090 -p /api/sensors -p /api/power
        uv run main.py trace https://gateway.local --ca-file ca.pem -p /api/status
    """
    # The host only: a URL may carry a password, refused at config.
    logger.info("Starting HTTP trace: %s", default_server_name(base_url))
    # One server through the same resolution as app mode.
    app_mode.run_config(
        {
            "servers": [
                {
                    "base_url": base_url,
                    "paths": list(paths),
                    "endpoint_map_file": endpoint_map_file or "",
                    "poll_interval": interval,
                    "verify_tls": not no_verify_tls,
                    "ca_file": ca_file,
                    "cert_file": cert_file,
                    "key_file": key_file,
                }
            ],
            "advanced": {"timeout": timeout},
        }
    )


if __name__ == "__main__":
    cli()
