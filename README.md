# Zelos HTTP

A Zelos extension for HTTP and HTTPS REST APIs. Poll IoT gateways, sensor platforms, devices with a web API, and other REST services, trace their JSON responses as signals, and write setpoints back from the Zelos App.

## Features

- 🔍 **Discovery**: List the paths, and every JSON field is traced; nested objects become sub-events, no map needed
- 📄 **Endpoint maps**: Named, typed signals with units and scaling, picked out by JSON path
- ⚡ **One request per URL**: Paths and endpoints sharing a URL are fetched once per poll cycle
- 🔒 **TLS**: Certificate verification on by default, with private CA and client certificate files
- 🌐 **Multiple servers**: Any number in one extension; one unreachable server never stalls the others
- ✏️ **Read, write & request actions**: Writable setpoints (PUT) and one-off requests from the Zelos App
- 🩺 **Health**: A `_server` event per server with reachability, last HTTP status, latency and errors
- 🧪 **Demo mode**: Built-in weather station simulator for testing without hardware

## Quick Start

[![Install the HTTP extension, add a server by its base URL and a path, start it and plot a discovered field](assets/howto/quickstart/quickstart.avif)](assets/howto/quickstart/quickstart.mp4)

From the CLI, on the agent that can reach the server:

```bash
zelos extensions install zeloscloud/zelos-extension-http
zelos extensions start zeloscloud.zelos-extension-http \
  --config '{"servers": [{"base_url": "http://192.168.1.100:8080", "paths": ["/api/status"]}]}'
```

In the app:

1. **Install** the extension from the Zelos App
2. **Configure** your servers (base URL, and paths to discover or an endpoint map file), then press Auto-configure to check them
3. **Start** the extension to begin streaming data
4. **View** real-time values in your Zelos App

## Configuration

### Servers

