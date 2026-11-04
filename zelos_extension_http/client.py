"""One HttpClient per server, all polled in one HttpRunner event loop."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import socket
import ssl
import time
from collections.abc import Callable, Coroutine, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlparse

import aiohttp
import zelos_sdk

from zelos_extension_http.constants import HEALTH_EVENT, trace_layout
from zelos_extension_http.discovery import BOOL, NUMBER, STRING, event_name, flatten, kind_of
from zelos_extension_http.endpoint_map import (
    SDK_DATATYPES,
    Endpoint,
    EndpointMap,
    coerce_value,
    encode_value,
    extract_json_value,
)

logger = logging.getLogger(__name__)

# Reconnect backoff: a server down for hours costs one attempt a minute.
RECONNECT_INITIAL = 3.0
RECONNECT_MAX = 60.0

# Ceiling on stopping every server: a wedged request must not make the process unkillable.
SHUTDOWN_TIMEOUT = 3.0


def _version() -> str:
    try:
        return version("zelos-extension-http")
    except PackageNotFoundError:
        return "unknown"


#: Every request: the extension reads JSON and names itself; nothing else is sent.
HEADERS = {"Accept": "application/json", "User-Agent": f"zelos-extension-http/{_version()}"}

HEALTH_FIELDS = (
    ("reachable", zelos_sdk.DataType.Boolean, ""),
    ("last_status", zelos_sdk.DataType.UInt16, ""),
    ("latency_ms", zelos_sdk.DataType.Float32, "ms"),
    ("errors", zelos_sdk.DataType.UInt32, ""),
)
_UINT32_MAX = 2**32 - 1

DISCOVERED_TYPES = {
    BOOL: zelos_sdk.DataType.Boolean,
    NUMBER: zelos_sdk.DataType.Float64,
    STRING: zelos_sdk.DataType.String,
}

#: Response headers `send_request` returns; the rest are transport detail.
RESPONSE_HEADERS = ("Content-Type", "Content-Length", "Location", "ETag", "Last-Modified")


def has_userinfo(url: str) -> bool:
    """Whether `url` carries `user[:password]@`: the extension never sends credentials."""
    return "@" in urlparse(url.strip()).netloc


def default_server_name(base_url: str) -> str:
    """The base URL host as a trace name: `weather01`, `192_168_1_10`."""
    return zelos_sdk.sanitize_name(urlparse(base_url).hostname or base_url, kind="source")


def url_port(base_url: str) -> int:
    """The base URL's port, explicit or the scheme's."""
    parsed = urlparse(base_url)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def tls_context(
    verify: bool = True, ca_file: str = "", cert_file: str = "", key_file: str = ""
) -> ssl.SSLContext:
    """The TLS settings of one server. `ca_file` replaces the system's CAs.

    Raises:
        ValueError: A key without a certificate
        OSError, ssl.SSLError: An unreadable or mismatched file
    """
    if key_file and not cert_file:
        raise ValueError("a key file needs its certificate file")
    ctx = ssl.create_default_context(cafile=ca_file or None)
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cert_file:
        ctx.load_cert_chain(cert_file, key_file or None)
    return ctx


def parse_json(raw: bytes, path: str) -> Any:
    """A response body as JSON, whatever its Content-Type: devices often mislabel it.

    Raises:
        ValueError: If the body is not JSON
    """
    try:
        return json.loads(raw)
    except ValueError:
        raise ValueError(f"The response of {path} is not JSON") from None


def describe_error(error: BaseException | None, timeout: float) -> tuple[str, str]:
    """(what failed, what the user does) for a request that got no HTTP answer."""
    check = "check the Base URL and that the server is running"
    if error is None:
        return "nothing to poll", "add Paths or an Endpoint Map File"
    if isinstance(error, TimeoutError):
        return f"no response within {timeout:g} s", f"{check}, or raise Timeout"
    if isinstance(error, aiohttp.ClientConnectorCertificateError):
        reason = getattr(error.certificate_error, "verify_message", "") or error.certificate_error
        return (
            f"TLS certificate not trusted ({reason})",
            "set CA File to the CA that issued the server's certificate, or turn off Verify TLS",
        )
    if isinstance(error, aiohttp.ClientConnectorError):
        os_error = error.os_error
        if isinstance(os_error, ssl.SSLError):
            reason = os_error.reason or str(os_error)
            return f"TLS handshake failed ({reason})", "check the Base URL scheme and TLS files"
        if isinstance(os_error, ConnectionRefusedError):
            return "connection refused", check
        if isinstance(os_error, socket.gaierror):
            return f"cannot resolve host '{error.host}'", check
        return (os_error.strerror or str(os_error)).lower(), check
    if isinstance(error, aiohttp.ServerDisconnectedError):
        return "server closed the connection", check
    return (str(error) or type(error).__name__), check


class _ServerLog(logging.LoggerAdapter):
    """Prefixes every record with the server name; several servers share one log."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        return f"[{self.extra['server']}] {msg}", kwargs


