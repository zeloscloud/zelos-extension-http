"""App mode runner for Zelos HTTP extension."""

from __future__ import annotations

import asyncio
import logging
import threading
from importlib import resources
from pathlib import Path
from typing import Any

import zelos_sdk
from zelos_sdk.extensions import load_config

from zelos_extension_http.client import HttpClient
from zelos_extension_http.endpoint_map import EndpointMap

logger = logging.getLogger(__name__)

DEMO_HOST = "127.0.0.1"
DEMO_PORT = 8090


def get_demo_endpoint_map_path() -> Path:
    """Get path to the bundled demo endpoint map."""
    with resources.as_file(
        resources.files("zelos_extension_http.demo").joinpath("demo_device.json")
    ) as path:
        return path


def start_demo_server() -> threading.Thread:
    """Start the demo HTTP server in a background thread."""
    from zelos_extension_http.demo.simulator import DemoServer

    def run_server() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        server = DemoServer(host=DEMO_HOST, port=DEMO_PORT)
        try:
            loop.run_until_complete(server.start())
            loop.run_forever()
        except Exception as e:
            logger.error(f"Demo server error: {e}")
        finally:
            loop.run_until_complete(server.stop())
            loop.close()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"Demo server started on http://{DEMO_HOST}:{DEMO_PORT}")

    # Wait for server to be ready
    import time

    time.sleep(0.5)

    return thread


def run_app_mode(demo: bool = False) -> None:
    """Run the extension in app mode with configuration from Zelos App."""
    config = load_config()

    if demo or config.get("demo", False):
        logger.info("Demo mode: using built-in weather station simulator")
        _server_thread = start_demo_server()

        config["base_url"] = f"http://{DEMO_HOST}:{DEMO_PORT}"
        demo_map_path = get_demo_endpoint_map_path()
        config["endpoint_map_file"] = str(demo_map_path)

    # Set log level
    log_level = config.get("log_level", "INFO")
    logging.getLogger().setLevel(getattr(logging, log_level))

    # Parse custom headers
    headers: dict[str, str] = {}
    headers_str = config.get("headers", "")
    if headers_str and headers_str.strip():
        import json

        try:
            headers = json.loads(headers_str)
        except Exception as e:
            logger.warning(f"Invalid headers JSON: {e}")

    # Load endpoint map if provided
    endpoint_map = None
    map_file = config.get("endpoint_map_file")
    if map_file:
        map_path = Path(map_file)
        if map_path.exists():
            try:
                endpoint_map = EndpointMap.from_file(map_path)
                logger.info(f"Loaded endpoint map with {len(endpoint_map.endpoints)} endpoints")
            except Exception as e:
                logger.error(f"Failed to load endpoint map: {e}")
        else:
            logger.warning(f"Endpoint map file not found: {map_file}")

    # Override base_url from endpoint map if not set in config
    base_url = config.get("base_url", "http://127.0.0.1:8080")
    if endpoint_map and endpoint_map.base_url and base_url == "http://127.0.0.1:8080":
        base_url = endpoint_map.base_url

    client_kwargs: dict[str, Any] = {
        "base_url": base_url,
        "endpoint_map": endpoint_map,
        "poll_interval": config.get("poll_interval", 1.0),
        "timeout": config.get("timeout", 5.0),
        "headers": headers,
        "verify_ssl": config.get("verify_ssl", True),
    }

    client = HttpClient(**client_kwargs)

    # Register actions BEFORE init() — SDK requires this ordering
    zelos_sdk.actions_registry.register(client)

    # Initialize SDK (must be called after actions are registered)
    zelos_sdk.init(name="zelos_extension_http", actions=True)

    # Start and run
    client.start()
    client.run()
