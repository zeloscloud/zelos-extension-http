# Changelog

All notable changes to Zelos Extension HTTP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Several servers in one extension (`servers[]`), each with its own base URL, paths, endpoint map, poll interval, timeout and TLS settings; shared settings under `advanced`. One unreachable server never stalls the others.
- Discovery: `paths` are polled without a map and their JSON responses traced as they come: an event per path (`/api/sensors` as `api/sensors`), sub-events for nested objects, fields `<key>_<i>` for arrays of up to 16 scalars, `value` for a bare value.
- `_server` health event per server after every poll cycle: `reachable`, `last_status`, `latency_ms`, `errors`.
- `advanced.prefix` (default `HTTP`): events read `HTTP/<server>/<event>` and logs `HTTP/log`; cleared, one source per server and logs on `http_log`.
- TLS settings per server or shared: `verify_tls` (default on; off logs a WARNING at start), `ca_file`, `cert_file`, `key_file`.
- The config form's Auto-configure button checks each server in the form.
- `advanced.retry_unreachable_at_start`. A server that stops answering is retried with backoff, 3 s doubling to 60 s.
- `--file` records the trace to a `.trz`; `demo-server` runs the simulator standalone; `trace --path` for discovery, plus TLS options.
- Actions take an optional `server`; `read_endpoint` also reads a path; `send_request` adds PATCH and DELETE and returns the status, headers and body.

### Changed
- Config format: `base_url`, `endpoint_map_file`, `poll_interval`, `timeout`, `verify_ssl` and `log_level` move under `servers[]` and `advanced`. An old config stops the extension with one error naming its keys: reconfigure. No migration.
- Trace layout: the source is the prefix (`HTTP`), no longer the endpoint map's `name`; map events read `HTTP/<server>/<event>`. The map's `name` only labels it in the log, and its `base_url` is ignored.
- A server that cannot be reached at start stops the extension with one ERROR line naming it and the fix, exit status 1, instead of retrying every 3 s.
- Actions raise on failure instead of returning `"success": false`. `read_endpoint` is read-only. `send_request` takes JSON bodies only and fails on an HTTP status of 300 and up.
- Writes to an endpoint with a `scale` send the value divided by it, so a written value reads back the same; the value is checked against the endpoint's datatype first.
- `string` endpoints trace their text; they were declared as numbers and never recorded.
- Requests send `Accept: application/json` and a `zelos-extension-http/<version>` User-Agent, and read JSON whatever the Content-Type.
- A failing path or endpoint value logs one WARNING per outage; before, nothing was logged.
- Log lines are UTC ISO 8601 with milliseconds.
- An endpoint map event may be named `log`: the logs are no longer on the same source.

### Removed
- `headers`, the custom request headers used for authentication: no secrets in config. A base URL with a user name or password is refused.
- The Demo Mode setting: run `main.py demo` or `main.py --demo`. An old `demo` key is ignored.
- `trace -H`.

### Fixed
- A map event or endpoint name the trace would refuse (e.g. with a `.`) stops the extension with one error at load instead of a traceback; so does a duplicate endpoint name in one event.
- An endpoint value outside its datatype's range is skipped with one warning instead of failing the poll cycle.
- Mapped endpoints on one path with different methods each send their own request.

## [0.2.0] - 2026-11-01

### Changed
- Needs Zelos 26.0.10 or later.
- Get Status and List Endpoints are read-only and stay out of the app's action list. They still run by name.

### Fixed
- Logs record as a `log` event next to the polled values, on the endpoint map's source (`http` without a map), instead of on a separate `zelos_extension_http_logger` source. An endpoint map event can no longer be named `log`.
- Demo mode no longer writes an aiohttp access line to the log for every request the simulator serves.
- Saved settings that fail the settings check stop the extension with one `Invalid configuration: ...` error line and exit status 1, instead of a Python traceback.
- Send Request runs with no request body, as its form says. Before, it refused to run until the body field had a value.

## [0.1.1] - 2026-03-02

### Added
- Initial implementation
