#!/usr/bin/env python3
"""Zelos HTTP Extension - CLI entry point.

Modes:
1. App mode (default): Loads configuration from config.json when run from Zelos App
2. Demo mode: Uses built-in weather station simulator (no server required)
3. CLI trace mode: Direct command-line usage with explicit arguments

Examples:
    uv run main.py                           # App mode
    uv run main.py demo                      # Demo mode
    uv run main.py trace http://host:8080 endpoints.json
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType
from typing import TYPE_CHECKING

import rich_click as click
import zelos_sdk
from zelos_sdk.hooks.logging import TraceLoggingHandler

if TYPE_CHECKING:
    from zelos_extension_http.client import HttpClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_client: HttpClient | None = None


def shutdown_handler(signum: int, frame: FrameType | None) -> None:
    """Handle graceful shutdown on SIGTERM or SIGINT."""
    logger.info("Shutting down...")
    if _client:
        _client.stop()
    sys.exit(0)


def set_shutdown_client(client: HttpClient) -> None:
    """Set the client for shutdown handling."""
    global _client
    _client = client


@click.group(invoke_without_command=True)
@click.option("--demo", is_flag=True, help="Run in demo mode with simulated weather station")
@click.pass_context
def cli(ctx: click.Context, demo: bool) -> None:
    """Zelos HTTP Extension - Poll and monitor REST API endpoints.

    When run without a subcommand, starts in app mode using configuration
    from the Zelos App (config.json).

    Use --demo flag or 'demo' subcommand for simulated weather station.
    Use 'trace' subcommand for direct CLI access without Zelos App.
    """
    ctx.ensure_object(dict)
    ctx.obj["shutdown_handler"] = set_shutdown_client
    ctx.obj["demo"] = demo

    if ctx.invoked_subcommand is None:
        run_app_mode(ctx, demo=demo)


def run_app_mode(ctx: click.Context, demo: bool = False) -> None:
    """Run in app mode with Zelos SDK initialization.

    Note: zelos_sdk.init() is called inside cli/app.py AFTER actions are registered,
    because the SDK requires actions to be registered before init().
    """
    handler = TraceLoggingHandler("zelos_extension_http_logger")
    logging.getLogger().addHandler(handler)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    from zelos_extension_http.cli.app import run_app_mode as _run_app_mode

    _run_app_mode(demo=demo)


@cli.command()
@click.pass_context
def demo(ctx: click.Context) -> None:
    """Run demo mode with simulated weather station.

    Starts a local HTTP server with simulated sensor data
    and connects to it. No hardware or external server required.

    The simulated weather station includes:
    - Environmental sensors (temperature, humidity, pressure, wind)
    - Power system (battery, solar)
    - System status (CPU, memory, uptime)
    - Writable configuration endpoints
    """
    run_app_mode(ctx, demo=True)


@cli.command()
@click.argument("base_url", type=str)
@click.argument("endpoint_map_file", type=click.Path(exists=True), required=False)
@click.option("--interval", "-i", type=float, default=1.0, help="Poll interval in seconds")
@click.option("--timeout", type=float, default=5.0, help="Request timeout in seconds")
@click.option("--header", "-H", multiple=True, help="Custom header (Key: Value)")
@click.pass_context
def trace(
    ctx: click.Context,
    base_url: str,
    endpoint_map_file: str | None,
    interval: float,
    timeout: float,
    header: tuple[str, ...],
) -> None:
    """Trace HTTP endpoints from command line.

    BASE_URL is the server base URL (e.g., http://192.168.1.100:8080).

    ENDPOINT_MAP_FILE is an optional path to a JSON endpoint map file.

    \b
    Examples:
        # With endpoint map
        uv run main.py trace http://192.168.1.100:8080 endpoints.json

        # With custom headers
        uv run main.py trace http://api.example.com endpoints.json -H "Authorization: Bearer token"
    """
    from zelos_extension_http.client import HttpClient
    from zelos_extension_http.endpoint_map import EndpointMap

    handler = TraceLoggingHandler("zelos_extension_http_logger")
    logging.getLogger().addHandler(handler)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Parse headers
    headers: dict[str, str] = {}
    for h in header:
        if ":" in h:
            key, value = h.split(":", 1)
            headers[key.strip()] = value.strip()

    # Load endpoint map if provided
    endpoint_map = None
    if endpoint_map_file:
        try:
            endpoint_map = EndpointMap.from_file(endpoint_map_file)
            logger.info(f"Loaded endpoint map with {len(endpoint_map.endpoints)} endpoints")
        except Exception as e:
            raise click.ClickException(f"Invalid endpoint map: {e}") from e

    global _client
    _client = HttpClient(
        base_url=base_url,
        endpoint_map=endpoint_map,
        poll_interval=interval,
        timeout=timeout,
        headers=headers,
    )

    # Register actions BEFORE init() — SDK requires this ordering
    zelos_sdk.actions_registry.register(_client)
    zelos_sdk.init(name="zelos_extension_http", actions=True)

    logger.info(f"Starting HTTP trace: {base_url}")
    _client.start()
    _client.run()


if __name__ == "__main__":
    cli()
