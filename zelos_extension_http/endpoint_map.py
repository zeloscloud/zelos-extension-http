"""HTTP endpoint map for defining REST API polling targets.

The endpoint map format uses user-defined events to group endpoints semantically:

{
  "name": "weather_station",
  "events": {
    "environment": [
      {"name": "temperature", "path": "/api/temp", "json_path": "value"}
    ],
    "system": [
      {"name": "uptime", "path": "/api/system", "json_path": "uptime_seconds"}
    ]
  }
}

Event names become Zelos trace events. Endpoint names become fields.

Required fields per endpoint: name, path
Optional: method (GET), datatype (float32), unit, json_path, scale, writable
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_DATATYPES = {
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
}

VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


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
    description: str = ""
    body: str = ""

    def __post_init__(self) -> None:
        """Validate endpoint definition."""
        if self.datatype not in VALID_DATATYPES:
            msg = f"Invalid datatype '{self.datatype}'. Must be one of {sorted(VALID_DATATYPES)}"
            raise ValueError(msg)
        self.method = self.method.upper()
        if self.method not in VALID_METHODS:
            msg = f"Invalid method '{self.method}'. Must be one of {sorted(VALID_METHODS)}"
            raise ValueError(msg)


@dataclass
class EndpointMap:
    """Collection of endpoint definitions organized by user-defined events."""

    events: dict[str, list[Endpoint]] = field(default_factory=dict)
    name: str = "http"
    base_url: str = ""
    description: str = ""

    @classmethod
    def from_file(cls, path: str | Path) -> EndpointMap:
        """Load endpoint map from JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Endpoint map file not found: {path}")

        with path.open() as f:
            data = json.load(f)

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointMap:
        """Load endpoint map from dictionary."""
        events: dict[str, list[Endpoint]] = {}

        for event_name, endpoints_data in data.get("events", {}).items():
            endpoints = []
            for ep_data in endpoints_data:
                ep = Endpoint(
                    path=ep_data["path"],
                    name=ep_data["name"],
                    method=ep_data.get("method", "GET"),
                    datatype=ep_data.get("datatype", "float32"),
                    unit=ep_data.get("unit", ""),
                    json_path=ep_data.get("json_path", ""),
                    scale=ep_data.get("scale", 1.0),
                    writable=ep_data.get("writable", False),
                    description=ep_data.get("description", ""),
                    body=ep_data.get("body", ""),
                )
                endpoints.append(ep)
            events[event_name] = endpoints

        return cls(
            events=events,
            name=data.get("name", "http"),
            base_url=data.get("base_url", ""),
            description=data.get("description", ""),
        )

    @property
    def endpoints(self) -> list[Endpoint]:
        """Flat list of all endpoints across all events."""
        all_eps = []
        for eps in self.events.values():
            all_eps.extend(eps)
        return all_eps

    @property
    def event_names(self) -> list[str]:
        """List of all event names."""
        return list(self.events.keys())

    def get_event(self, event_name: str) -> list[Endpoint]:
        """Get all endpoints for an event."""
        return self.events.get(event_name, [])

    def get_by_name(self, name: str) -> Endpoint | None:
        """Find endpoint by name across all events."""
        for eps in self.events.values():
            for ep in eps:
                if ep.name == name:
                    return ep
        return None

    def get_by_path(self, path: str) -> Endpoint | None:
        """Find endpoint by path across all events."""
        for eps in self.events.values():
            for ep in eps:
                if ep.path == path:
                    return ep
        return None

    @property
    def writable_endpoints(self) -> list[Endpoint]:
        """Flat list of all writable endpoints."""
        return [ep for ep in self.endpoints if ep.writable]

    @property
    def unique_paths(self) -> set[str]:
        """Set of unique endpoint paths (for efficient polling)."""
        return {ep.path for ep in self.endpoints}
