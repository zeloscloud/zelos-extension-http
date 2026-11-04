"""Servers end to end against the in-process weather station simulator."""

from __future__ import annotations

import asyncio
import logging
import ssl
import time

import aiohttp
import pytest
import trustme
from aiohttp import web
from conftest import DEMO_MAP, clients_for, closed_port, logged, running, wait_until

from zelos_extension_http import actions
from zelos_extension_http.autoconfig import NO_SERVERS
from zelos_extension_http.client import SHUTDOWN_TIMEOUT, HttpClient, HttpRunner
from zelos_extension_http.demo.simulator import DemoServer
from zelos_extension_http.endpoint_map import EndpointMap

PATHS = ["/api/sensors", "/api/power"]


async def test_unreachable_at_start_stops_the_extension(sim, caplog):
    """First contact is required: a closed port stops every server with one ERROR."""
    gone = f"http://127.0.0.1:{closed_port()}"
    runner = HttpRunner(
        clients_for(
            [
                {"name": "wx", "base_url": sim.url, "paths": PATHS},
                {"name": "gone", "base_url": gone, "paths": PATHS},
            ]
        )
    )
    await asyncio.wait_for(runner._run_async(), 10.0)
    assert logged(caplog, logging.ERROR) == [
        f"Server 'gone' ({gone}): cannot connect: connection refused; check the Base URL and "
        "that the server is running, then start the extension again"
    ]
    assert runner.failed_at_start == [runner.clients["gone"]]
    assert runner.clients["wx"].reachable


async def test_retry_unreachable_then_outage_backs_off_and_recovers(caplog):
    """With retry, a server absent at start is polled once it appears; an outage after that
    warns once, backs off, and recovers on the same port."""
    caplog.set_level(logging.INFO)
    port = closed_port()
    (client,) = clients_for(
        [{"name": "late", "base_url": f"http://127.0.0.1:{port}", "paths": PATHS}]
    )
    client.poll_interval = 0.1
    async with running(HttpRunner([client], retry_unreachable=True)):
        await wait_until(client.contacted.is_set, 5.0)
        assert logged(caplog, logging.ERROR) == [
            f"Server 'late' (http://127.0.0.1:{port}): cannot connect: connection refused; "
            "retrying with backoff while the other servers poll"
        ]
        async with DemoServer(port=port):
            await wait_until(lambda: client.reachable, 5.0)  # the first backoff is 3 s
            polled = client.poll_count
            await wait_until(lambda: client.poll_count >= polled + 3, 5.0)
        await wait_until(lambda: not client.reachable, 5.0)
        async with DemoServer(port=port):
            await wait_until(lambda: client.reachable, 5.0)
    assert logged(caplog, logging.WARNING) == [
        "[late] Unreachable: connection refused; retrying with backoff"
    ]
    assert "[late] Reachable again" in logged(caplog, logging.INFO)


async def test_a_failing_path_warns_once_and_keeps_polling(sim, caplog):
    (client,) = clients_for(
        [{"name": "wx", "base_url": sim.url, "paths": ["/api/sensors", "/missing", "/health"]}]
    )
    client.poll_interval = 0.05
    async with running(HttpRunner([client])):
        await wait_until(lambda: client.poll_count >= 5, 5.0)
    assert logged(caplog, logging.WARNING) == ["[wx] Path '/missing': HTTP 404; still polled"]
    assert not [r for r in caplog.records if r.name.startswith("aiohttp")]  # no access lines
    assert client.reachable and client.errors >= 5
    assert {"_server", "api/sensors", "api/sensors/wind", "health"} == set(client._events)


