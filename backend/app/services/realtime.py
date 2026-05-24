"""Compatibility facade for realtime services.

New code should prefer the `app.services.realtime_runtime` package.
This module remains as the stable import path for existing callers.
"""

from app.services.realtime_runtime import (
    PRESENCE_TTL_SECONDS,
    PUBSUB_PREFIX,
    RealtimeRouter,
    realtime_router,
)

__all__ = [
    "PRESENCE_TTL_SECONDS",
    "PUBSUB_PREFIX",
    "RealtimeRouter",
    "realtime_router",
]
