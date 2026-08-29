"""NERVIS's own API surface (§14)."""

from nervis.api.chat import router as chat_router
from nervis.api.diagnostics import router as diagnostics_router
from nervis.api.events import router as events_router
from nervis.api.instances import router as instances_router
from nervis.api.routes import router
from nervis.api.traces import router as traces_router
from nervis.api.voice import router as voice_router

__all__ = [
    "chat_router",
    "diagnostics_router",
    "events_router",
    "instances_router",
    "router",
    "traces_router",
    "voice_router",
]
