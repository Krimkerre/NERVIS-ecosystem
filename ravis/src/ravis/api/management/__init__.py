"""The management API (RAVIS.md §15.1) — read-only at M18a.

Separate from `/v1` in every way that matters: different path, different error
envelope (the MEP one, §4.5), and a different audience. `/v1` serves software
that wants an answer; this serves a person or a dashboard that wants to know
*why* RAVIS did what it did.
"""

from ravis.api.management.routes import router as management_router

__all__ = ["management_router"]
