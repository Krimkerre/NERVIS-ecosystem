# Proposed pool memberships — NOT APPLIED.
# Written by tools/propose_pools.py. A model's opinion about which
# models are good is retrieved content (CLARVIS.md §11.5): read it,
# disagree with it, and edit ravis/core/pools.py by hand.

# ── proposed for ravis/chat ────────────────────────────────────────
# Selected cheap, capable chat models first, then mid-range options, finally expensive frontier models as last resort.
# 142 of 591 models would be members.
#
# The models this selects, in the order proposed:
#   amazon/nova-micro-v1
#   anthropic/claude-haiku-4.5
#   anthropic/claude-haiku-4.5:batch
#   anthropic/claude-opus-4
#   anthropic/claude-opus-4.1
#   anthropic/claude-opus-4.1:batch
#   anthropic/claude-opus-4.5
#   anthropic/claude-opus-4.5:batch
#   anthropic/claude-opus-4.6
#   anthropic/claude-opus-4.6:batch
#   anthropic/claude-opus-4.7
#   anthropic/claude-opus-4.7-fast
#   anthropic/claude-opus-4.7:batch
#   anthropic/claude-opus-4.8
#   anthropic/claude-opus-4.8-fast
#   anthropic/claude-opus-4.8:batch
#   anthropic/claude-opus-5
#   anthropic/claude-opus-5-fast
#   anthropic/claude-opus-5:batch
#   claude-haiku-4-5-20251001
#   claude-opus-4-5-20251101
#   claude-opus-4-6
#   claude-opus-4-7
#   claude-opus-4-8
#   claude-opus-5
#   deepseek/deepseek-v4-pro
#   deepseek/deepseek-v4-pro-0813
#   deepseek/deepseek-v4-pro-0813:batch
#   google/gemini-2.5-flash-lite
#   google/gemini-2.5-flash-lite:batch
#   google/gemini-2.5-pro
#   google/gemini-2.5-pro-preview
#   google/gemini-2.5-pro-preview-05-06
#   google/gemini-2.5-pro:batch
#   google/gemma-3-4b-it
#   gpt-4o
#   gpt-4o-2024-05-13
#   gpt-4o-2024-08-06
#   gpt-4o-2024-11-20
#   gpt-4o-mini
#   …

CHAT_FAMILIES: tuple[str, ...] = (
    "gpt-4o-mini",
    "claude-haiku",
    "gemini-2.5-flash-lite",
    "mistralai/ministral-3b",
    "google/gemma-3-4b-it",
    "meta-llama/llama-3.2-1b-instruct",
    "amazon/nova-micro",
    "qwen/qwen3-4b",
    "gpt-4o",
    "claude-opus",
    "gemini-2.5-pro",
    "mistralai/mistral-large",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3-max",
    "gpt-5",
)

CHAT_EXCLUDED: tuple[str, ...] = (
    ":batch",
    "-code",
    "-coder",
    "-image",
    "-tts",
    "-transcribe",
    "-realtime",
    "-audio",
    "-search",
    "embedding",
    "moderation",
    "guard",
    "deep-research",
    "vision",
    "codestral",
    "devstral",
    "gpt-5-codex",
    "o1",
    "o3",
)
