"""Tests for uam.config module."""

import json

import uam.config as config_mod
from uam.config import default_config, get_config, parse_listen, resolve_key


def test_default_config_structure():
    cfg = default_config()
    assert set(cfg.keys()) == {"listen", "anthropic", "runpod", "openrouter", "local", "default_backend"}
    assert cfg["listen"] == "127.0.0.1:5100"
    assert isinstance(cfg["local"]["probe_ports"], list)
    assert isinstance(cfg["local"]["servers"], list)


def test_get_config_returns_default_when_no_file():
    assert not config_mod.CONFIG_PATH.exists()
    assert get_config() == default_config()


def test_get_config_loads_from_disk():
    custom = {"listen": "0.0.0.0:7777", "custom_key": True}
    config_mod.CONFIG_PATH.write_text(json.dumps(custom))
    result = get_config()
    assert result == custom


def test_get_config_custom_listen_port():
    custom = {"listen": "0.0.0.0:9999"}
    config_mod.CONFIG_PATH.write_text(json.dumps(custom))
    result = get_config()
    assert result["listen"] == "0.0.0.0:9999"


def test_resolve_key_from_env(monkeypatch):
    monkeypatch.setenv("TEST_KEY_VAR", "sk-secret")
    assert resolve_key("TEST_KEY_VAR") == "sk-secret"


def test_resolve_key_missing_env():
    assert resolve_key("NONEXISTENT_VAR_12345") == ""


def test_parse_listen_host_and_port():
    assert parse_listen({"listen": "0.0.0.0:8080"}) == ("0.0.0.0", 8080)


def test_parse_listen_default_when_missing():
    assert parse_listen({}) == ("127.0.0.1", 5100)


def test_parse_listen_port_only():
    assert parse_listen({"listen": "9999"}) == ("127.0.0.1", 9999)


def test_parse_listen_ipv6_style():
    assert parse_listen({"listen": "::1:5100"}) == ("::1", 5100)
