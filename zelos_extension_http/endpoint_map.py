"""Endpoint map: named, typed signals picked out of JSON responses.

{
  "name": "weather_station",
  "events": {
    "environment": [
      {"name": "temperature", "path": "/api/sensors", "json_path": "temperature", "unit": "C"}
    ],
    "config": [
      {"name": "setpoint", "path": "/api/config", "json_path": "setpoint", "writable": true}
    ]
  }
}

Event keys become trace events, endpoint names their fields. Required per
endpoint: name, path. Optional: method (GET), datatype (float32), unit,
json_path, scale, writable. `name` labels the map in the log; a `base_url`
key from older maps is ignored (each server sets its own).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zelos_sdk

INT_RANGES = {
    "uint8": (0, 2**8 - 1),
    "int8": (-(2**7), 2**7 - 1),
    "uint16": (0, 2**16 - 1),
    "int16": (-(2**15), 2**15 - 1),
    "uint32": (0, 2**32 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "uint64": (0, 2**64 - 1),
    "int64": (-(2**63), 2**63 - 1),
}
VALID_DATATYPES = {*INT_RANGES, "bool", "float32", "float64", "string"}
VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

SDK_DATATYPES = {
    "bool": zelos_sdk.DataType.Boolean,
    "uint8": zelos_sdk.DataType.UInt8,
    "int8": zelos_sdk.DataType.Int8,
    "uint16": zelos_sdk.DataType.UInt16,
    "int16": zelos_sdk.DataType.Int16,
    "uint32": zelos_sdk.DataType.UInt32,
    "int32": zelos_sdk.DataType.Int32,
    "uint64": zelos_sdk.DataType.UInt64,
    "int64": zelos_sdk.DataType.Int64,
    "float32": zelos_sdk.DataType.Float32,
    "float64": zelos_sdk.DataType.Float64,
    "string": zelos_sdk.DataType.String,
}

BOOL_TRUE = frozenset({"true", "1", "on", "yes"})
BOOL_FALSE = frozenset({"false", "0", "off", "no"})


@dataclass
class Endpoint:
    """A single HTTP endpoint definition."""

    path: str
    name: str
    method: str = "GET"
    datatype: str = "float32"
    unit: str = ""
    json_path: str = ""
    scale: float = 1.0
    writable: bool = False

    def __post_init__(self) -> None:
        """Validate endpoint definition."""
        if self.datatype not in VALID_DATATYPES:
            msg = f"Invalid datatype '{self.datatype}'. Must be one of {sorted(VALID_DATATYPES)}"
            raise ValueError(msg)
        self.method = self.method.upper()
        if self.method not in VALID_METHODS:
            msg = f"Invalid method '{self.method}'. Must be one of {sorted(VALID_METHODS)}"
            raise ValueError(msg)
        if not self.path.startswith("/"):
            raise ValueError(f"Endpoint '{self.name}': path '{self.path}' must start with '/'")


@dataclass
class EndpointMap:
    """Collection of endpoint definitions organized by user-defined events."""

    events: dict[str, list[Endpoint]] = field(default_factory=dict)
    name: str = "http"

    @classmethod
    def from_file(cls, path: str | Path) -> EndpointMap:
        """Load endpoint map from JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Endpoint map file not found: {path}")
        with path.open() as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointMap:
        """Load endpoint map from dictionary.

        Raises:
            ValueError: On a name the trace would refuse or re-nest, or a duplicate
                endpoint name in one event: either would misfile data.
        """
        events: dict[str, list[Endpoint]] = {}
        for event_name, endpoints_data in data.get("events", {}).items():
            if zelos_sdk.sanitize_name(event_name, kind="event") != event_name:
                raise ValueError(
                    f"Invalid event name {event_name!r}: use letters, digits, space, '_', '-' "
                    "or '/', not starting with '_'"
                )
            endpoints = []
            for i, ep_data in enumerate(endpoints_data):
                if "name" not in ep_data or "path" not in ep_data:
                    raise ValueError(f"Event '{event_name}': endpoint {i} needs a name and a path")
                ep = Endpoint(
                    path=ep_data["path"],
                    name=ep_data["name"],
                    method=ep_data.get("method", "GET"),
                    datatype=ep_data.get("datatype", "float32"),
                    unit=ep_data.get("unit", ""),
                    json_path=ep_data.get("json_path", ""),
                    scale=ep_data.get("scale", 1.0),
                    writable=ep_data.get("writable", False),
                )
                if (
                    zelos_sdk.sanitize_name(ep.name, kind="field") != ep.name
                    or ep.name == "time_ns"
                ):
                    raise ValueError(
                        f"Event '{event_name}': invalid endpoint name {ep.name!r}; use letters, "
                        "digits, space, '_' or '-' (not 'time_ns')"
                    )
                if any(e.name == ep.name for e in endpoints):
                    raise ValueError(f"Event '{event_name}': duplicate endpoint name '{ep.name}'")
                endpoints.append(ep)
            if endpoints:
                events[event_name] = endpoints
        return cls(events=events, name=data.get("name", "http"))

    @property
    def endpoints(self) -> list[Endpoint]:
        """Flat list of all endpoints across all events."""
        return [ep for eps in self.events.values() for ep in eps]

    def get_by_name(self, name: str) -> tuple[str, Endpoint] | None:
        """(event, endpoint) by name, or by `<event>/<name>`.

        Raises:
            ValueError: If a bare name is in several events
        """
        event, _, bare = name.rpartition("/")
        matches = [
            (event_name, ep)
            for event_name, eps in self.events.items()
            if not event or event_name == event
            for ep in eps
            if ep.name == bare
        ]
        if len(matches) > 1:
            choices = ", ".join(f"{e}/{ep.name}" for e, ep in matches)
            raise ValueError(f"Endpoint name '{name}' is in several events; use one of: {choices}")
        return matches[0] if matches else None


