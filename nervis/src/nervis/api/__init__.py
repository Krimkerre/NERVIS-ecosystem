"""NERVIS's own API surface (§14)."""

from nervis.api.background import router as background_router
from nervis.api.chat import router as chat_router
from nervis.api.code import proxy as code_proxy_router
from nervis.api.code import router as code_router
from nervis.api.commands import router as commands_router
from nervis.api.conversations_transfer import router as conversations_transfer_router
from nervis.api.diagnostics import router as diagnostics_router
from nervis.api.documents import router as documents_router
from nervis.api.events import router as events_router
from nervis.api.files import router as files_router
from nervis.api.handovers import router as handovers_router
from nervis.api.inspector import router as inspector_router
from nervis.api.instances import router as instances_router
from nervis.api.learned import router as learned_router
from nervis.api.link import router as link_router
from nervis.api.logs import router as logs_router
from nervis.api.notifications import router as notifications_router
from nervis.api.proposals import router as proposals_router
from nervis.api.recall import router as recall_router
from nervis.api.routes import router
from nervis.api.settings_transfer import router as settings_transfer_router
from nervis.api.supervision import router as supervision_router
from nervis.api.traces import router as traces_router
from nervis.api.voice import router as voice_router

__all__ = [
    "files_router",
    "code_proxy_router",
    "code_router",
    "background_router",
    "chat_router",
    "commands_router",
    "diagnostics_router",
    "events_router",
    "documents_router",
    "inspector_router",
    "logs_router",
    "handovers_router",
    "instances_router",
    "learned_router",
    "notifications_router",
    "proposals_router",
    "recall_router",
    "conversations_transfer_router",
    "link_router",
    "settings_transfer_router",
    "router",
    "supervision_router",
    "traces_router",
    "voice_router",
]
