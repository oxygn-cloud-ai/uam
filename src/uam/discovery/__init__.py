from uam.discovery.anthropic import discover_anthropic
from uam.discovery.runpod import discover_runpod
from uam.discovery.openrouter import discover_openrouter
from uam.discovery.local import discover_local

__all__ = [
    "discover_anthropic",
    "discover_runpod",
    "discover_openrouter",
    "discover_local",
]
