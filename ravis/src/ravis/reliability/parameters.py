"""Which request settings an upstream refused by name, and which RAVIS may leave out.

**Why this exists.** Found live on 16 September 2026: a NERVIS chat turn was
sent to `gpt-4.1-2025-04-14` and OpenAI answered 400, "Unrecognized request
arguments supplied: explore, explore_prefer_unmeasured, reasoning_effort". Two
of those were RAVIS's own routing switches, which should never have left RAVIS
(`chat.RAVIS_FIELDS` now removes them). The third, `reasoning_effort`, is a real
OpenAI setting that this model does not take — and NERVIS sends it on every
ordinary turn as `"none"`, because a thinking model that receives it answers in
a second instead of ten (NERVIS's `_completion_payload`).

Nothing in the request was wrong for the models that do take it, and the
refusal names exactly what this model objected to. So the useful response is
not to give up, and not to guess which models reason (RAVIS's catalogue says
`UNKNOWN` for OpenAI's models *and* for DeepSeek's thinking ones, so a rule
keyed on the capability would either do nothing or switch thinking back on
where it matters). It is to try the same model once more **without the settings
it named**, and to remember for a while that it refuses them.

**Only settings that tune an answer, never ones that change what the answer
is.** Leaving out `temperature` or a reasoning budget gets a differently tuned
answer to the same question. Leaving out `max_tokens` would remove somebody's
spending ceiling, and leaving out `tools`, `response_format` or `stop` would
return a different kind of response than the caller can parse — those are
never dropped, and a refusal naming them moves on to another model instead
(`FailureClass.UNSUPPORTED_PARAMETER` permits that).
"""

from __future__ import annotations

import re

#: Settings whose absence changes how an answer is tuned and nothing else.
#: `reasoning_effort` is the one found live; the sampling settings are the ones
#: OpenAI's reasoning models refuse ("Unsupported value: 'temperature' does not
#: support 0.2 with this model"), and the rest are the knobs NERVIS forwards
#: (`nervis.api.chat.FORWARDED`) that some upstreams do not know.
DROPPABLE: frozenset[str] = frozenset({
    "reasoning_effort",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
    "seed",
})

# OpenAI's wording for top-level fields it does not define, singular or plural,
# as observed on 16 September 2026. The names are read as a comma-separated run
# of identifiers, so a sentence that happens to follow the list is not mistaken
# for part of it.
_UNRECOGNIZED = re.compile(
    r"unrecognized request arguments? supplied:\s*([a-z0-9_]+(?:\s*,\s*[a-z0-9_]+)*)",
    re.IGNORECASE,
)

# OpenAI's wording for a setting one model will not take, or not at that value:
# "Unsupported parameter: 'max_tokens' is not supported with this model" (seen
# 11 September 2026) and "Unsupported value: 'temperature' does not support …".
_UNSUPPORTED = re.compile(r"unsupported (?:parameter|value):\s*'([a-z0-9_]+)'", re.IGNORECASE)


def refused_parameters(message: str) -> frozenset[str]:
    """The setting names an upstream's refusal names, or an empty set.

    An empty set means the words named nothing this code can read, which is a
    reason not to retry — never a reason to drop something at random.
    """
    listed = _UNRECOGNIZED.search(message)
    if listed is not None:
        return frozenset(name.strip() for name in listed.group(1).split(","))
    return frozenset(_UNSUPPORTED.findall(message))


def droppable(names: frozenset[str], added: frozenset[str] = frozenset()) -> bool:
    """Whether every named setting may be left out, and there is at least one.

    `added` is what RAVIS put in the request on its own initiative — the caller
    never asked for it, so leaving it out takes nothing from them. Today that is
    `stream_options`, which RAVIS adds to learn what a streamed call cost
    (`chat.USAGE_FIELD`); an upstream that refuses it still answers without it.
    """
    return bool(names) and names <= DROPPABLE | added
