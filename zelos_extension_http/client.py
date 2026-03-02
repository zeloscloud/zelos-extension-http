"""Core HTTP client with Zelos SDK integration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import zelos_sdk

from zelos_extension_http.endpoint_map import Endpoint, EndpointMap

logger = logging.getLogger(__name__)

SDK_DATATYPE_MAP = {
    "bool": zelos_sdk.DataType.Boolean,
    "uint8": zelos_sdk.DataType.UInt8,
    "int8": zelos_sdk.DataType.Int8,
    "uint16": zelos_sdk.DataType.UInt16,
    "int16": zelos_sdk.DataType.Int16,
    "uint32": zelos_sdk.DataType.UInt32,
    "int32": zelos_sdk.DataType.Int32,
    "float32": zelos_sdk.DataType.Float32,
    "uint64": zelos_sdk.DataType.UInt64,
    "int64": zelos_sdk.DataType.Int64,
    "float64": zelos_sdk.DataType.Float64,
}


def extract_json_value(data: Any, json_path: str) -> Any:
    """Extract a value from nested JSON data using dot-notation path.

    Args:
        data: Parsed JSON data (dict or list)
        json_path: Dot-separated path (e.g., "data.sensors.temperature")

    Returns:
        Extracted value, or None if path is invalid
    """
    if not json_path:
        return data

    current = data
    for key in json_path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def coerce_value(raw: Any, datatype: str, scale: float = 1.0) -> Any:
    """Coerce a raw value to the target datatype with scaling.

    Args:
        raw: Raw value from JSON
        datatype: Target data type string
        scale: Scale factor to apply

    Returns:
        Coerced value, or None if conversion fails
    """
    if raw is None:
        return None

    try:
        if datatype == "string":
            return str(raw)
        if datatype == "bool":
            return bool(raw)

        numeric = float(raw) * scale

        if datatype in ("uint8", "int8", "uint16", "int16", "uint32", "int32", "uint64", "int64"):
            return int(numeric)
        # float32, float64
        return float(numeric)
    except (ValueError, TypeError):
        return None


class HttpClient:
    """HTTP client with polling and Zelos SDK integration."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        endpoint_map: EndpointMap | None = None,
        poll_interval: float = 1.0,
        timeout: float = 5.0,
        headers: dict[str, str] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint_map = endpoint_map
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.headers = headers or {}
        self.verify_ssl = verify_ssl

        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._connected = False
        self._poll_count = 0
        self._error_count = 0
        self._last_status_code: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Zelos SDK trace source
        self._source: zelos_sdk.TraceSourceCacheLast | None = None

    def _init_trace_source(self) -> None:
        """Initialize Zelos trace source from endpoint map."""
        source_name = self.endpoint_map.name if self.endpoint_map else "http"
        self._source = zelos_sdk.TraceSourceCacheLast(source_name)

        if not self.endpoint_map or not self.endpoint_map.events:
            return

        for event_name, endpoints in self.endpoint_map.events.items():
            if not endpoints:
                continue

            fields = []
            for ep in endpoints:
                if ep.datatype == "string":
                    # String fields use a fixed-size representation
                    dtype = zelos_sdk.DataType.UInt32
                else:
                    dtype = SDK_DATATYPE_MAP.get(ep.datatype, zelos_sdk.DataType.Float32)
                fields.append(zelos_sdk.TraceEventFieldMetadata(ep.name, dtype, ep.unit))

            self._source.add_event(event_name, fields)

    def _get_sdk_datatype(self, datatype: str) -> zelos_sdk.DataType:
        """Map endpoint datatype to Zelos SDK DataType."""
        return SDK_DATATYPE_MAP.get(datatype, zelos_sdk.DataType.Float32)

    async def connect(self) -> bool:
        """Create HTTP session and verify connectivity."""
        try:
            connector = aiohttp.TCPConnector(ssl=self.verify_ssl if self.verify_ssl else False)
            client_timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=client_timeout,
                connector=connector,
            )
            # Test connectivity with a simple request
            test_url = self.base_url + "/health"
            async with self._session.get(test_url) as resp:
                self._connected = resp.status < 500
                self._last_status_code = resp.status
                if self._connected:
                    logger.info(f"Connected to {self.base_url}")
                else:
                    logger.warning(f"Server returned {resp.status} on health check")
            return self._connected
        except aiohttp.ClientError as e:
            # Health endpoint may not exist - try the base URL
            try:
                async with self._session.get(self.base_url) as resp:
                    self._connected = resp.status < 500
                    self._last_status_code = resp.status
                    if self._connected:
                        logger.info(f"Connected to {self.base_url}")
                    return self._connected
            except aiohttp.ClientError:
                pass
            logger.error(f"Connection error: {e}")
            self._connected = False
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
            self._connected = False
            logger.info("Disconnected from HTTP server")

    async def fetch_endpoint(self, endpoint: Endpoint) -> Any:
        """Fetch data from a single endpoint.

        Args:
            endpoint: Endpoint definition

        Returns:
            Extracted and coerced value, or None on error
        """
        if not self._session:
            return None

        url = self.base_url + endpoint.path
        try:
            async with self._session.request(endpoint.method, url) as resp:
                self._last_status_code = resp.status
                if resp.status != 200:
                    logger.warning(f"HTTP {resp.status} from {endpoint.path}")
                    return None
                data = await resp.json()
                raw = extract_json_value(data, endpoint.json_path)
                return coerce_value(raw, endpoint.datatype, endpoint.scale)
        except aiohttp.ClientError as e:
            logger.error(f"Request error for {endpoint.path}: {e}")
            if self._is_connection_error(e):
                self._connected = False
            return None
        except Exception as e:
            logger.error(f"Error fetching {endpoint.path}: {e}")
            return None

    async def fetch_path(self, path: str, method: str = "GET") -> dict | None:
        """Fetch raw JSON from a path.

        Args:
            path: URL path
            method: HTTP method

        Returns:
            Parsed JSON response or None on error
        """
        if not self._session:
            return None

        url = self.base_url + path
        try:
            async with self._session.request(method, url) as resp:
                self._last_status_code = resp.status
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception:
            return None

    async def write_endpoint(self, endpoint: Endpoint, value: Any) -> bool:
        """Write a value to a writable endpoint via PUT.

        Args:
            endpoint: Endpoint definition (must be writable)
            value: Value to write

        Returns:
            True if successful
        """
        if not endpoint.writable:
            logger.warning(f"Endpoint '{endpoint.name}' is not writable")
            return False

        if not self._session:
            return False

        url = self.base_url + endpoint.path
        payload = {endpoint.json_path: value} if endpoint.json_path else value
        try:
            async with self._session.put(url, json=payload) as resp:
                self._last_status_code = resp.status
                return resp.status in (200, 201, 204)
        except Exception as e:
            logger.error(f"Write error for {endpoint.path}: {e}")
            return False

    async def _poll_endpoints(self) -> dict[str, dict[str, Any]]:
        """Poll all endpoints in the endpoint map.

        Groups endpoints by path to minimize HTTP requests - endpoints
        sharing the same path are fetched once and values extracted
        from the single response.

        Returns:
            Dictionary of {event_name: {field_name: value}}
        """
        if not self.endpoint_map or not self._session:
            return {}

        # Cache fetched paths to avoid duplicate requests
        path_cache: dict[str, dict] = {}

        results: dict[str, dict[str, Any]] = {}

        for event_name, endpoints in self.endpoint_map.events.items():
            event_results: dict[str, Any] = {}
            for ep in endpoints:
                # Fetch path data (cached per path)
                if ep.path not in path_cache:
                    data = await self.fetch_path(ep.path, ep.method)
                    path_cache[ep.path] = data

                data = path_cache[ep.path]
                if data is None:
                    continue

                raw = extract_json_value(data, ep.json_path)
                value = coerce_value(raw, ep.datatype, ep.scale)
                if value is not None:
                    event_results[ep.name] = value

            if event_results:
                results[event_name] = event_results

        return results

    async def _log_values(self, values: dict[str, dict[str, Any]]) -> None:
        """Log polled values to Zelos trace source."""
        if not self._source:
            return

        for event_name, event_values in values.items():
            if not event_values:
                continue

            event = getattr(self._source, event_name, None)
            if event:
                # Filter out string values from trace logging
                numeric_values = {k: v for k, v in event_values.items() if not isinstance(v, str)}
                if numeric_values:
                    event.log(**numeric_values)

    def start(self) -> None:
        """Start the client (initialize trace source)."""
        self._running = True
        self._init_trace_source()
        logger.info("HttpClient started")

    def stop(self) -> None:
        """Stop the client."""
        self._running = False
        logger.info("HttpClient stopped")

    def run(self) -> None:
        """Run the polling loop (blocking)."""
        asyncio.run(self._run_async())

    async def _ensure_connected(self) -> bool:
        """Ensure connection is established, reconnecting if needed."""
        if self._connected and self._session and not self._session.closed:
            return True

        self._connected = False
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info(f"Connecting to {self.base_url}...")
        return await self.connect()

    def _run_on_loop(self, coro: Any) -> Any:
        """Run a coroutine on the main event loop from an action thread.

        Actions are called from SDK background threads, so we need to schedule
        async work on the running event loop via run_coroutine_threadsafe.
        """
        if self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=self.timeout + 1)
        # Fallback if loop isn't running (e.g. tests)
        return asyncio.run(coro)

    async def _run_async(self) -> None:
        """Async polling loop with automatic reconnection."""
        self._loop = asyncio.get_running_loop()
        reconnect_interval = 3.0

        try:
            while self._running:
                if not await self._ensure_connected():
                    logger.warning(f"Connection failed, retrying in {reconnect_interval}s...")
                    await asyncio.sleep(reconnect_interval)
                    continue

                try:
                    values = await self._poll_endpoints()
                    await self._log_values(values)
                    self._poll_count += 1

                    if self._poll_count % 10 == 0:
                        logger.debug(f"Poll #{self._poll_count}: {values}")

                except Exception as e:
                    self._error_count += 1
                    logger.error(f"Poll error: {e}")

                    if self._is_connection_error(e):
                        self._connected = False
                        logger.warning("Connection lost, will reconnect...")
                        continue

                await asyncio.sleep(self.poll_interval)
        finally:
            await self.disconnect()

    def _is_connection_error(self, error: Exception) -> bool:
        """Check if an exception indicates a connection problem."""
        error_str = str(error).lower()
        connection_indicators = [
            "connection",
            "timeout",
            "refused",
            "reset",
            "broken pipe",
            "cannot connect",
            "name or service not known",
        ]
        return any(ind in error_str for ind in connection_indicators)

    # SDK Actions
    @zelos_sdk.action("Get Status", "Get connection and polling status")
    def get_status(self) -> dict[str, Any]:
        """Get current client status."""
        return {
            "connected": self._connected,
            "base_url": self.base_url,
            "poll_count": self._poll_count,
            "error_count": self._error_count,
            "poll_interval": self.poll_interval,
            "last_status_code": self._last_status_code,
            "endpoints": len(self.endpoint_map.endpoints) if self.endpoint_map else 0,
        }

    @zelos_sdk.action("Send Request", "Send an HTTP request and display the response")
    @zelos_sdk.action.text("path", title="URL Path (e.g., /api/sensors)")
    @zelos_sdk.action.select(
        "method",
        choices=["GET", "POST", "PUT"],
        default="GET",
        title="HTTP Method",
    )
    @zelos_sdk.action.text("body", title="Request Body (JSON, optional)")
    def send_request(self, path: str, method: str, body: str) -> dict[str, Any]:
        """Send an arbitrary HTTP request."""
        url = self.base_url + path

        async def _send() -> dict[str, Any]:
            if not self._session or self._session.closed:
                await self.connect()
            if not self._session:
                return {"error": "Not connected", "success": False}

            kwargs: dict[str, Any] = {}
            if body and body.strip():
                try:
                    kwargs["json"] = json.loads(body)
                except json.JSONDecodeError:
                    kwargs["data"] = body

            try:
                async with self._session.request(method, url, **kwargs) as resp:
                    try:
                        resp_data = await resp.json()
                    except Exception:
                        resp_data = await resp.text()
                    return {
                        "status_code": resp.status,
                        "response": resp_data,
                        "success": resp.status < 400,
                    }
            except Exception as e:
                return {"error": str(e), "success": False}

        return self._run_on_loop(_send())

    @zelos_sdk.action("List Endpoints", "List all configured endpoints")
    def list_endpoints(self) -> dict[str, Any]:
        """List all endpoints in the endpoint map."""
        if not self.endpoint_map:
            return {"endpoints": [], "count": 0}

        eps = [
            {
                "name": ep.name,
                "path": ep.path,
                "method": ep.method,
                "datatype": ep.datatype,
                "unit": ep.unit,
                "json_path": ep.json_path,
                "writable": ep.writable,
            }
            for ep in self.endpoint_map.endpoints
        ]
        return {"endpoints": eps, "count": len(eps)}

    @zelos_sdk.action("Read Endpoint", "Read a value from a named endpoint")
    @zelos_sdk.action.text("name", title="Endpoint Name")
    def read_endpoint(self, name: str) -> dict[str, Any]:
        """Read a single endpoint by name."""
        if not self.endpoint_map:
            return {"error": "No endpoint map loaded", "success": False}

        ep = self.endpoint_map.get_by_name(name)
        if not ep:
            return {"error": f"Endpoint '{name}' not found", "success": False}

        async def _read() -> Any:
            if not self._session or self._session.closed:
                await self.connect()
            return await self.fetch_endpoint(ep)

        value = self._run_on_loop(_read())
        return {
            "name": name,
            "path": ep.path,
            "value": value,
            "unit": ep.unit,
            "success": value is not None,
        }

    @zelos_sdk.action("Write Endpoint", "Write a value to a writable endpoint")
    @zelos_sdk.action.text("name", title="Endpoint Name")
    @zelos_sdk.action.text("value", title="Value")
    def write_endpoint_action(self, name: str, value: str) -> dict[str, Any]:
        """Write a value to a named writable endpoint."""
        if not self.endpoint_map:
            return {"error": "No endpoint map loaded", "success": False}

        ep = self.endpoint_map.get_by_name(name)
        if not ep:
            return {"error": f"Endpoint '{name}' not found", "success": False}

        if not ep.writable:
            return {"error": f"Endpoint '{name}' is not writable", "success": False}

        # Parse value to appropriate type
        typed_value: Any
        try:
            typed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            typed_value = value

        async def _write() -> bool:
            if not self._session or self._session.closed:
                await self.connect()
            return await self.write_endpoint(ep, typed_value)

        success = self._run_on_loop(_write())
        return {
            "name": name,
            "path": ep.path,
            "value": typed_value,
            "success": success,
        }
