"""The real entry point: simulator -> `main.py --file` in app mode -> the recorded trace."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest
from conftest import DEMO_MAP, closed_port, read_trace

ROOT = Path(__file__).parents[1]
MAPPED = ("environment", "power", "system", "config")
DISCOVERED = ("api/sensors", "api/sensors/wind", "api/power", "api/system")


@pytest.mark.parametrize(
    ("prefix", "log_source", "source", "events"),
    [("HTTP", "HTTP", "HTTP", "wx/"), ("", "http_log", "wx", "")],
)
async def test_app_mode_records_the_trace_layout(sim, tmp_path, prefix, log_source, source, events):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "wx",
                        "base_url": sim.url,
                        "paths": ["/api/sensors", "/api/power", "/api/system"],
                        "endpoint_map_file": DEMO_MAP,
                        "poll_interval": 0.2,
                    }
                ],
                "advanced": {"prefix": prefix},
            }
        )
    )
    trz = tmp_path / "out.trz"
    env = {
        **os.environ,
        "ZELOS_CONFIG_PATH": str(config),
        # No agent: the recording is the only sink, and nothing reaches a local app.
        "ZELOS_AGENT_URL": f"http://127.0.0.1:{closed_port()}",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "main.py",
        "--file",
        str(trz),
        cwd=ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output: list[str] = []
    async with asyncio.timeout(15.0):
        while not any("Polling" in line for line in output):
            line = await proc.stdout.readline()
            assert line, "".join(output)
            output.append(line.decode())
    await asyncio.sleep(1.0)
    proc.send_signal(signal.SIGINT)
    rest, _ = await asyncio.wait_for(proc.communicate(), 10.0)
    assert proc.returncode == 0, "".join(output) + rest.decode()

    trace = read_trace(str(trz))
    assert set(trace) == {
        (log_source, "log"),
        (source, f"{events}_server"),
        *((source, f"{events}{e}") for e in (*MAPPED, *DISCOVERED)),
    }

    def event(name: str) -> dict[str, list]:
        return trace[(source, f"{events}{name}")]

    health = event("_server")
    assert len(health["reachable"]) >= 3
    assert set(health["reachable"]) == {True} and set(health["last_status"]) == {200}
    assert all(10.0 < t < 35.0 for t in event("environment")["temperature"])
    assert set(event("config")["station_name"]) == {"STATION-001"}
    assert all(0.0 <= d < 360.0 for d in event("api/sensors/wind")["direction"])
    assert {f"cell_voltages_{i}" for i in range(4)} <= set(event("api/power"))
    assert set(event("api/system")["firmware_version"]) == {"1.2.3"}
    messages = trace[(log_source, "log")]["message"]
    # Map and paths share URLs: four requests a cycle, not seven.
    assert "[wx] Polling 4 requests every 0.2 s" in messages
    assert (
        "Trace prefix: HTTP" if prefix else "Trace prefix cleared: one trace source per server"
    ) in messages