async def test_actions_dispatch_into_the_polling_loop(sim):
    a, b = clients_for(
        [
            {"name": "wx", "base_url": sim.url, "paths": PATHS, "endpoint_map_file": DEMO_MAP},
            {"name": "wx2", "base_url": sim.url, "paths": ["/api/system"]},
        ]
    )
    runner = HttpRunner([a, b])
    actions.set_runner(runner)
    task = asyncio.create_task(runner._run_async())

    def call(action, *args):
        return asyncio.to_thread(action, *args)

    try:
        await wait_until(lambda: a.poll_count >= 1 and b.poll_count >= 1, 5.0)
        read = await call(actions.read_endpoint, "temperature", "", "wx")
        assert read["event"] == "environment" and 10.0 < read["value"] < 35.0
        read = await call(actions.read_endpoint, "", "/api/sensors", "wx")
        assert set(read["events"]) == {"api/sensors", "api/sensors/wind"}

        written = await call(actions.write_endpoint, "alarm_threshold", "45.5", "wx")
        assert (written["value"], written["status"]) == (45.5, 200)
        assert (await call(actions.read_endpoint, "config/alarm_threshold", "", "wx"))[
            "value"
        ] == 45.5
        with pytest.raises(ValueError, match="Unknown endpoint 'temp'. Use list_endpoints"):
            await call(actions.read_endpoint, "temp", "", "wx")
        with pytest.raises(ValueError, match="'temperature' is not writable"):
            await call(actions.write_endpoint, "temperature", "1", "wx")
        with pytest.raises(ValueError, match="Cannot parse 'warm' as float32"):
            await call(actions.write_endpoint, "alarm_threshold", "warm", "wx")

        sent = await call(actions.send_request, "PUT", "/api/config", '{"sample_rate": 2}', "wx")
        assert sent["status"] == 200 and sent["body"]["updated"] == {"sample_rate": 2.0}
        assert sent["headers"]["Content-Type"].startswith("application/json")
        with pytest.raises(RuntimeError, match="GET /nope answered HTTP 404"):
            await call(actions.send_request, "GET", "/nope", "", "wx")
        with pytest.raises(ValueError, match="Body is not JSON"):
            await call(actions.send_request, "POST", "/api/config", "{bad", "wx")

        listing = actions.list_endpoints("wx")
        assert len(listing["endpoints"]) == 13
        sensors = next(p for p in listing["paths"] if p["path"] == "/api/sensors")
        assert sensors["events"]["api/sensors/wind"] == {"direction": "float64", "gust": "float64"}
        assert actions.list_endpoints()["count"] == 13 + 3
        assert actions.get_status()["count"] == 2
        status = actions.get_status("wx2")
        assert (status["state"], status["last_status"]) == ("ok", 200)

        with pytest.raises(ValueError, match="set server to one of: wx, wx2"):
            await call(actions.read_endpoint, "temperature")
        with pytest.raises(ValueError, match="Unknown server 'x'. Servers: wx, wx2"):
            actions.get_status("x")
        with pytest.raises(ValueError, match="No endpoint map on server 'wx2'"):
            await call(actions.read_endpoint, "temperature", "", "wx2")
    finally:
        actions.set_runner(None)
        started = time.monotonic()
        runner.stop()
        await asyncio.wait_for(task, 10.0)
        elapsed = time.monotonic() - started
    assert elapsed < SHUTDOWN_TIMEOUT


async def test_a_write_without_an_answer_says_it_may_have_applied():
    async def stall(request: web.Request) -> web.Response:
        await asyncio.sleep(1.0)  # past the 0.5 s timeout
        return web.json_response({})

    app = web.Application()
    app.router.add_put("/setpoint", stall)
    server = web.AppRunner(app)
    await server.setup()
    port = closed_port()
    await web.TCPSite(server, "127.0.0.1", port).start()
    endpoint_map = EndpointMap.from_dict(
        {"events": {"ctl": [{"name": "sp", "path": "/setpoint", "writable": True}]}}
    )
    client = HttpClient("plc", f"http://127.0.0.1:{port}", endpoint_map=endpoint_map, timeout=0.5)
    client._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=client.timeout))
    try:
        with pytest.raises(TimeoutError, match="may have reached the server and been applied"):
            await client.write_endpoint(endpoint_map.events["ctl"][0], 1.0)
    finally:
        await client._session.close()
        await server.cleanup()


