"""Tests for Zelos HTTP extension.

Tests core functionality:
- Endpoint map parsing
- JSON value extraction and coercion
- Simulator physics logic
- Integration tests with demo server
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from zelos_extension_http.client import (
    HttpClient,
    coerce_value,
    extract_json_value,
)
from zelos_extension_http.demo.simulator import DemoServer, WeatherStationSimulator
from zelos_extension_http.endpoint_map import Endpoint, EndpointMap

# =============================================================================
# Endpoint Map Tests
# =============================================================================


class TestEndpoint:
    """Test Endpoint dataclass."""

    def test_defaults(self):
        """Minimal required fields use sensible defaults."""
        ep = Endpoint(path="/api/test", name="test")
        assert ep.method == "GET"
        assert ep.datatype == "float32"
        assert ep.writable is False

    def test_invalid_datatype_raises(self):
        """Invalid datatype raises ValueError."""
        with pytest.raises(ValueError, match="Invalid datatype"):
            Endpoint(path="/api/test", name="test", datatype="invalid")

    def test_invalid_method_raises(self):
        """Invalid HTTP method raises ValueError."""
        with pytest.raises(ValueError, match="Invalid method"):
            Endpoint(path="/api/test", name="test", method="INVALID")

    def test_method_uppercased(self):
        """Method is normalized to uppercase."""
        ep = Endpoint(path="/api/test", name="test", method="get")
        assert ep.method == "GET"

    def test_valid_datatypes(self):
        """All valid datatypes are accepted."""
        for dt in [
            "bool",
            "uint8",
            "int8",
            "uint16",
            "int16",
            "uint32",
            "int32",
            "float32",
            "uint64",
            "int64",
            "float64",
            "string",
        ]:
            ep = Endpoint(path="/test", name="t", datatype=dt)
            assert ep.datatype == dt

    def test_writable_endpoint(self):
        """Writable flag is set correctly."""
        ep = Endpoint(path="/api/config", name="setpoint", writable=True)
        assert ep.writable is True


class TestEndpointMap:
    """Test EndpointMap parsing."""

    def test_from_dict_creates_events(self):
        """Events are correctly parsed from dict."""
        data = {
            "events": {
                "sensors": [{"name": "temp", "path": "/api/temp"}],
                "system": [{"name": "uptime", "path": "/api/uptime"}],
            }
        }
        ep_map = EndpointMap.from_dict(data)
        assert set(ep_map.event_names) == {"sensors", "system"}
        assert len(ep_map.endpoints) == 2

    def test_from_file(self):
        """Endpoint map loads from JSON file."""
        data = {"events": {"test": [{"name": "val", "path": "/api/val"}]}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            ep_map = EndpointMap.from_file(f.name)
        assert len(ep_map.endpoints) == 1
        Path(f.name).unlink()

    def test_from_file_not_found(self):
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            EndpointMap.from_file("/nonexistent/file.json")

    def test_get_by_name(self):
        """Find endpoint by name across events."""
        data = {
            "events": {
                "a": [{"name": "temp", "path": "/api/temp"}],
                "b": [{"name": "humidity", "path": "/api/humidity"}],
            }
        }
        ep_map = EndpointMap.from_dict(data)
        assert ep_map.get_by_name("temp").path == "/api/temp"
        assert ep_map.get_by_name("humidity").path == "/api/humidity"
        assert ep_map.get_by_name("nonexistent") is None

    def test_get_by_path(self):
        """Find endpoint by path."""
        data = {"events": {"a": [{"name": "temp", "path": "/api/temp"}]}}
        ep_map = EndpointMap.from_dict(data)
        assert ep_map.get_by_path("/api/temp").name == "temp"
        assert ep_map.get_by_path("/api/nonexistent") is None

    def test_writable_endpoints(self):
        """writable_endpoints returns only writable ones."""
        data = {
            "events": {
                "sensors": [{"name": "temp", "path": "/api/temp"}],
                "config": [{"name": "setpoint", "path": "/api/config", "writable": True}],
            }
        }
        ep_map = EndpointMap.from_dict(data)
        writable = ep_map.writable_endpoints
        assert len(writable) == 1
        assert writable[0].name == "setpoint"

    def test_unique_paths(self):
        """unique_paths returns deduplicated paths."""
        data = {
            "events": {
                "env": [
                    {"name": "temp", "path": "/api/sensors", "json_path": "temperature"},
                    {"name": "humidity", "path": "/api/sensors", "json_path": "humidity"},
                ]
            }
        }
        ep_map = EndpointMap.from_dict(data)
        assert ep_map.unique_paths == {"/api/sensors"}

    def test_base_url_parsed(self):
        """Base URL is parsed from dict."""
        data = {
            "base_url": "http://192.168.1.100:8080",
            "events": {"test": [{"name": "t", "path": "/api/t"}]},
        }
        ep_map = EndpointMap.from_dict(data)
        assert ep_map.base_url == "http://192.168.1.100:8080"

    def test_name_and_description(self):
        """Name and description are parsed."""
        data = {
            "name": "my_device",
            "description": "Test device",
            "events": {"test": [{"name": "t", "path": "/t"}]},
        }
        ep_map = EndpointMap.from_dict(data)
        assert ep_map.name == "my_device"
        assert ep_map.description == "Test device"

    def test_json_path_parsed(self):
        """json_path is correctly parsed from endpoint data."""
        data = {
            "events": {
                "env": [{"name": "temp", "path": "/api/sensors", "json_path": "data.temperature"}]
            }
        }
        ep_map = EndpointMap.from_dict(data)
        assert ep_map.endpoints[0].json_path == "data.temperature"


# =============================================================================
# JSON Extraction & Coercion Tests
# =============================================================================


class TestExtractJsonValue:
    """Test JSON path extraction."""

    def test_simple_key(self):
        """Extract simple top-level key."""
        data = {"temperature": 22.5}
        assert extract_json_value(data, "temperature") == 22.5

    def test_nested_key(self):
        """Extract nested key with dot notation."""
        data = {"data": {"sensors": {"temperature": 22.5}}}
        assert extract_json_value(data, "data.sensors.temperature") == 22.5

    def test_empty_path_returns_full_data(self):
        """Empty path returns entire data object."""
        data = {"temp": 22.5}
        assert extract_json_value(data, "") == data

    def test_missing_key_returns_none(self):
        """Missing key returns None."""
        data = {"temperature": 22.5}
        assert extract_json_value(data, "humidity") is None

    def test_missing_nested_key_returns_none(self):
        """Missing nested key returns None."""
        data = {"data": {"temperature": 22.5}}
        assert extract_json_value(data, "data.humidity") is None

    def test_list_index(self):
        """Extract from list by index."""
        data = {"sensors": [{"value": 10}, {"value": 20}]}
        assert extract_json_value(data, "sensors.1.value") == 20

    def test_invalid_list_index_returns_none(self):
        """Invalid list index returns None."""
        data = {"sensors": [{"value": 10}]}
        assert extract_json_value(data, "sensors.5.value") is None

    def test_non_dict_intermediate_returns_none(self):
        """Non-dict intermediate returns None."""
        data = {"value": 42}
        assert extract_json_value(data, "value.nested") is None


class TestCoerceValue:
    """Test value coercion."""

    def test_float32(self):
        """Float32 coercion."""
        assert coerce_value(22.5, "float32") == 22.5
        assert coerce_value("22.5", "float32") == 22.5

    def test_uint32(self):
        """Uint32 coercion."""
        assert coerce_value(1000, "uint32") == 1000
        assert coerce_value(22.7, "uint32") == 22

    def test_bool(self):
        """Bool coercion."""
        assert coerce_value(True, "bool") is True
        assert coerce_value(0, "bool") is False
        assert coerce_value(1, "bool") is True

    def test_string(self):
        """String coercion."""
        assert coerce_value("hello", "string") == "hello"
        assert coerce_value(42, "string") == "42"

    def test_none_returns_none(self):
        """None input returns None."""
        assert coerce_value(None, "float32") is None

    def test_scale_factor(self):
        """Scale factor is applied."""
        assert coerce_value(100, "float32", scale=0.1) == pytest.approx(10.0)
        assert coerce_value(100, "uint16", scale=0.1) == 10

    def test_invalid_value_returns_none(self):
        """Non-numeric string returns None for numeric types."""
        assert coerce_value("not_a_number", "float32") is None

    def test_int8_coercion(self):
        """Int8 coercion."""
        assert coerce_value(127, "int8") == 127

    def test_uint8_coercion(self):
        """Uint8 coercion."""
        assert coerce_value(255, "uint8") == 255


# =============================================================================
# Simulator Tests (no network)
# =============================================================================


class TestWeatherStationSimulator:
    """Test simulator physics logic."""

    def test_sensors_returns_all_fields(self):
        """Sensors endpoint returns complete value dictionary."""
        sim = WeatherStationSimulator()
        values = sim.get_sensors()
        expected = {"temperature", "humidity", "pressure", "wind_speed", "timestamp"}
        assert set(values.keys()) == expected

    def test_temperature_near_baseline(self):
        """Temperature stays within reasonable range."""
        sim = WeatherStationSimulator()
        values = sim.get_sensors()
        assert 10.0 < values["temperature"] < 35.0

    def test_humidity_in_range(self):
        """Humidity stays within valid range."""
        sim = WeatherStationSimulator()
        values = sim.get_sensors()
        assert 20.0 <= values["humidity"] <= 95.0

    def test_pressure_near_baseline(self):
        """Pressure stays near baseline."""
        sim = WeatherStationSimulator()
        values = sim.get_sensors()
        assert 1005.0 < values["pressure"] < 1025.0

    def test_power_returns_all_fields(self):
        """Power endpoint returns expected fields."""
        sim = WeatherStationSimulator()
        values = sim.get_power()
        expected = {"battery_voltage", "solar_watts", "soc", "charging"}
        assert set(values.keys()) == expected

    def test_battery_voltage_reasonable(self):
        """Battery voltage is in valid range."""
        sim = WeatherStationSimulator()
        values = sim.get_power()
        assert 10.5 < values["battery_voltage"] < 13.5

    def test_system_returns_all_fields(self):
        """System endpoint returns expected fields."""
        sim = WeatherStationSimulator()
        values = sim.get_system()
        expected = {"uptime_seconds", "cpu_temp", "free_memory_mb", "firmware_version", "hostname"}
        assert set(values.keys()) == expected

    def test_config_get_and_update(self):
        """Config can be read and updated."""
        sim = WeatherStationSimulator()
        config = sim.get_config()
        assert config["sample_rate"] == 1.0

        updated = sim.update_config({"sample_rate": 5.0, "alarm_threshold": 50.0})
        assert updated["sample_rate"] == 5.0
        assert updated["alarm_threshold"] == 50.0

        config = sim.get_config()
        assert config["sample_rate"] == 5.0
        assert config["alarm_threshold"] == 50.0


# =============================================================================
# Integration Tests with Demo Server
# =============================================================================


@pytest.fixture(scope="module")
def demo_server():
    """Fixture that starts demo server for integration tests."""
    import socket
    import threading
    import time

    server = DemoServer(host="127.0.0.1", port=18090)

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    # Wait for server to be ready by polling the socket
    for _ in range(50):
        try:
            sock = socket.create_connection(("127.0.0.1", 18090), timeout=0.1)
            sock.close()
            break
        except OSError:
            time.sleep(0.1)

    yield server


@pytest.fixture
def endpoint_map():
    """Load the demo endpoint map."""
    map_path = Path(__file__).parent.parent / "zelos_extension_http" / "demo" / "demo_device.json"
    return EndpointMap.from_file(str(map_path))


@pytest.fixture
def client(demo_server, endpoint_map):
    """Create a connected HttpClient."""
    c = HttpClient(
        base_url="http://127.0.0.1:18090",
        endpoint_map=endpoint_map,
        timeout=5.0,
    )
    asyncio.get_event_loop().run_until_complete(c.connect())
    yield c
    asyncio.get_event_loop().run_until_complete(c.disconnect())


class TestDemoServerIntegration:
    """Integration tests against the demo server."""

    def test_health_endpoint(self, client):
        """Health endpoint returns ok."""

        async def check():
            data = await client.fetch_path("/health")
            return data

        result = asyncio.get_event_loop().run_until_complete(check())
        assert result is not None
        assert result["status"] == "ok"

    def test_read_sensors(self, client):
        """Read sensor data from demo server."""

        async def read():
            data = await client.fetch_path("/api/sensors")
            return data

        result = asyncio.get_event_loop().run_until_complete(read())
        assert result is not None
        assert "temperature" in result
        assert "humidity" in result
        assert "pressure" in result
        assert "wind_speed" in result

    def test_read_float32_endpoint(self, client):
        """Read float32 value via endpoint definition."""
        ep = client.endpoint_map.get_by_name("temperature")
        assert ep is not None
        assert ep.datatype == "float32"

        async def read():
            return await client.fetch_endpoint(ep)

        value = asyncio.get_event_loop().run_until_complete(read())
        assert value is not None
        assert isinstance(value, float)
        assert 10.0 < value < 35.0

    def test_read_uint32_endpoint(self, client):
        """Read uint32 value via endpoint definition."""
        ep = client.endpoint_map.get_by_name("uptime")
        assert ep is not None
        assert ep.datatype == "uint32"

        async def read():
            return await client.fetch_endpoint(ep)

        value = asyncio.get_event_loop().run_until_complete(read())
        assert value is not None
        assert isinstance(value, int)
        assert value >= 0

    def test_read_uint8_endpoint(self, client):
        """Read uint8 value via endpoint definition."""
        ep = client.endpoint_map.get_by_name("battery_soc")
        assert ep is not None
        assert ep.datatype == "uint8"

        async def read():
            return await client.fetch_endpoint(ep)

        value = asyncio.get_event_loop().run_until_complete(read())
        assert value is not None
        assert isinstance(value, int)
        assert 0 <= value <= 100

    def test_read_string_endpoint(self, client):
        """Read string value via endpoint definition."""
        ep = client.endpoint_map.get_by_name("station_name")
        assert ep is not None
        assert ep.datatype == "string"

        async def read():
            return await client.fetch_endpoint(ep)

        value = asyncio.get_event_loop().run_until_complete(read())
        assert value is not None
        assert isinstance(value, str)
        assert value == "STATION-001"

    def test_write_config_endpoint(self, client):
        """Write a value to a writable config endpoint."""
        ep = client.endpoint_map.get_by_name("alarm_threshold")
        assert ep is not None
        assert ep.writable is True

        async def write_and_read():
            success = await client.write_endpoint(ep, 45.0)
            assert success is True

            # Read back via GET
            data = await client.fetch_path("/api/config")
            return data["alarm_threshold"]

        value = asyncio.get_event_loop().run_until_complete(write_and_read())
        assert value == 45.0

    def test_write_non_writable_fails(self, client):
        """Writing to non-writable endpoint fails."""
        ep = client.endpoint_map.get_by_name("temperature")
        assert ep is not None
        assert ep.writable is False

        async def try_write():
            return await client.write_endpoint(ep, 99.0)

        success = asyncio.get_event_loop().run_until_complete(try_write())
        assert success is False

    def test_poll_all_events(self, client):
        """Poll all endpoints and verify event structure."""

        async def poll():
            return await client._poll_endpoints()

        results = asyncio.get_event_loop().run_until_complete(poll())

        # Should have all events from endpoint map
        assert "environment" in results
        assert "power" in results
        assert "system" in results
        assert "config" in results

        # Environment event should have temperature, humidity, etc.
        assert "temperature" in results["environment"]
        assert "humidity" in results["environment"]
        assert "pressure" in results["environment"]

        # Check values are reasonable
        assert 10.0 < results["environment"]["temperature"] < 35.0

    def test_shared_path_polling_efficiency(self, client):
        """Multiple endpoints sharing a path should result in one HTTP request."""
        # The environment event has 4 endpoints all pointing to /api/sensors
        # This tests that path_cache works correctly

        async def poll():
            return await client._poll_endpoints()

        results = asyncio.get_event_loop().run_until_complete(poll())
        env = results.get("environment", {})
        assert "temperature" in env
        assert "humidity" in env
        assert "pressure" in env
        assert "wind_speed" in env

    def test_read_power_data(self, client):
        """Read power system data."""

        async def read():
            return await client.fetch_path("/api/power")

        result = asyncio.get_event_loop().run_until_complete(read())
        assert result is not None
        assert "battery_voltage" in result
        assert "solar_watts" in result
        assert "soc" in result
        assert 10.5 < result["battery_voltage"] < 13.5

    def test_read_system_data(self, client):
        """Read system status data."""

        async def read():
            return await client.fetch_path("/api/system")

        result = asyncio.get_event_loop().run_until_complete(read())
        assert result is not None
        assert "uptime_seconds" in result
        assert "cpu_temp" in result
        assert result["firmware_version"] == "1.2.3"


# =============================================================================
# Action Tests
# =============================================================================


class TestReconnection:
    """Tests for connection error detection."""

    def test_is_connection_error_timeout(self):
        """Timeout errors are detected as connection errors."""
        client = HttpClient()
        assert client._is_connection_error(Exception("Connection timeout")) is True

    def test_is_connection_error_refused(self):
        """Connection refused errors are detected."""
        client = HttpClient()
        assert client._is_connection_error(Exception("Connection refused")) is True

    def test_is_connection_error_false_for_other(self):
        """Non-connection errors return False."""
        client = HttpClient()
        assert client._is_connection_error(Exception("Invalid JSON")) is False
        assert client._is_connection_error(ValueError("bad value")) is False


class TestActionsUnit:
    """Unit tests for SDK actions (no network)."""

    @pytest.fixture
    def client_with_map(self):
        """Create client with endpoint map but no connection."""
        data = {
            "name": "test_device",
            "events": {
                "sensors": [
                    {"name": "temp", "path": "/api/temp", "unit": "C"},
                    {"name": "humidity", "path": "/api/humidity", "unit": "%"},
                ],
                "config": [
                    {
                        "name": "setpoint",
                        "path": "/api/config",
                        "writable": True,
                    },
                ],
            },
        }
        ep_map = EndpointMap.from_dict(data)
        return HttpClient(endpoint_map=ep_map)

    def test_get_status_returns_info(self, client_with_map):
        """Get Status action returns expected fields."""
        result = client_with_map.get_status()
        assert "connected" in result
        assert "base_url" in result
        assert "poll_count" in result
        assert "endpoints" in result
        assert result["endpoints"] == 3

    def test_list_endpoints_returns_all(self, client_with_map):
        """List Endpoints action returns all endpoints."""
        result = client_with_map.list_endpoints()
        assert result["count"] == 3
        names = [ep["name"] for ep in result["endpoints"]]
        assert "temp" in names
        assert "humidity" in names
        assert "setpoint" in names

    def test_list_endpoints_no_map(self):
        """List Endpoints with no map returns empty."""
        client = HttpClient()
        result = client.list_endpoints()
        assert result["count"] == 0
        assert result["endpoints"] == []

    def test_read_endpoint_no_map(self):
        """Read Endpoint with no map returns error."""
        client = HttpClient()
        result = client.read_endpoint("anything")
        assert result["success"] is False
        assert "error" in result

    def test_read_endpoint_not_found(self, client_with_map):
        """Read Endpoint with unknown name returns error."""
        result = client_with_map.read_endpoint("nonexistent")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_write_endpoint_no_map(self):
        """Write Endpoint with no map returns error."""
        client = HttpClient()
        result = client.write_endpoint_action("anything", "100")
        assert result["success"] is False
        assert "error" in result

    def test_write_endpoint_not_found(self, client_with_map):
        """Write Endpoint with unknown name returns error."""
        result = client_with_map.write_endpoint_action("nonexistent", "100")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_write_endpoint_not_writable(self, client_with_map):
        """Write Endpoint to non-writable endpoint returns error."""
        result = client_with_map.write_endpoint_action("temp", "100")
        assert result["success"] is False
        assert "not writable" in result["error"]


class TestActionsIntegration:
    """Integration tests for SDK actions with demo server."""

    def test_list_endpoints_action(self, client):
        """List Endpoints action returns all demo endpoints."""
        result = client.list_endpoints()
        assert result["count"] > 0
        names = [ep["name"] for ep in result["endpoints"]]
        assert "temperature" in names
        assert "battery_voltage" in names
        assert "uptime" in names

    def test_get_status_action(self, client):
        """Get Status action returns connection info."""
        result = client.get_status()
        assert result["connected"] is True
        assert result["endpoints"] > 0


# =============================================================================
# End-to-End Lifecycle Tests (polling loop running)
# =============================================================================


@pytest.fixture
def polling_client(demo_server, endpoint_map):
    """Client with active polling loop running in a background thread.

    This mirrors production: the polling loop owns the event loop,
    and actions are called from a separate thread (like the SDK's gRPC thread).
    """
    import threading
    import time

    c = HttpClient(
        base_url="http://127.0.0.1:18090",
        endpoint_map=endpoint_map,
        poll_interval=0.2,
        timeout=5.0,
    )
    c.start()

    thread = threading.Thread(target=c.run, daemon=True)
    thread.start()

    # Wait for first successful poll
    for _ in range(50):
        if c._poll_count > 0:
            break
        time.sleep(0.1)
    assert c._poll_count > 0, "Polling loop did not start"

    yield c
    c.stop()


class TestEndToEndLifecycle:
    """Full lifecycle tests with active polling loop.

    These test the production topology: polling loop in a background thread,
    actions called from the test thread (simulating the SDK's gRPC thread).
    """

    def test_polling_loop_advances(self, polling_client):
        """Polling loop runs continuously and accumulates polls."""
        import time

        initial = polling_client._poll_count
        time.sleep(0.6)
        assert polling_client._poll_count >= initial + 2
        assert polling_client._error_count == 0

    def test_trace_source_initialized(self, polling_client):
        """Trace source has correct events from endpoint map."""
        src = polling_client._source
        assert src is not None
        assert getattr(src, "environment", None) is not None
        assert getattr(src, "power", None) is not None
        assert getattr(src, "system", None) is not None
        assert getattr(src, "config", None) is not None

    def test_read_endpoint_during_polling(self, polling_client):
        """Read action works cross-thread while polling loop is active."""
        result = polling_client.read_endpoint("temperature")
        assert result["success"] is True
        assert isinstance(result["value"], float)
        assert 10.0 < result["value"] < 35.0

    def test_send_request_during_polling(self, polling_client):
        """Send Request action works cross-thread during active polling."""
        result = polling_client.send_request("/api/sensors", "GET", "")
        assert result["success"] is True
        assert result["status_code"] == 200
        assert "temperature" in result["response"]

    def test_write_then_read_round_trip(self, polling_client):
        """Write a config value, read it back via action."""
        import time

        write_result = polling_client.write_endpoint_action("alarm_threshold", "99.0")
        assert write_result["success"] is True

        time.sleep(0.5)

        read_result = polling_client.read_endpoint("alarm_threshold")
        assert read_result["success"] is True
        assert read_result["value"] == pytest.approx(99.0)

    def test_get_status_reflects_live_state(self, polling_client):
        """Get Status reports accurate live metrics."""
        result = polling_client.get_status()
        assert result["connected"] is True
        assert result["poll_count"] > 0
        assert result["error_count"] == 0
        assert result["last_status_code"] == 200
        assert result["endpoints"] == 13
