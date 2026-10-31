# Contributing

## Prerequisites

- [Zelos](https://zeloscloud.io) 26.0.10 or later
- [Zelos CLI](https://docs.zeloscloud.io/reference/cli/) 0.1.11 or later
- [just](https://github.com/casey/just)
- [uv](https://docs.astral.sh/uv/)

## Layout

`extension.toml` describes everything the extension contributes:

| Part | Files | Manifest |
| ---- | ----- | -------- |
| Agent runtime | `main.py`, the Python package, `pyproject.toml`, `config.schema.json` | `[agent]`, `[config]` |

The Python package:

| File | Contents |
| ---- | -------- |
| `main.py` | CLI: app mode (default), `demo` and `trace` |
| `zelos_extension_http/client.py` | `HttpClient`: polls endpoints, writes trace events, serves the actions |
| `zelos_extension_http/endpoint_map.py` | `Endpoint` and `EndpointMap`: events, paths and JSON paths |
| `zelos_extension_http/cli/app.py` | App mode: reads the config and starts the client |
| `zelos_extension_http/demo/simulator.py` | The weather station server that demo mode polls |
| `zelos_extension_http/demo/demo_device.json` | The endpoint map for the simulator |
| `tests/test_http.py` | Unit tests, and integration tests against the simulator |

## Commands

| Command                | Description                                        |
| ---------------------- | -------------------------------------------------- |
| `just install`         | Install dependencies and the pre-commit hooks      |
| `just format`          | Format code with ruff                              |
| `just check`           | Check formatting and lint (ruff)                   |
| `just test`            | Run tests (pytest)                                 |
| `just package`         | Package for the Zelos marketplace                  |
| `just release VERSION` | Bump version, check, test, commit, tag             |
| `just clean`           | Remove build output, caches and packages           |

`just --list` also shows the recipes each part adds. `just install` installs the
pre-commit hooks in `.pre-commit-config.yaml`.

The tests start the simulator on localhost and poll it with the real client. They need no
network.

## Run it in Zelos

```bash
zelos extensions install-local .
zelos extensions start local.http --config '{"base_url": "http://127.0.0.1:8090", "demo": true}'
zelos extensions logs local.http
```

A local install runs from your project directory, so you do not install again after a code
change:

- Agent runtime: stop and start the extension. After a dependency change, stop it and run
  `zelos extensions reinstall <id>`.
- `extension.toml`: run `zelos extensions install-local .` again.

Demo mode starts the weather station simulator on `127.0.0.1:8090` and polls it, so it needs
no server. To run without the agent's extension manager:

```bash
echo '{"base_url": "http://127.0.0.1:8090", "demo": true}' > config.json
just run                                                      # app mode, reads ./config.json
uv run python main.py demo                                    # demo mode, needs no config.json
uv run python main.py trace http://host:8080 endpoints.json   # poll a real server
```

These connect to the agent at `ZELOS_AGENT_URL`, else `http://localhost:2300`.

## Packaging

```bash
just package
```

The archive lands next to `extension.toml` as `<name>-<version>.tar.gz`, where `<name>` is the
manifest name in lowercase with every other run of characters turned into one hyphen. It holds
`extension.toml`, the README, the icon, the config schema and the paths listed in
`[package] paths`. With an agent runtime it also holds `pyproject.toml`, `uv.lock` and
`actions.json`, the list of actions that run while the extension is stopped.

## Release

Add the release to `CHANGELOG.md` and commit it, then:

```bash
just release 1.0.0
git push --follow-tags
```

The release workflow checks, tests and packages the extension, then attaches the archive to a
GitHub release.
