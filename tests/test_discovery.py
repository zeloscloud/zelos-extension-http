"""Discovery's JSON flattening, at the helper seam."""

from __future__ import annotations

from zelos_extension_http.discovery import MAX_ARRAY_ITEMS, event_name, flatten


def test_event_names_follow_the_path():
    assert event_name("/api/sensors") == "api/sensors"
    assert event_name("/a b/c.d/") == "a b/c_d"
    assert event_name("//data//v1?unit=si") == "data/v1_unit_si"
    assert event_name("/") == ""


def test_objects_nest_arrays_index_and_odd_keys_are_reported():
    body = {
        "temperature": 21.5,
        "ok": True,
        "label": "roof",
        "missing": None,
        "wind": {"speed": 3.1, "gust": {"max": 7}},
        "cells": [3.7, 3.6],
        "samples": list(range(MAX_ARRAY_ITEMS + 4)),
        "rows": [{"a": 1}],
        "time_ns": 5,
        "a.b": 1,
        "a_b": 2,
    }
    rows, notes = flatten("api/sensors", body)
    assert rows == {
        "api/sensors": {
            "temperature": 21.5,
            "ok": True,
            "label": "roof",
            "cells_0": 3.7,
            "cells_1": 3.6,
            **{f"samples_{i}": i for i in range(MAX_ARRAY_ITEMS)},
            "a_b": 1,
        },
        "api/sensors/wind": {"speed": 3.1},
        "api/sensors/wind/gust": {"max": 7},
    }
    assert [message for _, message in notes] == [
        "Event 'api/sensors': array 'samples' has 20 items; the first 16 are traced",
        "Event 'api/sensors': array 'rows' holds objects or arrays, not traced; "
        "map them with json_path",
        "Event 'api/sensors': key 'time_ns' not traced; 'time_ns' is the trace's time column, "
        "map it under another name",
        "Event 'api/sensors': keys 'a.b' and 'a_b' both trace as field 'a_b'; 'a_b' not traced",
    ]


def test_a_body_that_is_not_an_object_traces_as_value():
    assert flatten("status", 42) == ({"status": {"value": 42}}, [])
    assert flatten("status", "ok") == ({"status": {"value": "ok"}}, [])
    assert flatten("levels", [1, 2]) == ({"levels": {"value_0": 1, "value_1": 2}}, [])
    assert flatten("empty", {}) == ({}, [])