def extract_json_value(data: Any, json_path: str) -> Any:
    """A value from parsed JSON by dot path (`data.sensors.0.value`); None when absent."""
    if not json_path:
        return data
    current = data
    for key in json_path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def coerce_value(raw: Any, datatype: str, scale: float = 1.0) -> Any:
    """A JSON value as `datatype`, times `scale`; None when absent, unparseable or
    out of the datatype's range (one bad value would fail the whole event at emit)."""
    if raw is None or isinstance(raw, dict | list):
        return None
    if datatype == "string":
        return str(raw)
    if datatype == "bool":
        if isinstance(raw, str):
            text = raw.strip().lower()
            return True if text in BOOL_TRUE else False if text in BOOL_FALSE else None
        return bool(raw)
    try:
        number = float(raw) * scale
    except (ValueError, TypeError):
        return None
    if datatype not in INT_RANGES:
        return number
    if not math.isfinite(number):
        return None
    # Unscaled ints stay exact: a float has 53 bits.
    value = raw if scale == 1 and isinstance(raw, int) else int(number)
    low, high = INT_RANGES[datatype]
    return value if low <= value <= high else None


def parse_value(text: str, datatype: str) -> Any:
    """A write action's text as `datatype`.

    Raises:
        ValueError: If the text does not parse as the datatype or is out of its range
    """
    if datatype == "string":
        return text
    value = text.strip().lower()
    if datatype == "bool":
        if value in BOOL_TRUE:
            return True
        if value in BOOL_FALSE:
            return False
        raise ValueError(f"Cannot parse '{text}' as bool. Use true/false or 1/0.")
    if datatype not in INT_RANGES:
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"Cannot parse '{text}' as {datatype}") from None
    try:
        number = int(value)
    except ValueError:
        try:
            number = int(float(value))
        except ValueError:
            raise ValueError(f"Cannot parse '{text}' as {datatype}") from None
    low, high = INT_RANGES[datatype]
    if not low <= number <= high:
        raise ValueError(f"{number} is out of range for {datatype} ({low} to {high})")
    return number


def encode_value(value: Any, datatype: str, scale: float = 1.0) -> Any:
    """A typed value in the map's units as the server's raw value: divided by `scale`."""
    if datatype in ("string", "bool") or scale in (0, 1):
        return value
    raw = value / scale
    return round(raw) if datatype in INT_RANGES else raw
