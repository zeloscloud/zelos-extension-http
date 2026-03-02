# CLAUDE.md - Zelos HTTP Extension

HTTP/REST API polling extension for Zelos. Monitors REST endpoints by periodically
polling them and extracting data from JSON responses.

## Quick Reference

```bash
just install    # Install deps + pre-commit
just test       # Run pytest
just check      # Ruff lint
just format     # Ruff format + fix
just dev        # Run extension locally
just package    # Package for marketplace (.tar.gz)
just release X.Y.Z  # Bump version, test, tag
```

## Structure

```
zelos-extension-http/
├── main.py                              # CLI entry point (app/demo/trace modes)
├── extension.toml                       # Zelos manifest
├── config.schema.json                   # Config UI schema
├── zelos_extension_http/
│   ├── client.py                        # HttpClient with SDK integration
│   ├── endpoint_map.py                  # Endpoint/EndpointMap data model
│   ├── cli/app.py                       # App mode runner
│   └── demo/
│       ├── simulator.py                 # aiohttp demo weather station server
│       └── demo_device.json             # Demo endpoint map
└── tests/test_http.py                   # Unit + integration tests (72 tests)
```

## Key Patterns

- **EndpointMap**: JSON file mapping event names to lists of HTTP endpoints, each with
  a `json_path` for extracting values from JSON responses
- **Path caching**: Endpoints sharing the same path are fetched once per poll cycle
- **Simulator**: Real aiohttp server (not mocked) - tests exercise the actual client code
- **Actions**: Get Status, Send Request, List Endpoints, Read Endpoint, Write Endpoint

## Dependencies

- `aiohttp` - HTTP client and demo server
- `zelos-sdk` - Zelos trace/action integration
- `rich-click` - CLI framework
