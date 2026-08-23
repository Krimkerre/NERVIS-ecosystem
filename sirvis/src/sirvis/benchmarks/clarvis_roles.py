"""M13 — the Clarvis role benchmarks, wrapped rather than rewritten (§13.2).

M12's instruction is *inspect Clarvis's existing benchmark assets — do not
rewrite*, and this module is what that produced. Everything load-bearing here
came from `clarvis-firstrun/tools/suite2.py`, which measured the models this
ecosystem was built around. It is credited inline rather than absorbed silently,
because each piece encodes a failure that cost a real debugging session, and a
reimplementation would have to rediscover every one.

**The three assets classified `REUSE`, and why each is irreplaceable:**

1. *Eight phrasings, not one.* `granite-4.0-h-tiny` passed a single-prompt
   tool-call check three times out of three and lost the filename on every one
   of the eight. A harness with one prompt measures whether that phrasing works.
2. *Assembled from the stream, by index.* The original harness sent
   `stream: false` and read `message.tool_calls` off a finished response.
   Clarvis never does that — every reply it makes is streamed, and tool calls
   arrive as deltas. The two modes **disagree**: the same build returns a
   well-formed call unstreamed and streams one whose arguments never arrive. The
   old harness therefore scored 3/3 for a build that fails every realistic
   request. Measuring the mode the product does not use is measuring nothing.
3. *The follow-up turn.* One-shot codegen cannot see the failure where a model
   malforms a path and then retries the dead path four times, twice after being
   told plainly to use a different tool. Replaying that exchange is the only way
   to catch it.

**What this module adds rather than wraps** is the evidence contract. §13.2
makes tool-call reliability a `TrialRate` rather than a `Measurement` — eight
phrasings times three repetitions is twenty-four attempts, and the useful number
is how many produced a well-formed call, not a median over ones and zeros. M7
built that receptacle; this fills it.

**No metadata-only capability claim.** M13's acceptance says it outright, and it
is the reason this file exists at all: a catalogue flag saying `tool_use` is not
evidence that a build calls tools, and on this machine the two disagree in both
directions. A `tools` capability asserted here comes from attempts that were
made and counted, or it is not asserted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sirvis.benchmarks.spec import BenchmarkTest, GenerationConfig

# Clarvis's two roles, spelled as RAVIS's pools name them. M16 found the seam
# this closes: evidence filed under `agent` cannot answer a query for
# `clarvis-agent`, and the fix is to measure the role RAVIS actually asks about
# rather than to invent a mapping between two vocabularies.
ROLE_CHAT = "clarvis-chat"
ROLE_AGENT = "clarvis-agent"

# The tool Clarvis actually offers, in the shape it offers it.
READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "readFile",
        "description": "Read a file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}

LIST_FILES_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "listFiles",
        "description": "List files in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"recursive": {"type": "boolean"}},
            "required": ["recursive"],
        },
    },
}

# Verbatim from `clarvis-firstrun/tools/suite2.py`. Eight ways a person actually
# asks for a file, rather than one phrasing that happens to work — see the
# module docstring for what that distinction caught.
TOOL_PROMPTS: tuple[str, ...] = (
    "Read src/main.go and tell me what package it declares.",
    "Read package.json and tell me what the build script does.",
    "Open src/extension.ts and tell me what it activates.",
    "Read the README and summarise the install steps.",
    "Look at src/model/providers.ts and list the providers.",
    "Check .eslintrc and tell me if no-console is on.",
    "Read docs/CURRENT_STATE.md and tell me what is built.",
    "Read the file config.json and tell me what is in it.",
)

# Clarvis's own scenes, from the same file. These are what the product asks for,
# which is the point: a role benchmark run on invented prompts measures a model
# against a workload nobody has.
CHAT_SCENES: tuple[tuple[str, str], ...] = (
    ("quip", "You are a dry, unflappable butler in a code editor. In ONE short "
             "sentence, tell the user their build just failed for the third time."),
    ("briefing", "You are a dry butler in a code editor. In two sentences, tell the "
                 "user where they left off: branch main, clean tree, last commit "
                 "'fix the parser' two days ago. Invent nothing else."),
    ("explain", "Explain in three sentences what a race condition is, to someone six "
                "months into learning to code."),
)

AGENT_SCENES: tuple[tuple[str, str], ...] = (
    ("code", "Write a Python function `dedupe(items)` that removes duplicates from a "
             "list while preserving order. Code only, no explanation."),
    ("code2", "Write a Python function `fib(n)` returning the nth Fibonacci number, "
              "where fib(0) == 0 and fib(1) == 1. Code only, no explanation."),
)

# What the follow-up turn can conclude. Named outcomes rather than a boolean
# because they are different failures with different fixes, and collapsing them
# would lose the distinction the test was built to make.
USED_RESULT = "used-result"
RETRIED = "retried"
LOST_ARGUMENTS = "lost-arguments"
ANSWERED_IN_PROSE = "answered-in-prose"
GAVE_UP = "gave-up"
NO_CALL = "no-call"

# Only one of those is the model doing the right thing. Stated as a constant so
# a scorer cannot quietly widen it.
FOLLOWUP_PASSES = (USED_RESULT,)


@dataclass
class AssembledCall:
    """One tool call, rebuilt from the deltas that carried it."""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""

    @property
    def path(self) -> str | None:
        """The `path` argument, or None when it never arrived or will not parse.

        None is the interesting answer. A call whose arguments never completed
        is not a call this product can act on, and it must not be counted as
        one — that is the exact failure the streamed harness exists to catch.
        """
        try:
            parsed = json.loads(self.arguments or "{}")
        except ValueError:
            return None
        return parsed.get("path") if isinstance(parsed, dict) else None

    @property
    def is_well_formed(self) -> bool:
        """Whether Clarvis could dispatch this call as it stands."""
        return bool(self.name) and self.path is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "id": self.id, "name": self.name,
            "arguments": self.arguments, "path": self.path,
            "well_formed": self.is_well_formed,
        }


def absorb_tool_deltas(frames: Sequence[Mapping[str, Any]]) -> list[AssembledCall]:
    """Rebuild tool calls from streamed deltas, the way Clarvis does.

    Mirrors `absorbToolDeltas` in the Clarvis extension, and the two properties
    that matter are both about *absence*:

    - **Addressed by index**, because parallel calls interleave and only the
      index says which fragment belongs to which call.
    - **Every field survives a frame that omits it.** The opening frame carries
      the id and name; every frame after it carries only more `arguments`. A
      naive merge that overwrote with `None` would erase the name on the second
      frame of every call.
    """
    calls: dict[int, AssembledCall] = {}
    for frame in frames:
        index = int(frame.get("index", 0) or 0)
        existing = calls.setdefault(index, AssembledCall(index=index))
        function = frame.get("function") or {}
        calls[index] = AssembledCall(
            index=index,
            id=str(frame.get("id") or existing.id),
            name=str(function.get("name") or existing.name),
            arguments=existing.arguments + str(function.get("arguments") or ""),
        )
    return [calls[index] for index in sorted(calls)]


@dataclass
class ToolTrial:
    """One phrasing, attempted once, and what came back."""

    prompt: str
    calls: list[AssembledCall] = field(default_factory=list)
    text: str = ""

    @property
    def outcome(self) -> str:
        """What this attempt actually produced, in the vocabulary above."""
        if not self.calls:
            return NO_CALL
        return USED_RESULT if self.calls[0].is_well_formed else LOST_ARGUMENTS

    @property
    def passed(self) -> bool:
        """Whether Clarvis could have acted on this attempt.

        A call with no usable arguments does **not** pass. It is the failure
        that looks most like success — a `tool_calls` array arrived, the name is
        right, and the product still cannot dispatch it.
        """
        return bool(self.calls) and self.calls[0].is_well_formed

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "outcome": self.outcome,
            "passed": self.passed,
            "calls": [call.as_dict() for call in self.calls],
            # Preserved rather than summarised (§11.9): "it answered in prose
            # instead of calling the tool" is a finding, not noise.
            "text": self.text,
        }


def score_followup(
    first: Sequence[AssembledCall], second: Sequence[AssembledCall], prose: str
) -> str:
    """What the model did after a tool answered with an error and an instruction.

    The F17 test, wrapped from `tool_followup` in suite2.py. Its whole value is
    that one-shot code generation cannot see this failure: the model that
    malformed a path and then retried the dead path four times — twice after
    being told plainly to use `listFiles` — writes perfectly good Python.
    """
    if not first:
        return NO_CALL
    asked = first[0].path
    if asked is None:
        return LOST_ARGUMENTS
    if not second:
        return GAVE_UP if not prose.strip() else ANSWERED_IN_PROSE
    if second[0].name == LIST_FILES_TOOL["function"]["name"]:
        return USED_RESULT
    if second[0].name == READ_FILE_TOOL["function"]["name"] and second[0].path == asked:
        # F17's exact signature: told the path was wrong, asked for it again.
        return RETRIED
    return USED_RESULT


def followup_messages(asked: str, call: AssembledCall) -> list[dict[str, Any]]:
    """The second turn, with the tool answering as Clarvis's `readFile` does.

    The error text is Clarvis's own, verbatim. A generic "file not found" would
    make this a different test: the point is that the model is given a specific,
    actionable instruction and we watch whether it follows it.
    """
    return [
        {"role": "user", "content": TOOL_PROMPTS[0]},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": call.id or "abc123def",
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call.id or "abc123def",
            "content": (
                f"`{asked}` isn't there. Paths are relative to the workspace root — "
                "no leading folder name for the project itself. Use listFiles to see what is."
            ),
        },
    ]


def role_tests(role: str) -> tuple[BenchmarkTest, ...]:
    """The prose workload for one Clarvis role, from Clarvis's own scenes.

    Deterministic generation, because §11.4's performance workloads are compared
    across builds and a default temperature measures something slightly
    different every run.
    """
    scenes = CHAT_SCENES if role == ROLE_CHAT else AGENT_SCENES
    return tuple(
        BenchmarkTest(
            id=name,
            version="1",
            prompt=prompt,
            generation=GenerationConfig(temperature=0, max_tokens=600),
        )
        for name, prompt in scenes
    )
