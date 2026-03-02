# Zelos extension for HTTP/REST API polling

## Features

- 📊 **Polling REST endpoints** - Periodically poll any HTTP/JSON API and stream values into Zelos
- 🔍 **JSON path extraction** - Extract nested values from JSON responses using dot-notation paths
- ⚡ **Path caching** - Endpoints sharing the same URL are fetched once per poll cycle
- ✏️ **Writable endpoints** - Write values back to REST APIs via PUT requests
- 🌐 **Custom headers & SSL** - Supports auth headers, custom HTTP methods, and SSL configuration
- 🌤️ **Demo mode** - Built-in weather station simulator for testing without hardware

## Quick Start

1. **Install** the extension from the Zelos App
2. **Configure** your base URL and provide an endpoint map file (`.json`)
3. **Start** the extension to begin streaming data
4. **View** real-time data in your Zelos App

## Configuration

All configuration is managed through the Zelos App settings interface.

### Required Settings
- **Base URL**: The server base URL (e.g., `http://192.168.1.100:8080`)
- **Endpoint Map File**: Upload your endpoint map JSON file

### Optional Settings
- **Poll Interval**: Seconds between polls (default: 1.0)
- **Timeout**: HTTP request timeout in seconds (default: 5.0)
- **Headers**: JSON object of custom HTTP headers (e.g., `{"Authorization": "Bearer token"}`)
- **Verify SSL**: Verify SSL certificates (default: true)
- **Demo Mode**: Use the built-in weather station simulator

## Actions

The extension provides several actions accessible from the Zelos App:

- **Get Status**: View connection status, poll count, and error count
- **Send Request**: Send an arbitrary HTTP request and display the response
- **List Endpoints**: Browse all configured endpoints with their metadata
- **Read Endpoint**: Read the current value of a single named endpoint
- **Write Endpoint**: Write a value to a writable endpoint via PUT

## Endpoint Map Format

The endpoint map defines which URLs to poll and how to extract values from JSON responses. Endpoints are grouped into events, which become Zelos trace events.

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

Endpoints sharing the same `path` are fetched once per poll cycle — define multiple signals from a single API response at no extra cost.

### Endpoint Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | — | Signal name (becomes trace field) |
| `path` | Yes | — | URL path to poll |
| `json_path` | No | `""` | Dot-notation path to extract from JSON response |
| `method` | No | `GET` | HTTP method |
| `datatype` | No | `float32` | Data type (bool, uint8–64, int8–64, float32/64, string) |
| `unit` | No | `""` | Physical unit |
| `scale` | No | `1.0` | Scale factor applied to extracted value |
| `writable` | No | `false` | If true, can PUT values to this endpoint |

## What is REST API polling?

REST API polling is a technique for monitoring HTTP endpoints by periodically sending requests and extracting values from JSON responses. This is common for IoT device gateways, sensor platforms, cloud service health endpoints, and industrial equipment with web interfaces.

## Development

Want to contribute or modify this extension? See [CLAUDE.md](CLAUDE.md) for the developer guide.

## CLI Usage

The extension includes a command-line interface for advanced use cases. No installation required — just use `uv run`:

### Demo Mode

```bash
# Launch with built-in weather station simulator
uv run main.py demo
```

### HTTP Tracing

```bash
# Poll endpoints defined in a map file
uv run main.py trace http://192.168.1.100:8080 endpoints.json

# With custom poll interval and headers
uv run main.py trace http://api.example.com endpoints.json -i 2.0 -H "Authorization: Bearer token"
```

## Links

- **Repository**: [github.com/zeloscloud/zelos-extension-http](https://github.com/zeloscloud/zelos-extension-http)
- **Issues**: [Report bugs or request features](https://github.com/zeloscloud/zelos-extension-http/issues)

## Support

For help and support:
- 📖 [Zelos Documentation](https://docs.zeloscloud.io)
- 🐛 [GitHub Issues](https://github.com/zeloscloud/zelos-extension-http/issues)
- 📧 help@zeloscloud.io

## License

MIT License — see [LICENSE](LICENSE) for details.

---

**Built with [Zelos](https://zeloscloud.io)**
