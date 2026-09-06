"""The OpenAI-compatible client API (RAVIS.md §4.2).

This is the surface Clarvis, the OpenAI SDK and every other compatible client
speak to. Its shapes are not ours to improve: they are a published contract that
other people's software parses, which is why errors here use OpenAI's error
object rather than the MEP envelope (§4.5).
"""

from ravis.api.openai.chat import router as chat_router
from ravis.api.openai.embeddings import router as embeddings_router
from ravis.api.openai.models import router as models_router

__all__ = ["chat_router", "embeddings_router", "models_router"]
