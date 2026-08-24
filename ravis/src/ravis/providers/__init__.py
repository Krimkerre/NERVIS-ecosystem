"""Provider adapters: how RAVIS learns about and reaches an upstream."""

from ravis.providers.base import (
    ProtocolMode,
    ProviderAdapter,
    TranslatingAdapter,
    TranslationError,
)
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.providers.lmstudio import LmStudioAdapter
from ravis.providers.ollama import OllamaAdapter

__all__ = [
    "GenericOpenAiAdapter",
    "LmStudioAdapter",
    "OllamaAdapter",
    "ProtocolMode",
    "ProviderAdapter",
    "TranslatingAdapter",
    "TranslationError",
]
