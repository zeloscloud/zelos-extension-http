# CLAUDE.md

## Commands

```bash
just install      # deps + pre-commit hooks
just format       # ruff format + ruff check --fix
just check        # ruff format --check + ruff check
just test         # pytest (integration tests start the real simulator on loopback)
just run          # app mode, reads ./config.json
just demo         # built-in weather station simulator
just sim *ARGS    # standalone simulator: --host, --port
just package      # zelos extensions package .
```

## Key Files

| Path | Role |
|------|------|
| `main.py` | Click entry point; re-exports `ACTION_PREFIX` |
| `zelos_extension_http/__init__.py` | `ACTION_PREFIX = "HTTP"` - must match `name` in `extension.toml` |
| `zelos_extension_http/constants.py` | `trace_layout()`, `name_error()`, reserved names, `HEALTH_EVENT` |
| `zelos_extension_http/actions.py` | Free-function action surface + `register_actions()` |
| `zelos_extension_http/client.py` | `HttpClient` (one server: session, poll cycle, trace events, health) and `HttpRunner` (the loop, signals, first contact, shutdown, action dispatch) |
| `zelos_extension_http/discovery.py` | `event_name()`, `flatten()`: a JSON body as events and fields |
| `zelos_extension_http/endpoint_map.py` | Endpoint/EndpointMap parsing and name rules; read coercion, write parsing and scaling |
| `zelos_extension_http/autoconfig.py` | `auto_config`: one GET per server in the form |
| `zelos_extension_http/cli/app.py` | Config load, `servers[]` / `advanced` resolution, startup validation, `serve()` |
| `zelos_extension_http/demo/simulator.py` | Weather station REST server: demo mode, `demo-server`, tests |
| `zelos_extension_http/demo/demo_device.json` | Demo endpoint map |
| `tests/conftest.py` | Simulator fixture, `clients_for`, `running`, `read_trace` |
| `tests/test_config.py` | Defaults vs schema, legacy shape, name rules, inheritance, map validation, action flags |
| `tests/test_discovery.py` | Flattening rules |
| `tests/test_http.py` | Runner against the simulator: first contact, retry and outage, failing path, actions, TLS, `auto_config` |
| `tests/test_e2e.py` | `main.py --file` in app mode, prefix and cleared, read back from the `.trz` |

## Architecture

### Startup

- `cli/app.serve()` is the only place that starts clients: config -> clients -> `set_runner` -> `register_actions` -> `TraceWriter` (`--file`) -> `open_sources(prefix)` -> `zelos_sdk.init(name=ACTION_PREFIX)` -> `client.start` -> `runner.run()`.
- `open_sources` creates the SDK's global source (`prefix`, else `http_log`) before `init()`, which then reuses it; the log handler (INFO+) writes its `log` event. Earlier records reach stderr only.
- Registration must precede `init()` (later actions may never be advertised).
- Startup problems (bad config, names, map, TLS files) are one `logger.error` + `sys.exit(1)`; tracebacks are for bugs.

### Config

- `run_app_mode` checks the raw file for the old flat shape before `load_config`: the schema error would not say the format changed. It names the keys, never their values (an old `headers` held tokens).
- Per server, "" / null inherit from `advanced` (`inherit`): timeout, CA file, and client cert + key as a pair.
- Typed names (prefix, server) go through `name_error`: rejected, not rewritten. Default server names are the sanitized host, `_<port>` when hosts collide. Duplicates and `log` / `http_log` exit.
- No secrets in config: no headers, tokens or passwords. Base URL userinfo is refused at config and in `auto_config`, never echoed. Requests carry only `Accept: application/json` and the User-Agent.
- `demo` replaces `servers` with one server `demo` on the simulator at 127.0.0.1:8090 with the demo map; `trace` builds a one-entry config through the same path.

### Polling

- One `aiohttp.ClientSession` per server. Each distinct (method, path) of the map and `paths` is requested once per cycle, in order, at a fixed rate.
- A cycle with any HTTP answer is reachable. A connect failure (`ClientConnectorError`) ends the cycle: it is the host's, not one path's. Timeouts and bad statuses are per path.
- First contact is required: the runner waits for every server's first cycle, then one ERROR per server without an answer and exit 1, unless `retry_unreachable_at_start`. After that, no answer backs off 3 s doubling to 60 s: one WARNING per outage, one INFO on recovery.
- Warn once per key (`_warned`, cleared on recovery): a failing path, a missing map value, a discovery note.
- `_server` health after every cycle: `reachable`, `last_status`, `latency_ms` (slowest answer), `errors` (cumulative). Without an answer, status and latency are null, not zero.

### Trace

- `trace_layout(prefix, server)` is the one naming rule: source `<prefix>` with events `<server>/<event>`, or cleared, source `<server>` with unprefixed events.
- Map events are declared at start with their datatypes. Discovered events are declared at their first response, kinds inferred (bool, float64, string). A declared schema is fixed per source: a field that appears later or changes kind warns once and waits for a restart (no source rotation).
- An event name has one owner. A path colliding with a map event, or two paths with one event, is a load error; a discovered sub-event colliding at runtime warns once and the first owner keeps it.
- Rows are logged with `log_at` at the response's receive time, so a response's events share one timestamp.
- `time_ns` is the SDK's time column: refused as a map endpoint name, skipped with a note in discovery.

### Actions

- Free functions in `actions.py`, bound via `set_runner`. Optional `server`: `ValueError` listing names when omitted with several; `get_status` / `list_endpoints` cover all.
- Failure convention: **raise**. The protocol reads the verdict from the exception, so `{"success": False}` reports success. Input errors `ValueError`; no answer or an HTTP status >= 300 `RuntimeError`; a PUT without an answer in time `TimeoutError` saying it may have applied.
- Everything but `auto_config` dispatches via `HttpRunner.run_coro` into the polling loop, on the server's session; no loop = `RuntimeError("extension is not running")`.
- `auto_config` is standalone (own `asyncio.run`); the schema's `ui:options.autoconfig` names it, keep in step. No servers: an error asking for one (no standard port; never sweep).
- Writes: text parsed and range-checked against the datatype, divided by `scale`, PUT as `{json_path: value}` (the bare value without a json_path).

### Shutdown

- `HttpRunner` owns the one loop. Signals via `loop.add_signal_handler` set a stop event (`signal.signal` fallback); never `sys.exit` from a handler. Stop cancels every server task; sessions close within `SHUTDOWN_TIMEOUT` (3 s) in total.

## Code Style

- ruff, line length 100, google docstrings
- `contextlib.suppress(Exception)` over bare `try/except: pass`
- comments state the constraint, not the code
