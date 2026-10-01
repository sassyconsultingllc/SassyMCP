# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-7P4A6IPPVMRF
"""Tests for sassymcp._httpbind (HTTP bind resolution) and config live-reload."""
from __future__ import annotations

import json

import pytest

from sassymcp._httpbind import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    BindConfigError,
    HttpBind,
    resolve_http_bind,
)
from sassymcp.modules import runtime_config as rc


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point runtime_config at a temp config.json with a clean env."""
    fake = tmp_path / "config.json"
    monkeypatch.setattr(rc, "CONFIG_FILE", fake)
    monkeypatch.setattr(rc, "_config", {})
    for var in ("SASSYMCP_HOST", "SASSYMCP_PORT", "SASSYMCP_SSL"):
        monkeypatch.delenv(var, raising=False)
    return fake


def _write(path, data) -> None:
    path.write_text(json.dumps(data))


# ── defaults ──────────────────────────────────────────────────────────

def test_defaults(isolated_config):
    bind = resolve_http_bind([])
    assert bind == HttpBind(host=DEFAULT_HOST, port=DEFAULT_PORT, ssl=False)
    assert bind.host == "127.0.0.1"
    assert bind.port == 21001
    assert bind.url == "http://127.0.0.1:21001"
    assert bind.mcp_url == "http://127.0.0.1:21001/mcp"


def test_ssl_url_scheme():
    bind = HttpBind(host="127.0.0.1", port=21001, ssl=True)
    assert bind.scheme == "https"
    assert bind.url == "https://127.0.0.1:21001"
    assert bind.mcp_url == "https://127.0.0.1:21001/mcp"


# ── precedence: CLI > env > config > default ──────────────────────────

def test_config_port_used(isolated_config):
    _write(isolated_config, {"http.port": 22004, "http.host": "0.0.0.0"})
    bind = resolve_http_bind([])
    assert (bind.host, bind.port) == ("0.0.0.0", 22004)


def test_env_overrides_config(isolated_config, monkeypatch):
    _write(isolated_config, {"http.port": 22004})
    monkeypatch.setenv("SASSYMCP_PORT", "22003")
    bind = resolve_http_bind([])
    assert bind.port == 22003


def test_env_host(isolated_config, monkeypatch):
    monkeypatch.setenv("SASSYMCP_HOST", "192.168.1.10")
    assert resolve_http_bind([]).host == "192.168.1.10"


def test_cli_overrides_everything(isolated_config, monkeypatch):
    _write(isolated_config, {"http.port": 22004, "http.host": "0.0.0.0"})
    monkeypatch.setenv("SASSYMCP_PORT", "22003")
    monkeypatch.setenv("SASSYMCP_HOST", "192.168.1.10")
    bind = resolve_http_bind(["--port", "22002", "--host", "10.0.0.5"])
    assert (bind.host, bind.port) == ("10.0.0.5", 22002)


def test_cli_ssl_flag(isolated_config):
    assert resolve_http_bind(["--ssl"]).ssl is True
    assert resolve_http_bind([]).ssl is False


def test_env_ssl(isolated_config, monkeypatch):
    monkeypatch.setenv("SASSYMCP_SSL", "1")
    assert resolve_http_bind([]).ssl is True


def test_config_ssl(isolated_config):
    _write(isolated_config, {"http.ssl": True})
    assert resolve_http_bind([]).ssl is True


# ── invalid values fail fast ──────────────────────────────────────────

@pytest.mark.parametrize("bad", ["abc", "0", "-1", "99999", "22.5"])
def test_invalid_env_port(isolated_config, monkeypatch, bad):
    monkeypatch.setenv("SASSYMCP_PORT", bad)
    with pytest.raises(BindConfigError):
        resolve_http_bind([])


def test_empty_env_port_means_unset(isolated_config, monkeypatch):
    # An empty env var is treated as "not set" (falls through to config /
    # defaults) rather than an error — standard env-var convention.
    monkeypatch.setenv("SASSYMCP_PORT", "")
    assert resolve_http_bind([]).port == DEFAULT_PORT


def test_invalid_config_port(isolated_config):
    _write(isolated_config, {"http.port": "not-a-port"})
    with pytest.raises(BindConfigError):
        resolve_http_bind([])


def test_invalid_cli_port_range(isolated_config):
    with pytest.raises(BindConfigError):
        resolve_http_bind(["--port", "0"])


def test_non_numeric_cli_port_rejected_by_argparse(isolated_config):
    with pytest.raises(SystemExit):
        resolve_http_bind(["--port", "abc"])


# ── config live reload ────────────────────────────────────────────────

def test_reload_picks_up_changes(isolated_config):
    _write(isolated_config, {"http.port": 22004})
    rc.reload()
    assert rc.get("http.port") == 22004
    assert resolve_http_bind([]).port == 22004

    _write(isolated_config, {"http.port": 22005})
    rc.reload()
    assert resolve_http_bind([]).port == 22005


def test_reload_keeps_last_good_on_corrupt_file(isolated_config):
    _write(isolated_config, {"http.port": 22004})
    rc.reload()
    assert rc.get("http.port") == 22004

    isolated_config.write_text("{not valid json")
    rc.reload()  # must not raise, must not wipe
    assert rc.get("http.port") == 22004
