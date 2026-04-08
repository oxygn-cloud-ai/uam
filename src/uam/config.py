"""Configuration loading for uam."""

import json
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

CONFIG_DIR = Path.home() / ".uam"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_PORT = 5100
DEFAULT_HOST = "127.0.0.1"

# Schemes accepted for local backend URLs. file://, ftp://, gopher://, etc.
# are rejected so a malicious caller cannot smuggle a non-HTTP target into
# `local.servers` and have discovery probe it.
_ALLOWED_LOCAL_SCHEMES = {"http", "https"}

# Issue #45: serializes the load → mutate → save flow inside `add_local_server`
# so two concurrent threads (e.g. two POSTs to /config/local-servers off-loaded
# via asyncio.to_thread) cannot lose updates by interleaving. The endpoint runs
# this helper in a worker thread so a process-level threading.Lock is the right
# primitive — asyncio.Lock would not protect against the thread pool.
_config_write_lock = threading.Lock()


def get_config() -> dict:
    """Load config from ~/.uam/config.json, return defaults if missing.

    Pure read — does not write to disk. Use ensure_config_exists() at startup
    to materialize the file on first run.
    """
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return default_config()


def ensure_config_exists() -> Path:
    """Materialize ~/.uam/config.json with default content if it does not exist.

    Idempotent: existing user config is never overwritten. Creates the parent
    directory if needed. Called from __main__.main() at startup so users
    always have a real file to edit, instead of running forever on the
    in-memory defaults returned by get_config().
    """
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(default_config(), indent=2) + "\n")
    return CONFIG_PATH


def _normalize_local_server_url(url: str) -> str:
    """Normalize a local backend URL.

    Accepts host[:port] and http(s)://host[:port]. Returns canonical
    `scheme://host[:port]` form (lowercased host, no path/query/fragment,
    no userinfo, no trailing slash).

    Rejects:
        - empty / whitespace-only input (issue: malformed config)
        - non-http(s) schemes (issue: SSRF / file://)
        - URLs with userinfo (issue #51: persists plaintext credentials)
        - URLs with a non-trivial path, query, or fragment (issue #50:
          breaks downstream `/v1/models` and `/api/tags` URL construction
          and dedup)
        - URLs with no host
    """
    if not url or not url.strip():
        raise ValueError("url must not be empty")
    url = url.strip()

    # If no scheme, prepend http://. urlparse considers "host:port" as
    # scheme="host", path="port", which we explicitly want to override.
    if "://" not in url:
        # Issue #47: reject bare scheme keywords like "http:" / "https:"
        # before they get auto-prepended into "http://http:" (which would
        # otherwise parse as host="http", port=None and silently pass).
        bare = url.rstrip(":").lower()
        if bare in _ALLOWED_LOCAL_SCHEMES and url.endswith(":"):
            raise ValueError(f"url has no host: {url!r}")
        url = "http://" + url

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_LOCAL_SCHEMES:
        raise ValueError(
            f"unsupported scheme: {parsed.scheme!r} (allowed: {sorted(_ALLOWED_LOCAL_SCHEMES)})"
        )
    if not parsed.hostname:
        raise ValueError(f"url has no host: {url!r}")
    # Reject embedded credentials. config.json must store env-var names
    # only — never plaintext secrets. (issue #51)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            "url must not contain userinfo (use API keys via api_key_env)"
        )
    # Reject paths, queries, fragments. Discovery builds endpoints by
    # appending /v1/models and /api/tags to this URL; a path here would
    # produce broken concatenations. A bare "/" is allowed and stripped.
    if parsed.path not in ("", "/"):
        raise ValueError(
            f"url must not contain a path: {parsed.path!r}"
        )
    if parsed.query:
        raise ValueError("url must not contain a query string")
    if parsed.fragment:
        raise ValueError("url must not contain a fragment")

    # Rebuild canonical form so case-only differences in scheme/host
    # collapse to the same key for dedup.
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    # parsed.port may raise ValueError on out-of-range; surface that as
    # the same kind of validation error.
    try:
        port = parsed.port
    except ValueError as e:
        raise ValueError(f"invalid port: {e}") from e

    netloc = f"[{host}]" if ":" in host else host  # bracket IPv6 literals
    if port is not None:
        netloc += f":{port}"

    return f"{scheme}://{netloc}"


