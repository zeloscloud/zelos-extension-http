# Changelog

All notable changes to Zelos Extension HTTP will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
