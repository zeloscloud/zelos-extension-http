"""Simulated weather station HTTP API for demo mode.

Uses aiohttp to run a local REST API server with realistic
sensor data that changes over time.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
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

        return {
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "pressure": round(pressure, 2),
            "wind_speed": round(wind_speed, 2),
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

        return {
            "battery_voltage": round(self.battery_voltage, 2),
            "solar_watts": round(solar, 1),
            "soc": int(self.battery_soc),
            "charging": solar > 15.0,
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
    """HTTP demo server wrapping the weather station simulator."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8090) -> None:
        self.host = host
        self.port = port
        self.simulator = WeatherStationSimulator()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

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
        """Start the server."""
        app = self._create_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"Demo HTTP server started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        logger.info("Demo HTTP server stopped")


async def run_demo_server(host: str = "127.0.0.1", port: int = 8090) -> None:
    """Run the demo HTTP server (blocking)."""
    server = DemoServer(host=host, port=port)
    await server.start()
    try:
        # Run forever
        while True:
            await asyncio.sleep(3600)
    finally:
        await server.stop()
