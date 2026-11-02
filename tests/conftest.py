"""Fixtures: the weather station simulator on a loopback port, and clients built
through the app's own config resolution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pyarrow as pa
import pytest
import zelos_sdk

from zelos_extension_http.cli import app
from zelos_extension_http.client import HttpClient, HttpRunner
from zelos_extension_http.demo.simulator import DemoServer

DEMO_MAP = str(app.get_demo_map_path())


@pytest.fixture
async def sim() -> AsyncIterator[DemoServer]:
    async with DemoServer(port=0) as server:
        yield server


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_until(predicate: Callable[[], bool], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"Condition not met within {timeout}s")
        await asyncio.sleep(0.05)


def clients_for(servers: list[dict[str, Any]], prefix: str = "HTTP") -> list[HttpClient]:
    """Clients through the app's own resolution, started on the prefix's layout."""
    config = {"servers": servers, "advanced": {"timeout": 2.0}}
    clients = app.build_clients(app.resolve_servers(config, app.resolve_advanced(config)))
    shared = zelos_sdk.TraceSource(prefix) if prefix else None
    for client in clients:
        client.start(shared)
    return clients


@contextlib.asynccontextmanager
async def running(runner: HttpRunner) -> AsyncIterator[HttpRunner]:
    task = asyncio.create_task(runner._run_async())
    try:
        yield runner
    finally:
        runner.stop()
        await asyncio.wait_for(task, 10.0)


def logged(caplog: pytest.LogCaptureFixture, level: int = logging.WARNING) -> list[str]:
    """The extension's own records at `level` exactly."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == level and r.name.startswith("zelos_extension_http")
    ]


def read_trace(path: str) -> dict[tuple[str, str], dict[str, list[Any]]]:
    """(source, event) -> field -> values, from a recorded .trz."""
    out: dict[tuple[str, str], dict[str, list[Any]]] = {}
    with zelos_sdk.TraceReader(path) as reader:
        segments = [s.id for s in reader.list_data_segments()]
        span = reader.time_range()
        for source in reader.list_fields():
            for event in source.events:
                fields = [f.path for f in event.fields]
                result = reader.query(segments, fields, span.start, span.end)
                table = pa.ipc.open_stream(result.to_arrow()).read_all()
                out[(source.name, event.name)] = {
                    column.rsplit(".", 1)[-1]: table.column(column).to_pylist()
                    for column in table.column_names
                    if column not in ("time_s", "time_ns")
                }
    return out
