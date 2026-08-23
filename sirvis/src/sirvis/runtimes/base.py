"""The shapes every runtime adapter speaks in (§7).

§7 lists the adapter methods; this is the vocabulary they exchange. It is small
on purpose — M2 builds one adapter, and an interface generalised from a single
implementation is a guess about the second one. `LlamaCppAdapter`, `MLXAdapter`
and `OllamaAdapter` will widen it when they exist and can argue for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuntimeState(str, Enum):
    """§7's closed enum. Transitions carry timestamps and reasons.

    `UNKNOWN` is a member rather than an omission: a runtime that cannot be
    reached has a state, and it is not `STOPPED` — one means "we know it is
    off", the other means "we could not find out", and treating the second as
    the first is how a service reports a network problem as a clean shutdown.
    """

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    STOPPING = "stopping"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeInfo:
    """What a runtime says about itself.

    `detail` carries the reason a state is what it is (§7), so a `FAILED` that
    arrives without an explanation is a bug in the adapter rather than a fact
    about the runtime.
    """

    runtime_key: str
    state: RuntimeState
    base_url: str
    detail: str = ""
    version: str | None = None
    model_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_key": self.runtime_key,
            "state": self.state.value,
            "base_url": self.base_url,
            "detail": self.detail,
            "version": self.version,
            "model_count": self.model_count,
        }


@dataclass(frozen=True)
class LoadedModel:
    """One model resident in a runtime, with the configuration it actually got.

    **Both the requested and the effective configuration are kept** (§7.1), and
    `ignored` names anything asked for that the runtime did not honour. §7.1's
    rule is that an unsupported value is never silently dropped — a benchmark
    run at a context length the runtime quietly halved is a result about a
    configuration nobody chose.
    """

    model_key: str
    state: str
    requested: dict[str, Any] = field(default_factory=dict)
    effective: dict[str, Any] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "state": self.state,
            "requested_configuration": self.requested,
            "effective_configuration": self.effective,
            "ignored_configuration": self.ignored,
        }


@dataclass(frozen=True)
class GenerationChunk:
    """One fragment of a streamed completion, as the benchmark engine sees it.

    Streaming exists here for exactly one reason: **time-to-first-token cannot
    be measured from a round trip.** M2's `generate` deliberately did not
    stream, because M2 only had to prove the path worked; §11.4 makes TTFT a
    headline metric, and the arrival time of the first token is not recoverable
    from a response that arrives whole.

    `usage` arrives on the final chunk when the runtime reports it, and is
    `None` when it does not — which is the difference between counting tokens
    and guessing at them. The engine downgrades a throughput figure derived
    without it rather than presenting an estimate as a measurement (§12.1).

    `content` is empty on the final chunk and on any chunk carrying only a role
    or a reasoning delta, so a caller timing the first *token* must look for the
    first chunk with text in it — a reasoning model can spend its whole budget
    thinking and return nothing, which M2 found on this machine and which is a
    result about the run rather than a fault in the adapter.
    """

    content: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    # Tool-call deltas **exactly as the frame carried them** — a list of the
    # `tool_calls` pieces, each with its own `index`, and every field optional
    # because a frame routinely omits the ones it is not extending.
    #
    # Raw rather than assembled, and that distinction is the whole of M13's
    # tool-call evidence. Clarvis reassembles these deltas by index in
    # `absorbToolDeltas`, and `granite-4.0-h-tiny` returns a well-formed call
    # unstreamed while streaming one whose arguments never arrive — so a chunk
    # that handed back a finished call would measure a mode the product does
    # not use, and would have scored that build 3/3 on a request it always
    # fails.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class RuntimeUnavailableError(Exception):
    """A runtime refused an operation, or could not be reached at all.

    Deliberately not called `RuntimeError`: that name is a builtin meaning
    something else entirely, and a module where it means two things depending
    on the import is a trap for whoever reads it next.
    """
