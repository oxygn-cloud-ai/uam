"""Model router — discovery orchestration and model resolution."""

import asyncio
import logging

import aiohttp

logger = logging.getLogger("uam.router")

from uam.config import get_backend_timeout, get_config, resolve_key
from uam.discovery.anthropic import ALIASES as ANTHROPIC_ALIASES
from uam.discovery.anthropic import discover_anthropic
from uam.discovery.local import discover_local
from uam.discovery.openrouter import discover_openrouter
from uam.discovery.runpod import discover_runpod
from uam.state import load_state, save_state, sync_state_with_routes


class ModelRouter:
    def __init__(self, config: dict):
        self.config = config
        self.routes: dict[str, dict] = {}
        self.session: aiohttp.ClientSession | None = None

    async def start(self, skip_discovery: bool = False):
        logger.info("Starting model discovery...")
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=600, connect=10)
        )
        # Anthropic models are always registered
        self.routes.update(discover_anthropic(self.config))

        if not skip_discovery:
            await self.discover()

        # Sync state with discovered routes
        self._sync_state()
        logger.info(f"{self.model_count()} models available")

    async def stop(self):
        if self.session:
            await self.session.close()

    async def discover(self):
        """Run async discovery for all configured backends."""
        tasks = []
        if self.config.get("runpod", {}).get("accounts"):
            tasks.append(discover_runpod(self.config, self.session))
        if self.config.get("openrouter"):
            tasks.append(discover_openrouter(self.config, self.session))
        if self.config.get("local"):
            tasks.append(discover_local(self.config, self.session))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, dict):
                self.routes.update(result)
            elif isinstance(result, Exception):
                logger.error(f"Discovery error: {result}")

    async def refresh(self):
        """Re-read config from disk and rebuild all routes from scratch.

        Re-reading config is required so that newly-added local backends
        (e.g. via POST /config/local-servers) and any other config edits
        are picked up without a full proxy restart. Anthropic routes are
        rebuilt from the fresh config, not preserved, so changed
        api_key_env / url / timeout values also take effect on /refresh.
        """
        self.config = get_config()
        self.routes = dict(discover_anthropic(self.config))
        await self.discover()
        self._sync_state()

    def _sync_state(self):
        """Sync models.json with current discovered routes."""
        state = load_state()
        state = sync_state_with_routes(list(self.routes.keys()), state)
        save_state(state)

    def resolve(self, model: str) -> dict | None:
        """Resolve a model ID to a backend route."""
        # Direct match
        if model in self.routes:
            return self.routes[model]

        # Anthropic alias match
        if model in ANTHROPIC_ALIASES:
            target = ANTHROPIC_ALIASES[model]
            if target in self.routes:
                return self.routes[target]

        # Fall through to default backend
        default = self.config.get("default_backend", "anthropic")
        if default == "anthropic":
            anthropic_cfg = self.config.get("anthropic", {})
            api_key = resolve_key(anthropic_cfg.get("api_key_env", ""))
            return {
                "backend": "anthropic",
                "url": anthropic_cfg.get("url", "https://api.anthropic.com"),
                "api_key": api_key,
                "original_model": model,
                "api_format": "anthropic",
                "timeout": get_backend_timeout(self.config, "anthropic"),
            }
        return None

    def model_count(self) -> int:
        return len(self.routes)

    def list_models(self) -> list[dict]:
        """Return sorted list of all known models."""
        return [
            {
                "id": key,
                "backend": route["backend"],
                "original_model": route["original_model"],
            }
            for key, route in sorted(self.routes.items())
        ]
