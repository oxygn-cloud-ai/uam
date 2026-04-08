"""Tests for uam.config module."""

import json

import pytest

import uam.config as config_mod
from uam.config import (
    add_local_server,
    default_config,
    ensure_config_exists,
    get_config,
    parse_listen,
    resolve_key,
)


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


# ---------------------------------------------------------------------------
# ensure_config_exists — first-run bootstrap
# ---------------------------------------------------------------------------


def test_ensure_config_exists_writes_default_when_missing(tmp_path, monkeypatch):
    """First run: config.json should be materialized with default content."""
    cfg_path = tmp_path / "fresh" / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    assert not cfg_path.exists()
    result_path = ensure_config_exists()

    assert cfg_path.exists()
    assert result_path == cfg_path
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk == default_config()


def test_ensure_config_exists_creates_parent_dir(tmp_path, monkeypatch):
    """Parent directory must be created if it does not exist."""
    cfg_path = tmp_path / "nested" / "deeper" / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    ensure_config_exists()

    assert cfg_path.parent.is_dir()
    assert cfg_path.exists()


def test_ensure_config_exists_does_not_overwrite_existing(tmp_path, monkeypatch):
    """Existing user config must be left untouched."""
    cfg_path = tmp_path / "existing" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    custom = {"listen": "0.0.0.0:1234", "anthropic": {"url": "https://x"}}
    cfg_path.write_text(json.dumps(custom))

    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    ensure_config_exists()

    assert json.loads(cfg_path.read_text()) == custom


def test_ensure_config_exists_idempotent(tmp_path, monkeypatch):
    """Calling twice in a row must produce the same on-disk file."""
    cfg_path = tmp_path / "idem" / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    ensure_config_exists()
    first = cfg_path.read_text()
    ensure_config_exists()
    second = cfg_path.read_text()

    assert first == second


# ---------------------------------------------------------------------------
# add_local_server — re-runnable backend addition
# ---------------------------------------------------------------------------


def test_add_local_server_appends_to_empty_servers(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    result = add_local_server("http://192.0.2.1:11434")

    on_disk = json.loads(cfg_path.read_text())
    servers = on_disk["local"]["servers"]
    assert len(servers) == 1
    assert servers[0]["url"] == "http://192.0.2.1:11434"
    assert servers[0]["api_format"] == "openai"
    assert result == servers


def test_add_local_server_normalizes_missing_scheme(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    add_local_server("192.0.2.50:11434")

    servers = json.loads(cfg_path.read_text())["local"]["servers"]
    assert servers[0]["url"] == "http://192.0.2.50:11434"


def test_add_local_server_strips_trailing_slash(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    add_local_server("http://192.0.2.1:11434/")

    servers = json.loads(cfg_path.read_text())["local"]["servers"]
    assert servers[0]["url"] == "http://192.0.2.1:11434"


def test_add_local_server_dedupes(tmp_path, monkeypatch):
    """Adding the same server twice (with/without trailing slash) is a no-op."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    add_local_server("http://192.0.2.1:11434")
    add_local_server("http://192.0.2.1:11434/")

    servers = json.loads(cfg_path.read_text())["local"]["servers"]
    assert len(servers) == 1


def test_add_local_server_preserves_other_config_keys(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)
    starting = default_config()
    starting["anthropic"]["api_key_env"] = "MY_CUSTOM_KEY"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(starting))

    add_local_server("http://192.0.2.1:11434")

    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["anthropic"]["api_key_env"] == "MY_CUSTOM_KEY"


def test_add_local_server_custom_api_format(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    add_local_server("http://192.0.2.1:8000", api_format="anthropic")

    servers = json.loads(cfg_path.read_text())["local"]["servers"]
    assert servers[0]["api_format"] == "anthropic"


def test_add_local_server_rejects_empty_url(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError):
        add_local_server("")
    with pytest.raises(ValueError):
        add_local_server("   ")


def test_add_local_server_rejects_bogus_scheme(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError):
        add_local_server("file:///etc/passwd")
    with pytest.raises(ValueError):
        add_local_server("ftp://x:21")


def test_add_local_server_rejects_userinfo(tmp_path, monkeypatch):
    """Issue #51: URLs with embedded credentials must not be persisted to disk.

    config.json is meant to store env-var names only, never plaintext secrets.
    """
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError, match="userinfo"):
        add_local_server("http://user:pass@192.0.2.1:11434")
    with pytest.raises(ValueError, match="userinfo"):
        add_local_server("http://admin@192.0.2.1:11434")


def test_add_local_server_rejects_path(tmp_path, monkeypatch):
    """Issue #50: paths break _openai_chat_url and dedup. Reject anything past
    the host:port portion."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError, match="path"):
        add_local_server("http://192.0.2.1:11434/some/path")
    with pytest.raises(ValueError, match="path"):
        add_local_server("http://192.0.2.1:11434/v1")


def test_add_local_server_rejects_query(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError, match="query"):
        add_local_server("http://192.0.2.1:11434?token=abc")


def test_add_local_server_rejects_fragment(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError, match="fragment"):
        add_local_server("http://192.0.2.1:11434#section")


def test_add_local_server_rejects_bare_scheme(tmp_path, monkeypatch):
    """Issue #47: 'http:' alone must not be accepted as a host."""
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    with pytest.raises(ValueError):
        add_local_server("http:")
    with pytest.raises(ValueError):
        add_local_server("http://")


def test_add_local_server_concurrent_writes_do_not_lose_updates(tmp_path, monkeypatch):
    """Issue #45: TOCTOU — concurrent add_local_server calls must not lose
    updates. Two threads racing to add different servers should both end up
    persisted."""
    import threading

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    # Pre-populate to avoid first-call cost dominating
    add_local_server("http://192.0.2.1:11434")

    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(i: int):
        try:
            barrier.wait()
            add_local_server(f"http://192.0.2.{i + 10}:11434")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    on_disk = json.loads(cfg_path.read_text())
    urls = {s["url"] for s in on_disk["local"]["servers"]}
    # Original + 8 new = 9
    assert urls == {"http://192.0.2.1:11434"} | {
        f"http://192.0.2.{i + 10}:11434" for i in range(8)
    }
