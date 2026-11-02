"""Discovery: a path's JSON response traced as it comes, without a map.

An object becomes one event with a field per scalar key; a nested object becomes
a sub-event (`api/sensors/wind`); an array of scalars becomes fields `<key>_<i>`.
Names go through `zelos_sdk.sanitize_name`, so any key traces.
"""

from __future__ import annotations

from typing import Any

import zelos_sdk

#: Array items traced per key; the rest are reported once.
MAX_ARRAY_ITEMS = 16

#: Every row already carries the time under this name; the SDK refuses it as a field.
TIME_FIELD = "time_ns"

#: Inferred field kinds, as `list_endpoints` reports them.
BOOL, NUMBER, STRING = "bool", "float64", "string"

#: (warn-once key, message): something a body held that is not traced.
Note = tuple[str, str]


def event_name(path: str) -> str:
    """`/api/sensors` -> `api/sensors`: each segment sanitized; "" for the root."""
    return "/".join(zelos_sdk.sanitize_name(s) for s in path.split("/") if s)


def kind_of(value: Any) -> str | None:
    """The traced kind of a JSON scalar; None for null, objects and arrays."""
    if isinstance(value, bool):  # before int: bool is an int
        return BOOL
    if isinstance(value, int | float):
        return NUMBER
    if isinstance(value, str):
        return STRING
    return None


def flatten(event: str, body: Any) -> tuple[dict[str, dict[str, Any]], list[Note]]:
    """A JSON body as {event: {field: scalar}}, plus notes on what was left out.

    A body that is not an object traces as field `value` (`value_<i>` for an array).
    Nulls are skipped silently: a field without a value is a gap, not a fault.
    """
    flat = _Flattener()
    flat.walk(event, body if isinstance(body, dict) else {"value": body})
    return flat.rows, flat.notes


class _Flattener:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.notes: list[Note] = []
        self.seen: set[str] = set()

    def note(self, key: str, message: str) -> None:
        self.notes.append((key, message))

    def walk(self, event: str, obj: dict[str, Any]) -> None:
        self.seen.add(event)
        fields: dict[str, Any] = {}
        keys: dict[str, str] = {}  # field -> the JSON key it came from

        def put(field: str, value: Any, key: str) -> None:
            if field == TIME_FIELD:
                self.note(
                    f"{event}.{field}",
                    f"Event '{event}': key '{key}' not traced; '{TIME_FIELD}' is the "
                    "trace's time column, map it under another name",
                )
            elif field in fields:
                self.note(
                    f"{event}.{field}",
                    f"Event '{event}': keys '{keys[field]}' and '{key}' both trace as "
                    f"field '{field}'; '{key}' not traced",
                )
            else:
                fields[field], keys[field] = value, key

        for key, value in obj.items():
            if isinstance(value, dict):
                sub = f"{event}/{zelos_sdk.sanitize_name(key)}"
                if sub in self.seen:
                    self.note(sub, f"Event '{sub}': two objects trace as it; '{key}' not traced")
                else:
                    self.walk(sub, value)
            elif isinstance(value, list):
                self.array(event, key, value, put)
            elif kind_of(value):
                put(zelos_sdk.sanitize_name(key, kind="field"), value, key)
        if fields:
            self.rows[event] = fields

    def array(self, event: str, key: str, items: list[Any], put: Any) -> None:
        field = zelos_sdk.sanitize_name(key, kind="field")
        for i, item in enumerate(items[:MAX_ARRAY_ITEMS]):
            if kind_of(item):
                put(f"{field}_{i}", item, key)
            elif item is not None:
                self.note(
                    f"{event}.{field}[]",
                    f"Event '{event}': array '{key}' holds objects or arrays, not traced; "
                    "map them with json_path",
                )
                return
        if len(items) > MAX_ARRAY_ITEMS:
            self.note(
                f"{event}.{field}[]",
                f"Event '{event}': array '{key}' has {len(items)} items; "
                f"the first {MAX_ARRAY_ITEMS} are traced",
            )
