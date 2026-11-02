"""Config shape, names and the action surface: one ERROR line + exit 1 for every mistake."""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

import pytest
from conftest import DEMO_MAP, logged
from zelos_sdk.actions import ActionsRegistry
from zelos_sdk.extensions.actions import get_standalone_actions

from zelos_extension_http import ACTION_PREFIX, actions
from zelos_extension_http.cli import app
from zelos_extension_http.endpoint_map import (
    EndpointMap,
    coerce_value,
    encode_value,
    extract_json_value,
    parse_value,
)

ROOT = Path(__file__).parents[1]
URL = "http://127.0.0.1:8080"


def resolve(config: dict) -> list:
    return app.build_clients(app.resolve_servers(config, app.resolve_advanced(config)))


def test_defaults_match_schema():
    schema = json.loads((ROOT / "config.schema.json").read_text())
    props = schema["properties"]
    for defaults, obj in (
        (app.ADVANCED_DEFAULTS, props["advanced"]),
        (app.SERVER_DEFAULTS, props["servers"]["items"]),
    ):
        assert {k: v["default"] for k, v in obj["properties"].items()} == defaults


def test_manifest_schema_and_actions_agree():
    """One name everywhere: manifest, action prefix, default trace prefix, auto-configure hook."""
    manifest = tomllib.loads((ROOT / "extension.toml").read_text())
    schema = json.loads((ROOT / "config.schema.json").read_text())
    assert manifest["name"] == ACTION_PREFIX == app.ADVANCED_DEFAULTS["prefix"] == "HTTP"
    assert schema["ui:options"]["autoconfig"] == f"{ACTION_PREFIX}/auto_config"
    assert "auto_config" in get_standalone_actions()
    flags = {
        name: (getattr(actions, name)._action.read_only, getattr(actions, name)._action.hidden)
        for name in actions.register_actions(ActionsRegistry())
    }
    assert flags == {
        "auto_config": (True, True),
        "get_status": (True, True),
        "list_endpoints": (True, True),
        "read_endpoint": (True, False),
        "send_request": (False, False),
        "write_endpoint": (False, False),
    }


def test_legacy_config_exits_naming_keys_never_values(tmp_path, monkeypatch, caplog):
    saved = tmp_path / "config.json"
    saved.write_text(
        json.dumps(
            {
                "demo": False,
                "base_url": URL,
                "headers": '{"Authorization": "Bearer s3cret"}',
                "verify_ssl": True,
            }
        )
    )
    monkeypatch.setenv("ZELOS_CONFIG_PATH", str(saved))
    with pytest.raises(SystemExit) as exc:
        app.run_app_mode()
    assert exc.value.code == 1
    assert logged(caplog, logging.ERROR) == [
        "Config format changed: servers are now listed under servers[] with shared settings "
        "under advanced (found top-level base_url, headers, verify_ssl); reconfigure the extension"
    ]


def test_saved_config_off_the_schema_exits(tmp_path, monkeypatch, caplog):
    saved = tmp_path / "config.json"
    saved.write_text(json.dumps({"servers": [{"base_url": URL, "poll_interval": 0}]}))
    monkeypatch.setenv("ZELOS_CONFIG_PATH", str(saved))
    monkeypatch.chdir(ROOT)
    with pytest.raises(SystemExit) as exc:
        app.run_app_mode()
    assert exc.value.code == 1
    (error,) = logged(caplog, logging.ERROR)
    assert error.startswith("Invalid configuration: ") and "0 is less than the minimum" in error


@pytest.mark.parametrize(
    ("servers", "advanced", "reason"),
    [
        ([{"base_url": URL}], {"prefix": "HTTP/x"}, "Invalid Prefix 'HTTP/x': '/' is not allowed"),
        ([{"base_url": URL, "name": "log"}], {}, "Invalid server Name 'log': reserved"),
        ([{"base_url": URL, "name": "http_log"}], {}, "Invalid server Name 'http_log': reserved"),
        ([{"base_url": URL, "name": "a.b"}], {}, "Invalid server Name 'a.b': '.' is not allowed"),
        (
            [{"base_url": URL, "name": "wx"}, {"base_url": URL, "name": "wx"}],
            {},
            "Duplicate server name 'wx'; set Name on one of them.",
        ),
        (
            [{"base_url": URL}, {"base_url": URL + "/v2"}],
            {},
            "Duplicate server name '127_0_0_1_8080'; set Name on one of them.",
        ),
        ([{"base_url": "http://log:80"}], {}, "Server name 'log' is reserved"),
        ([{"base_url": "ftp://gw"}], {}, "must be http:// or https:// and a host"),
        ([{"base_url": URL, "paths": [" ", ""]}], {}, "Server '127_0_0_1': nothing to poll"),
        ([{"base_url": URL, "paths": ["api/x"]}], {}, "path 'api/x' must start with '/'"),
        ([{"base_url": URL, "paths": ["/"]}], {}, "path '/' must start with '/' and name"),
        (
            [{"base_url": URL, "paths": ["/a.b", "/a_b"]}],
            {},
            "paths '/a.b' and '/a_b' both trace as event 'a_b'",
        ),
        (
            [{"base_url": URL, "paths": ["/environment"], "endpoint_map_file": DEMO_MAP}],
            {},
            "path '/environment' and the endpoint map both trace event 'environment'",
        ),
        (
            [{"base_url": URL, "paths": ["/x"], "endpoint_map_file": "/nonexistent.json"}],
            {},
            "Failed to load endpoint map '/nonexistent.json'",
        ),
        (
            [{"base_url": URL, "paths": ["/x"], "cert_file": "/nonexistent.pem"}],
            {},
            "Server '127_0_0_1': cannot load its TLS files",
        ),
        (
            [{"base_url": URL, "paths": ["/x"]}],
            {"key_file": "k.pem"},
            "a key file needs its certificate file",
        ),
    ],
)
def test_invalid_server_config_exits(caplog, servers, advanced, reason):
    """Validated before anything starts; an error names the server and the fix."""
    with pytest.raises(SystemExit) as exc:
        app.run_config({"servers": servers, "advanced": advanced})
    assert exc.value.code == 1
    (error,) = logged(caplog, logging.ERROR)
    assert reason in error