| Setting | Default | Description |
|---------|---------|-------------|
| Name | base URL host | Trace name (`weather01`); unique |
| Base URL | | `http://host:port`, plus any path every request shares |
| Paths | | URL paths traced by [discovery](#discovery) |
| Endpoint Map File | | JSON [endpoint map](#endpoint-map) |
| Poll Interval | `1.0` | Seconds between requests of every path and endpoint |
| Timeout | Advanced | Request timeout (s) |
| Verify TLS | on | Check the server's certificate; off warns at start |
| CA File / Client Certificate / Client Private Key | Advanced | See [Secure connections](#secure-connections) |

### Advanced

Applies to every server unless the server overrides it.

| Setting | Default | Description |
|---------|---------|-------------|
| `prefix` | `HTTP` | Trace source: `HTTP/weather01/api/sensors`; cleared = one source per server |
| `log_level` | `INFO` | Logging verbosity |
| `retry_unreachable_at_start` | off | Keep retrying a server that cannot be reached at start |
| `timeout` | `5.0` | Request timeout (s) |
| `ca_file` / `cert_file` / `key_file` | | TLS files for every server without its own |

Every server must answer at start, or the extension stops with an error naming it. Once running, a server that stops answering is retried with backoff (3 s doubling to 60 s), and a path that fails is warned once and polled again.

### Secure connections

Use an `https://` base URL. The server's certificate is checked against the system's CAs, or only those in CA File (a private CA, or a self-signed server). Client Certificate and Client Private Key answer a server that requires a client certificate. Turning off Verify TLS accepts any certificate, with a warning at start. The extension sends no credentials: no user name, password, token or custom header, and a base URL with `user:pass@` is refused.

### Discovery

Each path's JSON response is traced as it comes. The path names the event (`/api/sensors` traces as `api/sensors`), each scalar key is a field, a nested object is a sub-event (`api/sensors/wind`), an array of scalars is fields `<key>_0` to `<key>_15`, and a bare value is the field `value`. Numbers trace as float64, true/false as bool, text as string. The first response fixes an event's fields: a key that appears later, or changes type, is warned once and traced after a restart.

With the simulator running (`just sim`), this server:

```json
{"servers": [{"name": "weather", "base_url": "http://127.0.0.1:8090", "paths": ["/api/sensors", "/api/power"]}]}
```

traces `HTTP/weather/api/sensors` (temperature, humidity, ...), `HTTP/weather/api/sensors/wind` (direction, gust) and `HTTP/weather/api/power` (..., `cell_voltages_0` to `cell_voltages_3`).

### Auto-configure

The config form's Auto-configure button checks each server in the form: it requests the server's first path (or its base URL) with the form's TLS settings and reports the HTTP status, whether the response is JSON, and the events it traces as. HTTP has no standard port to look on, so with no servers it asks for a base URL.

### Trace layout

| What | Prefix `HTTP` | Prefix cleared |
|---|---|---|
| Endpoint map event | `HTTP/weather/environment` | `weather/environment` |
| Discovered path | `HTTP/weather/api/sensors` | `weather/api/sensors` |
| Nested object | `HTTP/weather/api/sensors/wind` | `weather/api/sensors/wind` |
| Server health | `HTTP/weather/_server` | `weather/_server` |
| Extension logs | `HTTP/log` | `http_log/log` |

Values carry the time their response arrived. `_server` logs one row per poll cycle: `reachable`, `last_status` (HTTP status), `latency_ms` (the cycle's slowest answer) and `errors` (failed requests since start).

## Endpoint Map

```json
{
  "name": "my_device",
  "events": {
    "sensors": [
      {"name": "temperature", "path": "/api/sensors", "json_path": "temp", "unit": "C"},
      {"name": "humidity", "path": "/api/sensors", "json_path": "humidity", "unit": "%"}
    ],
    "controls": [
      {"name": "setpoint", "path": "/api/config", "json_path": "setpoint", "writable": true}
    ]
  }
}
```

Event keys become trace events, endpoint names their fields. Endpoints sharing a `path` are fetched once per poll cycle. A map event and a discovered path may not trace under the same event name.

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | | Field name |
| `path` | Yes | | URL path to poll, below the base URL |
| `json_path` | No | `""` | Dot path into the JSON response (`data.sensors.0.value`) |
| `method` | No | `GET` | HTTP method |
| `datatype` | No | `float32` | bool, int8-64, uint8-64, float32, float64, string |
| `unit` | No | `""` | Display unit |
| `scale` | No | `1.0` | Read value x scale; a write is divided by it |
| `writable` | No | `false` | Allow `write_endpoint` (PUT `{json_path: value}`) |

## Actions

| Action | Description |
|--------|-------------|
| `get_status` | Reachability, last HTTP status, latency, errors |
| `list_endpoints` | Mapped endpoints, and discovered paths with their field types |
| `read_endpoint` | Read a mapped endpoint by name, or GET a path and show what it traces |
| `write_endpoint` | Write a writable mapped endpoint (PUT, in the map's units) |
| `send_request` | One GET, POST, PUT, PATCH or DELETE, with an optional JSON body |
| `auto_config` | Check servers for the config form |

With several servers, pass `server` to pick one.

```bash
zelos actions execute HTTP/read_endpoint --params '{"name":"temperature","server":"weather"}'
```

## Development

```bash
just install      # deps + pre-commit hooks
just format       # ruff format + fix
just check        # ruff lint + format check
just test         # pytest
just run          # run app mode locally (reads ./config.json)
just demo         # built-in weather station simulator
just sim          # standalone simulator on 127.0.0.1:8090: --host, --port
```

## Links

- [Zelos Documentation](https://docs.zeloscloud.io)
- [SDK Guide](https://docs.zeloscloud.io/sdk)
- [aiohttp Documentation](https://docs.aiohttp.org/)
- [GitHub Issues](https://github.com/zeloscloud/zelos-extension-http/issues)

## CLI Usage

The extension includes a command-line interface for tracing without the Zelos App. No installation required, just use `uv run`:

```bash
uv run main.py                                                  # app mode (Zelos App config)
uv run main.py --file                                           # app mode, also recording <UTC time>.trz
uv run main.py demo                                             # built-in simulator
uv run main.py trace http://192.168.1.100:8080 endpoints.json   # one server: base URL + map
uv run main.py trace http://127.0.0.1:8090 -p /api/sensors      # discovery
uv run main.py trace https://gateway.local -p /api/status --ca-file ca.pem
```

## Support

For help and support:
- 📖 [Zelos Documentation](https://docs.zeloscloud.io)
- 🐛 [GitHub Issues](https://github.com/zeloscloud/zelos-extension-http/issues)
- 📧 help@zeloscloud.io

## License

MIT License - see [LICENSE](LICENSE) for details.

---

**Built with [Zelos](https://zeloscloud.io)**
