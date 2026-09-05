"""Which peer versions this NERVIS was built against (§12).

**§12: "Publish compatibility matrices and minimum/maximum peer versions."** The
registry has read every peer's `build_version` since M2, published it on
`/api/v1/services`, and compared it to nothing. Four products on independent
cadences, and no statement anywhere about which combinations were meant to work —
so an operator running a NERVIS six minors ahead of its RAVIS had no way of being
told, and the first symptom would be a surface answering in a shape this build
does not read.

**Reported, never refused, and §12 chooses that word.** *"NERVIS must tolerate
peers one supported minor behind during a rolling upgrade."* An upgrade happens
one service at a time; a version check that refused would turn the ordering of
that upgrade into an outage, which is the opposite of what tolerating means. The
answer travels beside the version it judges and changes nothing else.

**Ranges, not pins.** A minimum a full minor below what this build was written
against is what makes the rolling upgrade possible at all, and the maximum says
what has actually been exercised together rather than guessing forward: a peer
newer than this NERVIS may well work, and nobody here has seen it.

**Protocol compatibility is a different question and stays where it is.**
`ecosystem_protocol.version` decides whether two services can speak at all, and
refuses structurally when they cannot (§4.2). This decides whether a combination
is one anybody has supported. A build can speak the protocol perfectly and still
be a pairing nobody has run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    """The versions of one peer this build supports, inclusive at both ends."""

    minimum: str
    maximum: str
    #: Why the range is what it is, for the operator reading a mismatch.
    note: str = ""


@dataclass(frozen=True)
class Answer:
    """Whether a peer's version is inside its window, and what to say if not."""

    supported: bool
    reason: str = ""


#: What this NERVIS was built and exercised against.
#:
#: The maxima are the versions these peers ship today; the minima are a minor
#: below, which is §12's rolling-upgrade clause expressed as a number rather than
#: as an intention. `tools/check_compatibility.py` fails when a maximum falls
#: behind what a peer actually ships, so this cannot quietly describe last
#: month's ecosystem.
SUPPORTED_PEERS: dict[str, Window] = {
    "ravis": Window(
        minimum="0.20.0", maximum="0.21.999",
        note="the gateway NERVIS routes chat through and proxies configuration to",
    ),
    "sirvis": Window(
        minimum="0.14.0", maximum="0.15.999",
        note="read for evidence and benchmark jobs; its absence degrades rather than stops",
    ),
    "clarvis": Window(
        minimum="0.11.0", maximum="0.12.999",
        note="the Bridge NERVIS reads status and configuration summaries from; it claims "
             "its version at registration rather than serving an identity surface",
    ),
}

#: Peers whose version NERVIS cannot judge. Empty, and kept as a named place
#: rather than deleted: Clarvis sat here until its Bridge published a version at
#: registration, and the next peer that registers without one belongs here rather
#: than in a window that applies to nothing.
CANNOT_BE_JUDGED: dict[str, str] = {}


def _parts(version: str) -> tuple[int, ...] | None:
    """A version as numbers, or `None` when it is not one.

    Trailing labels are dropped rather than refused — `0.21.2+local` is a version
    somebody built, and treating it as unparseable would report a mismatch about
    a build that is inside the window.
    """
    core = version.strip().split("+")[0].split("-")[0]
    pieces = core.split(".")
    if not core or not all(piece.isdigit() for piece in pieces):
        return None
    return tuple(int(piece) for piece in pieces)


def supported(peer: str, version: str) -> Answer:
    """Whether this peer, at this version, is a combination this build supports.

    An empty version is *supported*, deliberately: a peer that has not answered
    yet has no version, and calling that unsupported would be a confident answer
    about a number nobody has — which is the failure mode this repository keeps
    finding in other clothes.
    """
    window = SUPPORTED_PEERS.get(peer)
    if window is None or not version.strip():
        return Answer(True)

    seen = _parts(version)
    if seen is None:
        return Answer(False, f"{peer} reports version {version!r}, which is not a version")

    low, high = _parts(window.minimum), _parts(window.maximum)
    assert low is not None and high is not None  # the table is ours, and it is checked
    if seen < low:
        return Answer(False, f"{peer} is {version}; this NERVIS supports {window.minimum} "
                             f"and above")
    if seen > high:
        return Answer(False, f"{peer} is {version}; this NERVIS has been exercised up to "
                             f"{window.maximum}")
    return Answer(True)
