"""Simulated weather station HTTP API: demo mode, `demo-server` and the tests.

A local aiohttp REST server with sensor data that changes over time. Its
responses hold a nested object (`wind`), an array (`cell_voltages`) and strings,
so discovery has each shape to trace.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
import signal
import ssl
import threading
import time

from aiohttp import web

logger = logging.getLogger(__name__)


class WeatherStationSimulator:
    """Simulates a remote weather station with environmental sensors."""

    def __init__(self) -> None:
        self.start_time = time.time()

        # Environmental baselines
        self.base_temperature = 22.0  # C
        self.base_humidity = 55.0  # %
        self.base_pressure = 1013.25  # hPa

        # Power system
        self.battery_voltage = 12.6  # V
        self.battery_soc = 85  # %

        # Config (writable)
        self.sample_rate = 1.0  # Hz
        self.alarm_threshold = 40.0  # C
        self.station_name = "STATION-001"

    def get_sensors(self) -> dict:
        """Get current environmental sensor readings."""
        t = time.time() - self.start_time

        # Temperature: diurnal cycle + noise
        temperature = self.base_temperature + 5.0 * math.sin(t * 0.01) + random.gauss(0, 0.3)

        # Humidity: inversely correlated with temperature + noise
        humidity = self.base_humidity - 3.0 * math.sin(t * 0.01) + random.gauss(0, 1.0)
        humidity = max(20.0, min(95.0, humidity))

        # Pressure: slow drift
        pressure = self.base_pressure + 2.0 * math.sin(t * 0.005) + random.gauss(0, 0.1)

        # Wind speed: gusty
        wind_base = 3.0 + 2.0 * math.sin(t * 0.02)
        wind_speed = max(0.0, wind_base + random.gauss(0, 0.5))
        direction = (180.0 + 40.0 * math.sin(t * 0.004) + random.gauss(0, 5.0)) % 360.0

        return {
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "pressure": round(pressure, 2),
            "wind_speed": round(wind_speed, 2),
            "wind": {
                "direction": round(direction, 1),
                "gust": round(wind_speed + abs(random.gauss(0, 1.0)), 2),
            },
            "timestamp": time.time(),
        }

    def get_power(self) -> dict:
        """Get power system status."""
        t = time.time() - self.start_time

        # Solar power follows a bell curve (daytime cycle)
        solar = max(0.0, 50.0 * math.sin(t * 0.008) + random.gauss(0, 2.0))

        # Battery voltage varies with SOC
        self.battery_soc = max(20, min(100, self.battery_soc + (solar - 15.0) * 0.001))
        self.battery_voltage = 11.0 + (self.battery_soc / 100.0) * 1.8

        cell = self.battery_voltage / 4
        return {
            "battery_voltage": round(self.battery_voltage, 2),
            "solar_watts": round(solar, 1),
            "soc": int(self.battery_soc),
            "charging": solar > 15.0,
            "cell_voltages": [round(cell + random.gauss(0, 0.01), 3) for _ in range(4)],
        }

    def get_system(self) -> dict:
        """Get system status."""
        t = time.time() - self.start_time
        cpu_temp = 45.0 + 5.0 * math.sin(t * 0.03) + random.gauss(0, 0.5)
        free_mem = max(100, 512 - int(t * 0.01) % 200 + random.randint(-10, 10))

        return {
            "uptime_seconds": int(t),
            "cpu_temp": round(cpu_temp, 1),
            "free_memory_mb": free_mem,
            "firmware_version": "1.2.3",
            "hostname": self.station_name,
        }

    def get_config(self) -> dict:
        """Get current configuration."""
        return {
            "sample_rate": self.sample_rate,
            "alarm_threshold": self.alarm_threshold,
            "station_name": self.station_name,
        }

    def update_config(self, data: dict) -> dict:
        """Update configuration with provided values."""
        updated = {}
        if "sample_rate" in data:
            self.sample_rate = float(data["sample_rate"])
            updated["sample_rate"] = self.sample_rate
        if "alarm_threshold" in data:
            self.alarm_threshold = float(data["alarm_threshold"])
            updated["alarm_threshold"] = self.alarm_threshold
        if "station_name" in data:
            self.station_name = str(data["station_name"])
            updated["station_name"] = self.station_name
        return updated


class DemoServer:
    """HTTP demo server wrapping the weather station simulator.

    `port=0` binds a free port; `port` holds the bound one once started.
    """

    def __init__(
        self, host: str = "127.0.0.1", port: int = 8090, ssl_context: ssl.SSLContext | None = None
    ) -> None:
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.simulator = WeatherStationSimulator()
        self._runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        """The base URL to configure."""
        return f"{'https' if self.ssl_context else 'http'}://{self.host}:{self.port}"

    def _create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/api/sensors", self._handle_sensors)
        app.router.add_get("/api/power", self._handle_power)
        app.router.add_get("/api/system", self._handle_system)
        app.router.add_get("/api/config", self._handle_config)
        app.router.add_put("/api/config", self._handle_config_update)
        app.router.add_get("/health", self._handle_health)
        return app

    async def _handle_sensors(self, request: web.Request) -> web.Response:
        return web.json_response(self.simulator.get_sensors())

    async def _handle_power(self, request: web.Request) -> web.Response:
        return web.json_response(self.simulator.get_power())

    async def _handle_system(self, request: web.Request) -> web.Response:
        return web.json_response(self.simulator.get_system())

    async def _handle_config(self, request: web.Request) -> web.Response:
        return web.json_response(self.simulator.get_config())

    async def _handle_config_update(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            updated = self.simulator.update_config(data)
            return web.json_response({"status": "ok", "updated": updated})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def start(self) -> None:
        """Start the server. Raises OSError when the port is taken."""
        self._runner = web.AppRunner(self._create_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port, ssl_context=self.ssl_context)
        try:
            await site.start()
        except OSError:
            await self._runner.cleanup()
            self._runner = None
            raise
        self.port = self._runner.addresses[0][1]
        logger.info("Demo HTTP server started on %s", self.url)

    async def stop(self) -> None:
        """Stop the server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Demo HTTP server stopped")

    async def __aenter__(self) -> DemoServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


def start_demo_server_thread(host: str, port: int) -> DemoServer:
    """The simulator on its own loop in a daemon thread, listening on return.

    Raises:
        RuntimeError: If it cannot bind, e.g. one already runs there
    """
    server = DemoServer(host=host, port=port)
    ready = threading.Event()
    failure: list[OSError] = []

    def run() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(server.start())
        except OSError as e:
            failure.append(e)
            ready.set()
            loop.close()
            return
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, name="demo-server", daemon=True).start()
    ready.wait(10.0)
    if failure:
        reason = failure[0].strerror or failure[0]
        raise RuntimeError(
            f"Could not start the simulator on {host}:{port} ({reason}); "
            "one may already be running there"
        )
    return server


async def run_sim(host: str, port: int) -> None:
    """The simulator in the foreground until SIGINT/SIGTERM. Raises OSError on a taken port."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop.set)
    async with DemoServer(host=host, port=port):
        await stop.wait()
