"""More than one transparent upstream, and how a model finds its way to one.

M1 forwarded to a single upstream and M8's first half taught RAVIS to *describe*
it. This is the plural half: a deployment with LM Studio and Ollama running side
by side is two upstreams, each with its own catalogue, its own adapter and its
own idea of what it has installed.

**Declared, never discovered.** §3's rule about provider clients applies with
more force here: probing to find out which upstreams exist would make coming up
depend on all of them being awake, and a runtime that is merely asleep would
look like a runtime that is gone.

**Names are the addressing scheme.** An upstream's name occupies the same slot a
translating provider's does — `ravis/<name>/<model>` — because a client asking
for a specific place should not have to know whether that place needs
translation. `chat.py` already parses that shape for Path B; this makes the same
segment mean something on Path A.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

# The name a singular configuration gets. Deployments that set
# `RAVIS_UPSTREAM_BASE_URL` and nothing else have exactly one upstream and no
# reason to have named it, but everything downstream keys on a name — so one is
# supplied rather than making the name optional everywhere it is read.
DEFAULT_NAME = "default"

# Where a hosted provider's OpenAI-compatible surface lives: the address, and
# the root its endpoints hang off.
#
# **This is the whole of what "supporting Google" turned out to require.** The
# expected shape was a `GoogleAdapter` translating between Gemini's native
# protocol and OpenAI's, the way `anthropic.py` does — several hundred lines and
# a wire format to keep up with. Google publishes an OpenAI-compatible surface
# instead, so the difference from any other upstream is two strings: its API
# roots at `/v1beta/openai` rather than `/v1`, which is why `Upstream.api_root`
# exists at all.
#
# OpenRouter is here for the same reason it is in `KNOWN_PROVIDERS`: the
# Credentials screen has always offered a row for it, and a credential slot that
# cannot route is a screen making a promise the service does not keep.
#
# A local runtime is deliberately absent. LM Studio and Ollama are wherever the
# operator started them, and guessing a port is how a dashboard reports the
# wrong machine as healthy.
KIND_ENDPOINTS: dict[str, tuple[str, str]] = {
    # Gemini's OpenAI-compatible surface. **Kept, and no longer the way to
    # reach Google**: M7 put Gemini on §6's translated path, and declaring a
    # transparent upstream of this kind now lists the same models twice.
    #
    # Kept rather than removed because an operator with it in their environment
    # should not fail to start, and because the native adapter takes precedence
    # for a direct address anyway. But the reason it was left behind is
    # measured, not stylistic: this endpoint reports `finish_reason: stop` on a
    # streamed tool call and omits the tool-call index, and both are things
    # Clarvis's agent role reads.
    #
    # **Declaring it alongside the native adapter buys nothing**, also measured:
    # the native catalogue's 39 models are a strict subset of this one's 54, and
    # all 15 extras — embeddings, Veo, Lyria, live-audio, robotics — are models
    # the native adapter filters out precisely because they cannot serve
    # `generateContent`. Declaring it adds 15 ids a client can pick out of
    # /v1/models and be refused for, which is the thing `merged_catalogue`
    # already refuses to do for a disabled upstream.
    "google": ("https://generativelanguage.googleapis.com", "/v1beta/openai"),
    # The shape everything else here is compatible *with*, so it needs no
    # special handling at all — which is the point worth recording. Listing it
    # is not about translation; it is so that "OpenAI" is a row on the
    # Credentials screen and a `kind` somebody can declare without looking up an
    # address, exactly like the two below it.
    "openai": ("https://api.openai.com", "/v1"),
    "openrouter": ("https://openrouter.ai/api", "/v1"),
    # Two more that need nothing but an address, for the same reason OpenAI
    # needs nothing: both publish the OpenAI protocol, so §6's transparent path
    # forwards to them unchanged and there is no wire format to keep up with.
    "deepseek": ("https://api.deepseek.com", "/v1"),
    "xai": ("https://api.x.ai", "/v1"),
}


def is_local_address(base_url: str) -> bool:
    """Whether requests to this upstream stay on this machine.

    **Read from the address, not from a list of kinds.** A list would be wrong
    the first time somebody runs a new local runtime or points `generic` at a
    LAN box, and being wrong here is not a routing inconvenience — `ravis/local`
    promises "never leaves this machine" and `ravis/private` promises cloud
    providers are excluded. A hostname RAVIS has not heard of must not inherit
    that promise by default.

    Loopback and link-local only. A private LAN address is deliberately **not**
    local: 192.168.1.50 is somebody else's computer, and "did not leave my
    network" is a different promise from "did not leave my machine" — the one
    these pools make is the second.

    Fails closed. An address that cannot be parsed is remote, because the cost
    of guessing wrong in that direction is a pool refusing to route, and in the
    other direction it is a prompt leaving the machine that was promised it
    would not.

    **One exception, by the owner's decision (19 September 2026):** LM Studio on
    this machine also serves, on its loopback address, models it reaches on the
    owner's other machines through LM Link, and those count as local too — "they
    are both mine" (RAVIS.md §5).
    """
    host = urlsplit(base_url).hostname or ""
    if host in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def default_base_url(kind: str) -> str:
    """The address a hosted kind knows about itself, or empty."""
    return KIND_ENDPOINTS.get(kind.strip().lower(), ("", ""))[0]


def api_root_for(kind: str) -> str:
    """Where this kind roots its OpenAI-shaped endpoints.

    `/v1` unless the provider says otherwise, because that is what OpenAI chose
    and what everything that copied OpenAI copied.
    """
    return KIND_ENDPOINTS.get(kind.strip().lower(), ("", "/v1"))[1]


@dataclass(frozen=True)
class UpstreamSpec:
    """One declared upstream, before anything has been built for it."""

    name: str
    base_url: str
    api_key: str = ""
    kind: str = "generic"

    def redacted(self) -> dict[str, Any]:
        """The spec as a diagnostic may print it.

        `api_key` is reported as present or absent and never as a value. §9.7
        keeps credentials out of everything RAVIS reads back, and a management
        surface listing upstreams is exactly where one would otherwise leak.
        """
        return {
            "name": self.name,
            "base_url": self.base_url,
            "kind": self.kind,
            "api_key_configured": bool(self.api_key),
        }


class UpstreamConfigurationError(ValueError):
    """A declaration RAVIS will not guess at.

    Distinct from an unrecognised `kind`, which degrades to the generic adapter
    on purpose: a typo in a kind costs the vendor metadata it would have read,
    while a malformed list means RAVIS does not know where to send anything.
    """


def upstream_specs(settings: Any) -> list[UpstreamSpec]:
    """Every transparent upstream this configuration declares, in order.

    Order is load-bearing — it is what breaks a tie when two upstreams serve the
    same model id — so it is preserved from the configuration rather than sorted
    into something prettier.

    The singular settings still work and still mean what they meant. A
    deployment that sets `RAVIS_UPSTREAM_BASE_URL` gets one upstream named
    `default`, which is what every existing deployment and every test written
    before this describes.
    """
    declared = (getattr(settings, "upstreams", "") or "").strip()
    if declared:
        return _parse(declared)
    if not settings.upstream_base_url:
        return []
    return [
        UpstreamSpec(
            name=DEFAULT_NAME,
            base_url=settings.upstream_base_url,
            api_key=settings.upstream_api_key,
            kind=settings.upstream_kind,
        )
    ]


def _parse(raw: str) -> list[UpstreamSpec]:
    """Read the JSON list form, refusing anything ambiguous."""
    try:
        payload = json.loads(raw)
    except ValueError as failure:
        raise UpstreamConfigurationError(f"not valid JSON: {failure}") from failure
    if not isinstance(payload, list):
        raise UpstreamConfigurationError("must be a JSON list of objects")

    specs: list[UpstreamSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(payload):
        spec = _spec_from(entry, index)
        if spec.name in seen:
            # Not a warning. Names are how a request addresses an upstream, so
            # two upstreams sharing one makes half the addresses in the
            # deployment silently unreachable — and which half depends on
            # iteration order, which is the worst kind of bug to ship.
            raise UpstreamConfigurationError(f"duplicate upstream name {spec.name!r}")
        seen.add(spec.name)
        specs.append(spec)
    return specs


def _spec_from(entry: Any, index: int) -> UpstreamSpec:
    """One list element, validated."""
    if not isinstance(entry, dict):
        raise UpstreamConfigurationError(f"entry {index} is not an object")
    name = str(entry.get("name") or "").strip()
    kind = str(entry.get("kind") or "generic").strip()
    # A hosted provider knows its own address, so declaring one should not mean
    # copying a URL out of documentation — that is a string nobody can verify by
    # reading it, and getting it subtly wrong produces a 404 that looks like an
    # outage. A local runtime has no such default and still must be told.
    base_url = str(entry.get("base_url") or "").strip() or default_base_url(kind)
    if not name:
        raise UpstreamConfigurationError(f"entry {index} has no name")
    if not base_url:
        raise UpstreamConfigurationError(
            f"upstream {name!r} has no base_url, and kind {kind!r} has no default"
        )
    if "/" in name:
        # A name with a slash in it would split `ravis/<name>/<model>` into more
        # segments than the address has, so the upstream could be declared and
        # never addressed.
        raise UpstreamConfigurationError(f"upstream name {name!r} may not contain '/'")
    return UpstreamSpec(
        name=name,
        base_url=base_url,
        api_key=str(entry.get("api_key") or ""),
        kind=kind,
    )