def test_credentials_in_the_base_url_are_refused_never_echoed(caplog):
    with pytest.raises(SystemExit):
        resolve({"servers": [{"base_url": "http://admin:s3cret@gw:8080", "paths": ["/x"]}]})
    assert logged(caplog, logging.ERROR) == [
        "Server 'gw': remove the user name / password from the Base URL; "
        "the extension sends no credentials"
    ]


def test_default_names_take_the_port_only_when_hosts_collide():
    servers = app.resolve_servers(
        {
            "servers": [
                {"base_url": "http://gw:8080", "paths": ["/x"]},
                {"base_url": "https://gw/api", "paths": ["/x"]},
                {"base_url": "http://192.168.1.10", "paths": ["/x"]},
                {"base_url": "http://gw:9000", "name": "line 2", "paths": ["/x"]},
            ]
        },
        app.ADVANCED_DEFAULTS,
    )
    assert [s["name"] for s in servers] == ["gw_8080", "gw_443", "192_168_1_10", "line 2"]


def test_a_server_inherits_timeout_and_tls_files_from_advanced():
    advanced = {**app.ADVANCED_DEFAULTS, "ca_file": "ca.pem", "cert_file": "c.pem", "key_file": "k"}
    own = {**app.SERVER_DEFAULTS, "timeout": 2.0, "cert_file": "mine.pem"}
    assert app.inherit(app.SERVER_DEFAULTS, advanced) == {
        "timeout": 5.0,
        "ca_file": "ca.pem",
        "cert_file": "c.pem",
        "key_file": "k",
    }
    # The certificate and key move as a pair: a mix would be a key mismatch.
    assert app.inherit(own, advanced) == {
        "timeout": 2.0,
        "ca_file": "ca.pem",
        "cert_file": "mine.pem",
        "key_file": "",
    }


@pytest.mark.parametrize(
    ("events", "reason"),
    [
        ({"env.indoor": [{"name": "t", "path": "/t"}]}, "Invalid event name 'env.indoor'"),
        ({"_server": [{"name": "t", "path": "/t"}]}, "Invalid event name '_server'"),
        ({"env": [{"name": "a.b", "path": "/t"}]}, "invalid endpoint name 'a.b'"),
        ({"env": [{"name": "time_ns", "path": "/t"}]}, "invalid endpoint name 'time_ns'"),
        ({"env": [{"name": "t", "path": "/t"}, {"name": "t", "path": "/u"}]}, "duplicate"),
        ({"env": [{"name": "t"}]}, "endpoint 0 needs a name and a path"),
        ({"env": [{"name": "t", "path": "t"}]}, "must start with '/'"),
        ({"env": [{"name": "t", "path": "/t", "datatype": "real"}]}, "Invalid datatype 'real'"),
        ({"env": [{"name": "t", "path": "/t", "method": "FETCH"}]}, "Invalid method 'FETCH'"),
    ],
)
def test_endpoint_map_rejects_names_that_would_misfile_data(events, reason):
    with pytest.raises(ValueError, match=reason):
        EndpointMap.from_dict({"events": events})


def test_endpoint_lookup_by_event_when_a_name_repeats():
    endpoint_map = EndpointMap.from_dict(
        {"events": {"a": [{"name": "t", "path": "/a"}], "b": [{"name": "t", "path": "/b"}]}}
    )
    assert endpoint_map.get_by_name("b/t")[1].path == "/b"
    with pytest.raises(ValueError, match="use one of: a/t, b/t"):
        endpoint_map.get_by_name("t")


def test_values_read_as_traced_and_write_in_map_units():
    body = {"data": {"sensors": [{"v": 1}, {"v": 2}]}}
    assert extract_json_value(body, "data.sensors.1.v") == 2
    assert extract_json_value(body, "data.sensors.5.v") is None
    # Read: out-of-range, unparseable and absent become no value, not a failed event.
    assert coerce_value(300, "uint8") is None
    assert coerce_value("n/a", "float32") is None
    assert coerce_value({"x": 1}, "float64") is None
    assert coerce_value("false", "bool") is False
    assert coerce_value(2**63 + 1, "uint64") == 2**63 + 1
    assert coerce_value(455, "uint16", scale=0.1) == 45
    # Write: the action's text, range-checked, then divided by the scale.
    assert parse_value("45.5", "float32") == 45.5
    assert parse_value("on", "bool") is True
    with pytest.raises(ValueError, match="out of range for uint8"):
        parse_value("300", "uint8")
    with pytest.raises(ValueError, match="Cannot parse 'abc' as float32"):
        parse_value("abc", "float32")
    assert encode_value(45, "uint16", scale=0.1) == 450
    assert encode_value(4.5, "float32", scale=0.5) == 9.0