def add_local_server(url: str, api_format: str = "openai") -> list[dict]:
    """Add a remote local-backend server to ~/.uam/config.json.

    Loads the existing config (or default), normalizes the URL via
    `_normalize_local_server_url` (which strictly validates scheme, host,
    and rejects userinfo / paths / queries / fragments), appends to
    `local.servers` if not already present, and atomically writes the
    file back. Returns the updated server list.

    Issue #45: the load → mutate → save flow is serialized via
    `_config_write_lock` so two concurrent callers (e.g. two POSTs to
    /config/local-servers off-loaded via asyncio.to_thread) cannot lose
    updates by interleaving.

    The proxy and router cache config in memory; the caller is expected
    to POST /refresh after this call so the new backend is discovered.
    """
    # Validate the URL up front (outside the lock) so cheap rejections
    # don't contend with concurrent valid writes.
    normalized = _normalize_local_server_url(url)

    with _config_write_lock:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
        else:
            cfg = default_config()

        local_cfg = cfg.setdefault("local", {})
        servers = local_cfg.setdefault("servers", [])

        # Dedup against existing entries (string or dict form). Compare
        # normalized so case-only or trailing-slash differences collapse.
        for existing in servers:
            existing_url = existing if isinstance(existing, str) else existing.get("url", "")
            try:
                if _normalize_local_server_url(existing_url) == normalized:
                    return servers
            except ValueError:
                # Skip malformed pre-existing entries rather than blow up.
                continue

        servers.append({"url": normalized, "api_format": api_format})

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to a sibling temp file then rename so a
        # crash mid-write cannot leave a half-written config on disk.
        # `with_suffix(CONFIG_PATH.suffix + ".tmp")` produces
        # `config.json.tmp`, NOT `config.tmp` — the latter would be the
        # naive `.with_suffix(".tmp")` footgun.
        tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(cfg, indent=2) + "\n")
        os.replace(tmp, CONFIG_PATH)

        return servers


def default_config() -> dict:
    return {
        "listen": f"{DEFAULT_HOST}:{DEFAULT_PORT}",
        "anthropic": {
            "url": "https://api.anthropic.com",
            "api_key_env": "ANTHROPIC_API_KEY_REAL",
            "timeout": 600,
        },
        "runpod": {"accounts": {}, "timeout": 300},
        "openrouter": {
            "url": "https://openrouter.ai/api",
            "api_key_env": "OPENROUTER_API_KEY",
            "timeout": 300,
        },
        "local": {"probe_ports": [11434, 8000, 8080, 2242, 5000, 3000], "servers": [], "timeout": 120},
        "default_backend": "anthropic",
    }


# Default timeout per backend (seconds), used as fallback
_DEFAULT_TIMEOUTS = {
    "anthropic": 600,
    "runpod": 300,
    "openrouter": 300,
    "local": 120,
}


def get_backend_timeout(config: dict, backend: str) -> int:
    """Return the timeout (seconds) for a backend, with sensible defaults."""
    backend_cfg = config.get(backend, {})
    if isinstance(backend_cfg, dict) and "timeout" in backend_cfg:
        return int(backend_cfg["timeout"])
    return _DEFAULT_TIMEOUTS.get(backend, 300)


def resolve_key(env_var_name: str) -> str:
    """Resolve an API key from an environment variable name. Never logs the value."""
    return os.environ.get(env_var_name, "")


def parse_listen(config: dict) -> tuple[str, int]:
    """Parse listen address from config."""
    listen = config.get("listen", f"{DEFAULT_HOST}:{DEFAULT_PORT}")
    if ":" in listen:
        host, port_str = listen.rsplit(":", 1)
        return host, int(port_str)
    return DEFAULT_HOST, int(listen)
