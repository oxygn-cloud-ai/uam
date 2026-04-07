"""SEC-002: Auth token management for mutating proxy endpoints.

The proxy listens only on 127.0.0.1 and already rejects non-localhost
Host headers (host_header_middleware). However a malicious page in the
user's browser can still issue cross-origin requests to localhost, and
DNS rebinding remains a theoretical concern. To defend POST /state and
POST /refresh (which mutate persistent state) we require **either**:

  1. A valid bearer token in the `Authorization` header, OR
  2. A request that is provably not browser-originated:
     - Host header = 127.0.0.1 / localhost (already enforced upstream)
     - No `Origin` header (browsers always send Origin on cross-origin
       requests; CLI clients like curl / hooks do not)

Rule 2 lets Claude Code's built-in slash command hooks call the proxy
without ever knowing the token, while still blocking browser-based
attackers (who cannot suppress the Origin header).

The token is generated on first read into ~/.uam/token with mode 0600.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger("uam.token")

TOKEN_PATH = Path.home() / ".uam" / "token"
_TOKEN_CACHE: str | None = None


def get_or_create_token() -> str:
    """Return the auth token, generating it on first call.

    The token is 32 random bytes hex-encoded (64 chars). The file is
    written with mode 0600 so only the owning user can read it.
    """
    global _TOKEN_CACHE
    if _TOKEN_CACHE is not None:
        return _TOKEN_CACHE

    if TOKEN_PATH.exists():
        try:
            token = TOKEN_PATH.read_text().strip()
            if token:
                _TOKEN_CACHE = token
                return token
        except OSError as e:
            logger.warning("Failed to read token file %s: %s", TOKEN_PATH, e)

    # Generate a new token.
    token = secrets.token_hex(32)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive permissions atomically.
    fd = os.open(str(TOKEN_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    # Re-chmod in case the file already existed with looser perms.
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass

    _TOKEN_CACHE = token
    logger.info("Generated new uam auth token at %s", TOKEN_PATH)
    return token


def reset_cache() -> None:
    """Test helper: clear the in-process token cache."""
    global _TOKEN_CACHE
    _TOKEN_CACHE = None


def request_is_authenticated(headers, expected_token: str) -> bool:
    """Return True if the request carries a valid bearer token OR is
    provably not browser-originated.

    `headers` may be any case-insensitive mapping (e.g. aiohttp's
    request.headers / CIMultiDict, or a plain dict in tests).
    """
    # Normalize headers for case-insensitive lookup.
    try:
        items = headers.items()
    except AttributeError:
        return False
    lowered = {str(k).lower(): str(v) for k, v in items}

    # Path 1: bearer token.
    auth = lowered.get("authorization", "")
    if auth.lower().startswith("bearer "):
        presented = auth[7:].strip()
        if presented and secrets.compare_digest(presented, expected_token):
            return True

    # Path 2: no Origin header (CLI request, not a browser).
    # Host is already validated by host_header_middleware upstream.
    if "origin" not in lowered:
        return True

    return False
