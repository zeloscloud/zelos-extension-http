# Contributing

## Prerequisites

- [Zelos](https://zeloscloud.io) 26.0.10 or later
- [Zelos CLI](https://docs.zeloscloud.io/getting-started/install-cli/) 0.1.12 or later
- [just](https://github.com/casey/just)
- [uv](https://docs.astral.sh/uv/)

## Development Workflow

```bash
just install  # dependencies + pre-commit hooks
just run      # run app mode locally (reads ./config.json)
just demo     # run against the built-in weather station simulator
just test     # pytest
just check    # ruff lint + format check
just format   # auto-fix
```

1. Make your changes
2. `just format`
3. `just test`
4. Commit (pre-commit hooks run ruff automatically)

## Project Structure

```
zelos-extension-http/
├── extension.toml                  # Extension manifest (name, version, agent, package paths)
├── config.schema.json              # Configuration UI schema
├── main.py                         # Click entry point
├── pyproject.toml                  # Dependencies, ruff and pytest config
├── uv.lock                         # Locked dependency versions
├── Justfile                        # Development commands
├── LICENSE
├── README.md                       # User documentation
├── CHANGELOG.md
├── CLAUDE.md                       # Architecture notes
├── CONTRIBUTING.md                 # This file
├── .pre-commit-config.yaml
├── zelos_extension_http/
│   ├── __init__.py                 # ACTION_PREFIX
│   ├── actions.py                  # Action functions + register_actions()
│   ├── autoconfig.py               # The config form's Auto-configure check
│   ├── client.py                   # One server's polling, health and reconnect; the runner
│   ├── constants.py                # Trace layout and name rules
│   ├── discovery.py                # JSON responses as events and fields
│   ├── endpoint_map.py             # Endpoint map parsing, value coercion
│   ├── cli/
│   │   ├── __init__.py
│   │   └── app.py                  # Config load, startup validation, serve()
│   └── demo/
│       ├── __init__.py
│       ├── simulator.py            # Weather station REST server
│       └── demo_device.json        # Demo endpoint map
├── tests/
│   ├── conftest.py                 # Simulator fixture and helpers
│   ├── test_config.py              # Config, names, map validation, action flags
│   ├── test_discovery.py           # Flattening rules
│   ├── test_http.py                # Against the simulator: reachability, actions, TLS
│   └── test_e2e.py                 # main.py --file, read back from the trace
├── assets/
│   └── icon.png                    # Marketplace icon
├── .github/
│   ├── workflows/
│   │   ├── CI.yml
│   │   └── release.yml
│   └── dependabot.yml
└── .vscode/
```

## Common Tasks

### Run it in Zelos

With the simulator running (`just sim`):

```bash
zelos extensions install-local .
zelos extensions start local.http \
  --config '{"servers": [{"base_url": "http://127.0.0.1:8090", "paths": ["/api/sensors"]}]}'
zelos extensions logs local.http
```

A local install runs from your project directory, so you do not install again after a code
change: stop and start the extension. After a dependency change, stop it and run
`zelos extensions reinstall local.http`; after an `extension.toml` change, run
`zelos extensions install-local .` again.

### Add a Dependency

```bash
uv add package-name        # runtime
uv add --dev package-name  # dev
```

### Package for the Marketplace

```bash
just package   # zelos extensions package .
```

Produces `http-<version>.tar.gz` for the Zelos Marketplace, with `actions.json` (the actions
that run while the extension is stopped). CI does this automatically.

### Create a Release

Add the release to `CHANGELOG.md` and commit it, then:

```bash
just release 1.0.0
git push --follow-tags
```

## Testing

Integration tests start the real weather station simulator on a loopback port, so they
exercise the actual HTTP path (discovery, endpoint maps, TLS, outages). `tests/test_e2e.py`
runs `main.py --file` in app mode and reads the recorded trace back.

```bash
just test                        # everything
uv run pytest -v                 # verbose
uv run pytest -k test_name       # one test
```

Keep tests targeted. Prefer one integration test that proves a behavior end to end over
several unit tests that assert on internals.

```python
# tests/test_http.py
async def test_a_failing_path_warns_once_and_keeps_polling(sim, caplog):
    (client,) = clients_for([{"name": "wx", "base_url": sim.url, "paths": ["/missing"]}])
    ...
```

## Code Quality

- ruff, line length 100, google docstrings
- Type hints on every function signature
- Comments state the constraint or the failure prevented, not what the code does

## Getting Help

- [Zelos Docs](https://docs.zeloscloud.io)
- [SDK Guide](https://docs.zeloscloud.io/sdk)
- [GitHub Issues](https://github.com/zeloscloud/zelos-extension-http/issues)

## License

MIT - see [LICENSE](LICENSE)