def test_a_mapped_row_carries_the_fields_other_paths_set():
    """An event whose endpoints sit on two paths: each response's row carries the other
    path's fields at their last value, so the event's latest values read whole."""
    endpoint_map = EndpointMap.from_dict(
        {
            "events": {
                "env": [
                    {"name": "temp", "path": "/api/sensors", "json_path": "temperature"},
                    {"name": "volts", "path": "/api/power", "json_path": "battery_voltage"},
                ]
            }
        }
    )
    client = HttpClient("wx", "http://127.0.0.1:1", endpoint_map=endpoint_map)
    rows: list[tuple[str, dict]] = []
    client._emit = lambda event, row, _t: rows.append((event, dict(row)))  # type: ignore[method-assign]
    client._trace_mapped("GET", "/api/sensors", {"temperature": 21.5}, 1)
    client._trace_mapped("GET", "/api/power", {"battery_voltage": 12.6}, 2)
    client._trace_mapped("GET", "/api/sensors", {"temperature": 22.0}, 3)
    assert rows == [
        ("env", {"temp": 21.5}),
        ("env", {"temp": 21.5, "volts": 12.6}),
        ("env", {"temp": 22.0, "volts": 12.6}),
    ]


def test_actions_need_a_running_extension():
    actions.set_runner(HttpRunner([HttpClient("wx", "http://127.0.0.1:1", paths=["/x"])]))
    try:
        assert actions.get_status()["servers"][0]["state"] == "connecting"
        with pytest.raises(RuntimeError, match="extension is not running"):
            actions.read_endpoint(path="/x")
    finally:
        actions.set_runner(None)
    with pytest.raises(RuntimeError, match="No HTTP server is configured"):
        actions.get_status()


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    """A private CA, its PEM on disk, and a server context for 127.0.0.1 signed by it."""
    ca = trustme.CA()
    ca_file = tmp_path_factory.mktemp("tls") / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_file))
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert("127.0.0.1").configure_cert(server)
    return str(ca_file), server


@pytest.mark.parametrize(
    ("settings", "reachable", "warnings"),
    [
        ("ca_file", True, []),
        (
            "verify_off",
            True,
            ["[secure] TLS certificate verification is off: the server is not authenticated"],
        ),
        ("system_cas", False, []),
    ],
)
async def test_tls_verifies_the_server(tls, caplog, settings, reachable, warnings):
    ca_file, server_context = tls
    async with DemoServer(port=0, ssl_context=server_context) as sim:
        server = {"name": "secure", "base_url": sim.url, "paths": PATHS}
        server |= {"ca_file": {"ca_file": ca_file}, "verify_off": {"verify_tls": False}}.get(
            settings, {}
        )
        runner = HttpRunner(clients_for([server]))
        async with running(runner):
            await wait_until(runner.clients["secure"].contacted.is_set, 5.0)
    assert runner.clients["secure"].reachable is reachable
    assert logged(caplog, logging.WARNING) == warnings
    if not reachable:
        assert logged(caplog, logging.ERROR) == [
            f"Server 'secure' ({sim.url}): cannot connect: TLS certificate not trusted "
            "(unable to get local issuer certificate); set CA File to the CA that issued the "
            "server's certificate, or turn off Verify TLS, then start the extension again"
        ]


async def test_auto_config_checks_the_form_servers(sim):
    none = await asyncio.to_thread(actions.auto_config, {})
    assert none == {"status": "error", "message": NO_SERVERS}
    gone = f"http://127.0.0.1:{closed_port()}"
    servers = [
        {"name": "wx", "base_url": sim.url, "paths": ["/api/sensors"]},
        {"base_url": gone},
        {"base_url": "http://admin:s3cret@gw:8080"},
        {"base_url": sim.url + "/api/system"},
        {"name": "mapped", "base_url": sim.url, "endpoint_map_file": DEMO_MAP},
    ]
    result = await asyncio.to_thread(actions.auto_config, {"servers": servers})
    assert result["status"] == "success"
    assert result["config"] == {"servers": servers}  # kept as entered
    assert result["message"] == (
        f"Found wx at {sim.url}/api/sensors (HTTP 200, JSON; traces api/sensors, "
        f"api/sensors/wind); 127_0_0_1 at {sim.url}/api/system (HTTP 200, JSON; add a path "
        f"under Paths to trace it); mapped at {sim.url}/api/sensors (HTTP 200, JSON; its "
        f"endpoint map picks the signals). Couldn't connect to {gone} (connection refused); "
        "check the Base URL and that the server is running. gw: remove the user name / password "
        "from the "
        "Base URL; the extension sends no credentials."
    )