class HttpClient:
    """One server: its session, poll cycle, trace events and health."""

    def __init__(
        self,
        name: str,
        base_url: str,
        paths: Sequence[str] = (),
        endpoint_map: EndpointMap | None = None,
        poll_interval: float = 1.0,
        timeout: float = 5.0,
        verify_tls: bool = True,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """Raises ValueError on paths that trace under no name or a name taken twice."""
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.endpoint_map = endpoint_map
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.verify_tls = verify_tls
        self._ssl = ssl_context
        self._log = _ServerLog(logger, {"server": name})

        self.paths = list(dict.fromkeys(paths))
        self._path_events: dict[str, str] = {}
        map_events = set(endpoint_map.events) if endpoint_map else set()
        for path in self.paths:
            event = event_name(path)
            if not path.startswith("/") or not event:
                raise ValueError(f"path '{path}' must start with '/' and name a resource")
            if event in map_events:
                raise ValueError(f"path '{path}' and the endpoint map both trace event '{event}'")
            other = next((p for p, e in self._path_events.items() if e == event), None)
            if other:
                raise ValueError(f"paths '{other}' and '{path}' both trace as event '{event}'")
            self._path_events[path] = event

        # (method, path) -> the map's (event, endpoint)s it feeds: one request per cycle.
        self._mapped: dict[tuple[str, str], list[tuple[str, Endpoint]]] = {}
        self._last: dict[str, dict[str, Any]] = {}  # map event -> its fields' last values
        for event, endpoints in (endpoint_map.events if endpoint_map else {}).items():
            for ep in endpoints:
                self._mapped.setdefault((ep.method, ep.path), []).append((event, ep))
        self._requests = list(dict.fromkeys([*self._mapped, *(("GET", p) for p in self.paths)]))

        self._session: aiohttp.ClientSession | None = None
        self._source: zelos_sdk.TraceSource | None = None
        self._event_prefix = ""
        self._events: dict[str, zelos_sdk.TraceSourceEvent] = {}
        # Discovered event -> {field: kind}, fixed by its first response.
        self._kinds: dict[str, dict[str, str]] = {}
        # Event -> the path that traces it ("" for the endpoint map).
        self._owner: dict[str, str] = dict.fromkeys(map_events, "")
        # Warn-once keys; cleared when the server comes back, so each outage reports afresh.
        self._warned: set[str] = set()

        self.contacted = asyncio.Event()  # set after the first cycle
        self.reachable = False
        self.ever_reachable = False
        self.last_status: int | None = None
        self.latency_ms: float | None = None
        self.last_error = ""
        self.fix = ""
        self.errors = 0
        self.poll_count = 0

    # ─── Trace ──────────────────────────────────────────────────────────────

    def start(self, shared: zelos_sdk.TraceSource | None = None) -> None:
        """Declare the health and map events.

        Args:
            shared: The prefix's source, events nested as `<server>/<event>`;
                None for a source of this server's own with unprefixed events
        """
        source_name, self._event_prefix = trace_layout(shared.name if shared else "", self.name)
        self._source = shared or zelos_sdk.TraceSource(source_name)
        self._declare(HEALTH_EVENT, list(HEALTH_FIELDS))
        for event, endpoints in (self.endpoint_map.events if self.endpoint_map else {}).items():
            self._declare(
                event, [(ep.name, SDK_DATATYPES[ep.datatype], ep.unit) for ep in endpoints]
            )
        if not self.verify_tls:
            self._log.warning(
                "TLS certificate verification is off: the server is not authenticated"
            )

    def _declare(self, event: str, fields: list[tuple[str, zelos_sdk.DataType, str]]) -> None:
        assert self._source is not None, "start() first"
        self._events[event] = self._source.add_event(
            f"{self._event_prefix}{event}",
            [zelos_sdk.TraceEventFieldMetadata(*f) for f in fields],
        )

    def _emit(self, event: str, row: dict[str, Any], time_ns: int) -> None:
        try:
            self._events[event].log_at(time_ns, **row)
        except (ValueError, TypeError, RuntimeError) as e:
            self._warn_once(f"emit:{event}", "Event '%s' not traced: %s", event, e)

    def _warn_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self._log.warning(msg, *args)

    # ─── Polling ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Poll every interval until cancelled; while unreachable, back off 3 s doubling to 60 s."""
        self._session = aiohttp.ClientSession(
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(ssl=self._ssl if self._ssl is not None else True),
        )
        backoff = RECONNECT_INITIAL
        try:
            while True:
                started = time.monotonic()
                up = await self.poll_once()
                self.contacted.set()
                if up:
                    backoff = RECONNECT_INITIAL
                    delay = self.poll_interval - (time.monotonic() - started)
                else:
                    delay, backoff = backoff, min(backoff * 2, RECONNECT_MAX)
                await asyncio.sleep(max(delay, 0.0))
        finally:
            session, self._session = self._session, None
            await session.close()

    async def poll_once(self) -> bool:
        """One cycle: each distinct request once, in order; True if any got an HTTP answer.

        A failed connect is the host's, not one path's: it ends the cycle. A path that
        answers badly or times out is warned once and polled again next cycle.
        """
        answered: list[tuple[int, float]] = []  # (status, seconds)
        failed: list[tuple[str, BaseException]] = []
        for method, path in self._requests:
            sent = time.monotonic()
            try:
                status, _, body = await self._fetch(method, path)
            except aiohttp.ClientConnectorError as e:
                self.errors += 1
                failed.append((path, e))
                break
            except (aiohttp.ClientError, TimeoutError) as e:
                self.errors += 1
                failed.append((path, e))
                continue
            answered.append((status, time.monotonic() - sent))
            self._handle(method, path, status, body, time.time_ns())

        self.poll_count += 1
        if answered:
            if self.ever_reachable and not self.reachable:
                self._log.info("Reachable again")
            elif not self.ever_reachable:
                self._log.info(
                    "Polling %d requests every %g s", len(self._requests), self.poll_interval
                )
            self.reachable = self.ever_reachable = True
            self.last_error = self.fix = ""
            self.last_status = answered[-1][0]
            self.latency_ms = max(seconds for _, seconds in answered) * 1000.0
            for path, e in failed:
                self._warn_once(
                    f"path:{path}", "Path '%s': %s; still polled", path, self._describe(e)[0]
                )
        else:
            self.last_error, self.fix = self._describe(failed[-1][1] if failed else None)
            if self.reachable:
                self._log.warning("Unreachable: %s; retrying with backoff", self.last_error)
                self._warned.clear()
            self.reachable = False
        self._log_health(bool(answered))
        return bool(answered)

    def _describe(self, error: BaseException | None) -> tuple[str, str]:
        return describe_error(error, self.timeout)

    def _log_health(self, answered: bool) -> None:
        row: dict[str, Any] = {"reachable": answered, "errors": min(self.errors, _UINT32_MAX)}
        if answered:  # no answer: status and latency are gaps, not zeros
            row["last_status"] = self.last_status
            row["latency_ms"] = self.latency_ms
        self._emit(HEALTH_EVENT, row, time.time_ns())

    async def _fetch(
        self, method: str, path: str, body: Any = None
    ) -> tuple[int, dict[str, str], bytes]:
        if self._session is None:
            raise RuntimeError(f"Server '{self.name}' is not running")
        kwargs = {} if body is None else {"json": body}
        async with self._session.request(method, self.base_url + path, **kwargs) as resp:
            headers = {k: resp.headers[k] for k in RESPONSE_HEADERS if k in resp.headers}
            return resp.status, headers, await resp.read()

    def _handle(self, method: str, path: str, status: int, body: bytes, received_ns: int) -> None:
        if not 200 <= status < 300:
            self.errors += 1
            self._warn_once(f"path:{path}", "Path '%s': HTTP %d; still polled", path, status)
            return
        try:
            data = json.loads(body)
        except ValueError:
            self.errors += 1
            self._warn_once(f"path:{path}", "Path '%s': response is not JSON; still polled", path)
            return
        if (method, path) in self._mapped:
            self._trace_mapped(method, path, data, received_ns)
        if method == "GET" and path in self._path_events:
            self._trace_discovered(path, data, received_ns)

    def _trace_mapped(self, method: str, path: str, data: Any, received_ns: int) -> None:
        """One row per event the response maps into, every field carried: the ones this
        response sets, the rest (endpoints on other paths) at their last value this
        session, as OPC UA does, so a latest-value read of the event is whole."""
        rows: dict[str, dict[str, Any]] = {}
        for event, ep in self._mapped[(method, path)]:
            value = coerce_value(extract_json_value(data, ep.json_path), ep.datatype, ep.scale)
            if value is None:
                self._warn_once(
                    f"map:{event}/{ep.name}",
                    "Endpoint '%s/%s': no %s value at json_path '%s' of %s; still polled",
                    event,
                    ep.name,
                    ep.datatype,
                    ep.json_path,
                    path,
                )
            else:
                rows.setdefault(event, {})[ep.name] = value
        for event, row in rows.items():
            last = self._last.setdefault(event, {})
            last.update(row)
            self._emit(event, last, received_ns)

    def _trace_discovered(self, path: str, data: Any, received_ns: int) -> None:
        """Declare each event at its first response; later changes to it are warned once.

        A declared schema is fixed per trace source, so a field that first appears
        later, or changes kind, is not traced until restart rather than re-declared.
        """
        rows, notes = flatten(self._path_events[path], data)
        for key, note in notes:
            self._warn_once(f"note:{key}", "%s", note)
        for event, fields in rows.items():
            owner = self._owner.setdefault(event, path)
            if owner != path:
                self._warn_once(
                    f"owner:{event}:{path}",
                    "Event '%s' of %s is already traced from %s; not traced",
                    event,
                    path,
                    owner or "the endpoint map",
                )
                continue
            kinds = self._kinds.get(event)
            if kinds is None:
                kinds = self._kinds[event] = {f: kind_of(v) for f, v in fields.items()}
                self._declare(event, [(f, DISCOVERED_TYPES[k], "") for f, k in kinds.items()])
            row = {}
            for field, value in fields.items():
                kind = kind_of(value)
                if field not in kinds:
                    self._warn_once(
                        f"new:{event}.{field}",
                        "Event '%s': field '%s' appeared after the first response; "
                        "traced after a restart",
                        event,
                        field,
                    )
                elif kinds[field] != kind:
                    self._warn_once(
                        f"kind:{event}.{field}",
                        "Event '%s': field '%s' is now %s, was %s; not traced",
                        event,
                        field,
                        kind,
                        kinds[field],
                    )
                else:
                    row[field] = float(value) if kind == NUMBER else value
            if row:
                self._emit(event, row, received_ns)

    # ─── Action support (coroutines run in the polling loop) ────────────────

    async def request(
        self, method: str, path: str, body: Any = None
    ) -> tuple[int, dict[str, str], bytes]:
        """One request on this server's session: (status, headers, body).

        Raises:
            RuntimeError: No HTTP answer, or a status of 300 and up
        """
        try:
            status, headers, raw = await self._fetch(method, path, body)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise RuntimeError(f"{method} {path}: {self._describe(e)[0]}") from e
        if status >= 300:
            text = raw.decode(errors="replace").strip()
            raise RuntimeError(f"{method} {path} answered HTTP {status}: {text[:200]}")
        return status, headers, raw

    async def read_endpoint(self, ep: Endpoint) -> Any:
        """The endpoint's current value, coerced and scaled as it is traced."""
        _, _, raw = await self.request(ep.method, ep.path)
        found = extract_json_value(parse_json(raw, ep.path), ep.json_path)
        value = coerce_value(found, ep.datatype, ep.scale)
        if value is None:
            raise ValueError(
                f"No {ep.datatype} value at json_path '{ep.json_path}' of {ep.path}: got {found!r}"
            )
        return value

    async def read_path(self, path: str) -> dict[str, Any]:
        """GET a path: the events its response traces as."""
        status, _, raw = await self.request("GET", path)
        event = self._path_events.get(path) or event_name(path)
        rows, notes = flatten(event, parse_json(raw, path))
        return {"status": status, "events": rows, "notes": [note for _, note in notes]}

    async def write_endpoint(self, ep: Endpoint, value: Any) -> int:
        """PUT `value` (map units) to the endpoint; `{json_path: value}` when it has one.

        Raises:
            TimeoutError: No answer in time: the server may still have applied the PUT
        """
        raw = encode_value(value, ep.datatype, ep.scale)
        try:
            status, _, _ = await self.request(
                "PUT", ep.path, {ep.json_path: raw} if ep.json_path else raw
            )
        except RuntimeError as e:
            if isinstance(e.__cause__, TimeoutError):
                raise TimeoutError(
                    f"PUT {ep.path} got no response within {self.timeout:g} s; it may have "
                    "reached the server and been applied - read the endpoint to confirm"
                ) from e
            raise
        return status

    # ─── Status ─────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        """ok, connecting (before the first cycle) or unreachable."""
        if self.reachable:
            return "ok"
        return "unreachable" if self.contacted.is_set() else "connecting"

    def status(self) -> dict[str, Any]:
        """Reachability and counters."""
        return {
            "server": self.name,
            "base_url": self.base_url,
            "state": self.state,
            "reachable": self.reachable,
            "last_status": self.last_status,
            "latency_ms": self.latency_ms,
            "last_error": self.last_error,
            "errors": self.errors,
            "poll_count": self.poll_count,
            "poll_interval": self.poll_interval,
            "timeout": self.timeout,
            "verify_tls": self.verify_tls,
            "endpoints": len(self.endpoint_map.endpoints) if self.endpoint_map else 0,
            "paths": len(self.paths),
        }

    def listing(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(mapped endpoints, discovered paths with the fields traced so far and their kinds)."""
        endpoints = [
            {
                "server": self.name,
                "event": event,
                "name": ep.name,
                "path": ep.path,
                "method": ep.method,
                "json_path": ep.json_path,
                "datatype": ep.datatype,
                "unit": ep.unit,
                "scale": ep.scale,
                "writable": ep.writable,
            }
            for event, eps in (self.endpoint_map.events if self.endpoint_map else {}).items()
            for ep in eps
        ]
        paths = [
            {
                "server": self.name,
                "path": path,
                "event": event,
                "events": {e: dict(k) for e, k in self._kinds.items() if self._owner[e] == path},
            }
            for path, event in self._path_events.items()
        ]
        return endpoints, paths


class HttpRunner:
    """The one event loop every server polls in: signals, stop, action dispatch.

    Shutdown cancels every server at once: it costs SHUTDOWN_TIMEOUT in total, not per server.
    """

    def __init__(self, clients: Sequence[HttpClient], retry_unreachable: bool = False) -> None:
        """`clients`: one per server, names unique (checked at config load)."""
        self.clients = {c.name: c for c in clients}
        self.retry_unreachable = retry_unreachable
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        # Servers unreachable at start, without retry: the caller exits 1.
        self.failed_at_start: list[HttpClient] = []

    def run(self) -> None:
        """Run every server until shutdown (blocking)."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        remove_signal_handlers = self._install_signal_handlers()
        tasks = [asyncio.create_task(c.run()) for c in self.clients.values()]
        stop = asyncio.ensure_future(self._stop.wait())
        contact = asyncio.ensure_future(
            asyncio.gather(*(c.contacted.wait() for c in self.clients.values()))
        )
        try:
            await asyncio.wait({contact, stop, *tasks}, return_when=asyncio.FIRST_COMPLETED)
            if contact.done() and not self._stop_at_start():
                # Until stopped, or a server task crashed (a bug).
                await asyncio.wait({stop, *tasks}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            contact.cancel()
            stop.cancel()
            for task in tasks:
                task.cancel()
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT)
                if pending:
                    logger.warning(
                        "%d server(s) still stopping after %g s", len(pending), SHUTDOWN_TIMEOUT
                    )
            remove_signal_handlers()
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception():
                raise task.exception()

    def _stop_at_start(self) -> bool:
        """After every server's first cycle, one ERROR per server that got no answer.

        First contact is required unless `retry_unreachable`: a server that is not
        there at start is a config error, fixed before starting again. Once a server
        has answered, an outage goes through the backoff.
        """
        down = [c for c in self.clients.values() if not c.reachable]
        for c in down:
            then = (
                "retrying with backoff while the other servers poll"
                if self.retry_unreachable
                else f"{c.fix}, then start the extension again"
            )
            logger.error(
                "Server '%s' (%s): cannot connect: %s; %s", c.name, c.base_url, c.last_error, then
            )
        if down and not self.retry_unreachable:
            self.failed_at_start = down
            return True
        return False

    def stop(self) -> None:
        """Request shutdown. Safe from any thread, including a signal handler."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

    def _install_signal_handlers(self) -> Callable[[], None]:
        """Set the stop event on SIGTERM/SIGINT; returns the uninstaller.

        sys.exit from a signal.signal handler would skip closing the sessions.
        """
        loop, event = self._loop, self._stop
        assert loop is not None

        def request_stop() -> None:
            logger.info("Shutdown signal received, stopping")
            event.set()

        installed: list[int] = []
        replaced: dict[int, Any] = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, request_stop)
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows, or off the main thread: signal.signal where it works.
                with contextlib.suppress(ValueError, OSError):
                    replaced[sig] = signal.signal(
                        sig, lambda *_: loop.call_soon_threadsafe(request_stop)
                    )

        def remove() -> None:
            for sig in installed:
                with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                    loop.remove_signal_handler(sig)
            for sig, prior in replaced.items():
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(sig, prior if prior is not None else signal.SIG_DFL)

        return remove

    def run_coro(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        """Run an action's coroutine in the polling loop, on its server's session.

        Raises:
            RuntimeError: If the polling loop is not running
            TimeoutError: If the coroutine outlives `timeout`, having cancelled it
        """
        if self._loop is None or not self._loop.is_running():
            coro.close()
            raise RuntimeError("extension is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise
