"""The Resource Manager: one owner for every load and unload (§9).

§9's first sentence is the whole design — *all load/unload operations flow
through it* — and its second paragraph is why:

    A loaded model may be used by RAVIS, the benchmark runner, the web UI or an
    external application at once. Do not unload because one owner releases it —
    unload when the reference count reaches zero. Never unload a resource owned
    by another client.

Without this, two clients sharing a model means whichever finishes first pulls
it out from under the other. Not hypothetical here: RAVIS routes traffic to the
same builds the benchmark runner wants, and LM Studio already loads extra
instances by itself when a request wants more context than the running copy has.

**Time is injected**, as in RAVIS's circuit breaker and for the same reason:
every lease decision is a function of elapsed time, so a test that had to sleep
through an expiry would either be slow or be lying about what it exercised.
Monotonic rather than wall clock — a clock adjustment must not strand a model or
expire a live session.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from sirvis.runtimes import LoadedModel, RuntimeUnavailableError

# Conservative, per §9's "conservative defaults". An hour outlasts a benchmark
# suite and is short enough that a client which died without releasing does not
# strand a model until somebody notices.
DEFAULT_LEASE_SECONDS = 3600.0

# How many models this manager holds at once. Two rather than a larger number
# because `clarvis/docs/benchmarks.md` measured exactly this on this class of
# machine: two co-resident models cost almost nothing on 24 GB, and the third is
# where it got tight. An operator with more memory should raise it — the point
# is that the ceiling exists and is stated rather than discovered.
DEFAULT_MAX_LOADED = 2


class ConflictPolicy(str, Enum):
    """What to do when a load cannot proceed immediately (§9).

    **Preemption is never implicit**, which is why this is a parameter with a
    conservative default rather than a heuristic. A caller wanting something
    evicted has to say so — and even then cannot take a model another client
    holds.
    """

    WAIT = "wait"
    REJECT = "reject"
    PREEMPT = "preempt"


class ResourceExhaustedError(Exception):
    """No capacity, and the policy did not permit making any.

    Names who is holding what, because "at capacity" without that is a dead end
    for whoever reads it.
    """


class Runtime(Protocol):
    """The slice of a runtime adapter this manager drives.

    Narrow on purpose: the manager owns lifecycle and nothing else, so it cannot
    reach a `generate` even by accident.
    """

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        ...

    async def unload(self, model_key: str) -> None:
        ...

    async def list_loaded_models(self) -> list[LoadedModel]:
        ...


@dataclass
class Holding:
    """One model this manager has loaded, and who is holding it."""

    model_key: str
    loaded: LoadedModel
    # session_id → how many times that session acquired it. A session acquiring
    # the same model twice must release it twice, or a nested caller releasing
    # early would pull the model out from under its own outer scope.
    references: dict[str, int] = field(default_factory=dict)
    acquired_at: float = 0.0
    # Whether this manager loaded it. §11.2's lifecycle says "unload **if
    # owned**", and the distinction is not academic: LM Studio JIT-loads
    # instances by itself and a user can load anything by hand. Unloading one of
    # those at the end of a benchmark would take away a model somebody else was
    # using, which is the implicit preemption §9 forbids.
    owned: bool = True

    @property
    def reference_count(self) -> int:
        return sum(self.references.values())


@dataclass
class Lease:
    """A client's claim on one or more models, with an expiry (§9).

    Leases exist to recover from clients that die without releasing. Without
    one, a crashed benchmark runner strands a multi-gigabyte model until someone
    notices and restarts the service.
    """

    session_id: str
    owner: str
    model_keys: tuple[str, ...]
    expires_at: float
    lease_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "owner": self.owner,
            "models": list(self.model_keys),
            "lease_seconds": self.lease_seconds,
            "state": "active",
        }


class ResourceManager:
    """Tracks what is loaded, who holds it, and when their claim lapses.

    No lock is held across a `load`, which looks like an oversight and is not:
    the runtime call happens outside the critical section so a multi-minute load
    does not block every other acquire and release. What makes that safe is that
    the in-flight load is registered *inside* the lock, so a second acquire of
    the same cold model joins that load rather than starting a second one.
    """

    def __init__(
        self,
        runtime: Runtime,
        clock: Callable[[], float] = time.monotonic,
        default_lease_seconds: float = DEFAULT_LEASE_SECONDS,
        max_loaded: int = DEFAULT_MAX_LOADED,
    ) -> None:
        self._runtime = runtime
        self._clock = clock
        self._default_lease = default_lease_seconds
        self._max_loaded = max_loaded
        self._holdings: dict[str, Holding] = {}
        self._leases: dict[str, Lease] = {}
        self._loading: dict[str, asyncio.Task[tuple[LoadedModel, bool]]] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        owner: str,
        model_key: str,
        configuration: dict[str, Any] | None = None,
        policy: ConflictPolicy = ConflictPolicy.WAIT,
        lease_seconds: float | None = None,
        session_id: str | None = None,
    ) -> Lease:
        """Take a reference to a model, loading it if nobody has.

        Acquiring the same model twice under one session takes two references
        and needs two releases. That is not double-counting — it is what lets a
        nested caller release its own claim without pulling the model out from
        under the scope containing it (§9 asks for idempotent operations).
        """
        session = session_id or uuid.uuid4().hex
        await self._expire_stale()

        loaded, owned = await self._ensure_loaded(model_key, configuration, policy)
        async with self._lock:
            holding = self._holdings.setdefault(
                model_key,
                Holding(model_key=model_key, loaded=loaded, acquired_at=self._clock(),
                        owned=owned),
            )
            holding.references[session] = holding.references.get(session, 0) + 1
            previous = self._leases.get(session)
            keys = tuple(sorted({*(previous.model_keys if previous else ()), model_key}))
            seconds = lease_seconds or (
                previous.lease_seconds if previous else self._default_lease
            )
            lease = Lease(
                session_id=session,
                owner=owner,
                model_keys=keys,
                expires_at=self._clock() + seconds,
                lease_seconds=seconds,
            )
            self._leases[session] = lease
            return lease

    async def release(self, session_id: str) -> list[str]:
        """Drop a session's claims, unloading anything nobody else holds.

        Returns what was actually unloaded, usually nothing: §9 is explicit that
        a model is not unloaded because *one* owner released it.
        """
        async with self._lock:
            lease = self._leases.pop(session_id, None)
            if lease is None:
                # Releasing an unknown session is a no-op rather than an error.
                # A client retrying after a timeout must not be punished for the
                # first attempt having succeeded.
                return []
            orphaned = self._drop_references(session_id, lease.model_keys)
        return await self._unload_all(orphaned)

    def lease_owner(self, session_id: str) -> str | None:
        """Who a *live* session belongs to, or None when there is no such lease.

        A read with no side effects, for a caller deciding whether it may act
        on this session at all — before `release`/`renew` run and make their
        own, different, decision about whether the session exists.
        """
        lease = self._leases.get(session_id)
        return lease.owner if lease is not None else None

    def renew(self, session_id: str, lease_seconds: float | None = None) -> Lease | None:
        """Extend a lease, or return None when it has already lapsed.

        Never a silent re-creation: a client whose lease expired has already had
        its models released, so handing it a fresh lease would tell it it still
        holds something it does not.
        """
        lease = self._leases.get(session_id)
        if lease is None:
            return None
        seconds = lease_seconds or lease.lease_seconds
        renewed = Lease(
            session_id=lease.session_id,
            owner=lease.owner,
            model_keys=lease.model_keys,
            expires_at=self._clock() + seconds,
            lease_seconds=seconds,
        )
        self._leases[session_id] = renewed
        return renewed

    async def sweep(self) -> list[str]:
        """Reclaim anything held by a lapsed lease.

        The crash-recovery path. A client that died without releasing looks
        exactly like one that is merely slow, so the only safe recovery is time
        — which is why leases exist rather than trusting clients to clean up.
        """
        return await self._expire_stale()

    async def force_unload(self, model_key: str, authority: str) -> bool:
        """Unload regardless of who holds it — §9's explicit escape hatch.

        Requires a named authority and is never reached by ordinary release,
        because §9 permits this only when done "explicitly with authority".
        """
        del authority  # Recorded by the caller's audit trail, not decided here.
        async with self._lock:
            existed = self._holdings.pop(model_key, None) is not None
        try:
            await self._runtime.unload(model_key)
        except RuntimeUnavailableError:
            return existed
        return True

    def residency(self, *, reveal_owner: bool = False) -> dict[str, Any]:
        """What is held, by how many sessions, and what is leased.

        The view §9 exists to make possible: RAVIS and NERVIS can see who is
        using what instead of guessing, which is the alternative that has them
        fighting over lifecycle.

        `reveal_owner` defaults closed: `owner` became a real caller identity
        rather than a self-declared string once close/renew started enforcing
        it (CWE-863's fix), and this view has no caller of its own to check —
        that decision belongs to whoever calls this with the request in hand,
        the same way the rest of this class never reads a header itself.
        """
        return {
            "max_loaded": self._max_loaded,
            "holdings": [
                {
                    "model_key": key,
                    "reference_count": holding.reference_count,
                    "sessions": sorted(holding.references),
                    "effective_configuration": holding.loaded.effective,
                    # Whether this manager loaded it, and therefore whether it
                    # will ever unload it. A dashboard showing memory needs the
                    # difference: an adopted instance occupies memory that
                    # releasing every lease will not give back.
                    "owned": holding.owned,
                }
                for key, holding in sorted(self._holdings.items())
            ],
            "leases": [
                lease.as_dict() if reveal_owner else {**lease.as_dict(), "owner": None}
                for lease in self._leases.values()
            ],
        }

    async def foreign_instances(self) -> list[str]:
        """Models the runtime holds that this manager did not load.

        Real and worth surfacing: LM Studio loads instances by itself when a
        request wants more context than the running copy has, and a user can
        load anything by hand. They occupy memory this manager is accounting for
        and it does not own them, so it reports them rather than reclaiming them.
        """
        try:
            resident = await self._runtime.list_loaded_models()
        except RuntimeUnavailableError:
            return []
        return sorted(m.model_key for m in resident if m.model_key not in self._holdings)

    # ── Internals ────────────────────────────────────────────────────────────

    def _drop_references(self, session_id: str, model_keys: tuple[str, ...]) -> list[str]:
        """Remove a session's references and report what fell to zero."""
        orphaned = []
        for model_key in model_keys:
            holding = self._holdings.get(model_key)
            if holding is None:
                continue
            holding.references.pop(session_id, None)
            if holding.reference_count == 0:
                orphaned.append(model_key)
        return orphaned

    async def _unload_all(self, model_keys: list[str]) -> list[str]:
        """Unload models nobody holds, keeping the bookkeeping true either way.

        A failed unload drops the holding regardless. Keeping a record of
        something this manager can no longer control means a stuck entry
        occupying capacity forever, which is worse than losing track of a model
        the runtime has its own opinions about.
        """
        unloaded = []
        for model_key in model_keys:
            holding = self._holdings.get(model_key)
            if holding is not None and not holding.owned:
                # Adopted, not loaded here. Dropping the bookkeeping is right;
                # unloading is not ours to do (§11.2's "unload if owned").
                async with self._lock:
                    self._holdings.pop(model_key, None)
                continue
            try:
                await self._runtime.unload(model_key)
                unloaded.append(model_key)
            except RuntimeUnavailableError:
                pass
            finally:
                async with self._lock:
                    self._holdings.pop(model_key, None)
        return unloaded

    async def _expire_stale(self) -> list[str]:
        """Drop lapsed leases and unload what they were holding."""
        async with self._lock:
            now = self._clock()
            expired = [s for s, lease in self._leases.items() if lease.expires_at <= now]
            orphaned: list[str] = []
            for session_id in expired:
                lease = self._leases.pop(session_id)
                orphaned += self._drop_references(session_id, lease.model_keys)
        return await self._unload_all(orphaned)

    async def _ensure_loaded(
        self, model_key: str, configuration: dict[str, Any] | None, policy: ConflictPolicy
    ) -> tuple[LoadedModel, bool]:
        """Get hold of a model, and say whether this manager owns the instance.

        Returns the instance and whether it was loaded here. Both halves matter:
        the caller needs the effective configuration, and the manager needs to
        know at release time whether unloading is its business.
        """
        async with self._lock:
            existing = self._holdings.get(model_key)
            if existing is not None:
                return existing.loaded, existing.owned
            in_flight = self._loading.get(model_key)
            if in_flight is None:
                self._make_room(policy)
                in_flight = asyncio.create_task(
                    self._acquire_instance(model_key, configuration)
                )
                self._loading[model_key] = in_flight

        try:
            # Shielded so a caller that gives up — a cancelled request, a client
            # that disconnected — does not cancel a load another caller is also
            # waiting on. Without this, one abandoned acquire takes the other
            # down with it.
            return await asyncio.shield(in_flight)
        finally:
            async with self._lock:
                if self._loading.get(model_key) is in_flight and in_flight.done():
                    del self._loading[model_key]

    async def _acquire_instance(
        self, model_key: str, configuration: dict[str, Any] | None
    ) -> tuple[LoadedModel, bool]:
        """Adopt an instance the runtime already holds, or load a new one.

        **Asking first is not an optimisation.** LM Studio does not reconfigure
        a resident model to satisfy a request it cannot serve — it loads another
        instance, which is how three copies of one 7B model ended up resident on
        this machine. A manager that issued a load for something already there
        would trigger exactly that, and would then be accounting for one
        instance while the machine held two.

        An adopted instance is tracked but **not owned**: it is reported in
        residency, it counts against capacity, and it is never unloaded here.
        Whoever loaded it still decides when it goes.
        """
        try:
            resident = await self._runtime.list_loaded_models()
        except RuntimeUnavailableError:
            # A runtime that will not answer the question gets the load request,
            # which is where the failure belongs — refusing here would report a
            # discovery problem as a load failure.
            resident = []
        existing = next((m for m in resident if m.model_key == model_key), None)
        if existing is not None:
            return existing, False
        return await self._runtime.load(model_key, configuration), True

    def _make_room(self, policy: ConflictPolicy) -> None:
        """Ensure there is capacity, according to the policy the caller chose.

        Called with the lock held, and `WAIT` does not actually wait here — it
        reports exhaustion. A queue that blocked inside the lock would deadlock
        against every release that could free the capacity it waits for, so
        bounded queueing belongs above this. Saying so is better than a `wait`
        that silently behaves like `reject`.

        **A load in flight counts against the ceiling.** This compared holdings
        alone, and a holding is only recorded once `_ensure_loaded` returns — so
        two callers wanting different cold models both saw an empty table, both
        passed, and both loaded on a manager configured for one. The ceiling
        failed at precisely the moment it exists for: two loads competing for
        the same memory. Counting reservations closes it, and the count is
        race-free because both this check and the `_loading` entry that follows
        it happen under one lock. Found by an external audit, 9 September 2026.

        The two tables never overlap — a key already held returns its holding
        before this is reached, and a key already loading is awaited — so
        adding their lengths counts each model once.
        """
        if len(self._holdings) + len(self._loading) < self._max_loaded:
            return

        # Unreferenced holdings cannot currently exist — release unloads at
        # zero — so `PREEMPT` refuses exactly as `REJECT` does. The branch is
        # kept because §9 names the policy and because it becomes reachable the
        # moment models are retained warm after their last release, which is
        # worth having and is not this milestone. Recorded in STATUS.md rather
        # than left as an unreachable branch nobody knows is unreachable.
        reclaimable = [
            key for key, holding in self._holdings.items() if holding.reference_count == 0
        ]
        if policy is ConflictPolicy.PREEMPT and reclaimable:
            # Only ever an unreferenced holding. §9 forbids unloading a resource
            # another client owns, and preemption is not an exception to that —
            # it reclaims what nobody is using.
            oldest = min(reclaimable, key=lambda key: self._holdings[key].acquired_at)
            self._holdings.pop(oldest, None)
            return

        holders = {key: sorted(h.references) for key, h in self._holdings.items()}
        # Loading keys are named too: "at capacity, held by {}" reads as a bug
        # when the capacity is taken by a load that has not finished, and the
        # reader's next question is always *what is using it*.
        loading = sorted(self._loading)
        raise ResourceExhaustedError(
            f"at capacity ({self._max_loaded} models); held by {holders}"
            + (f"; loading {loading}" if loading else "")
            + f"; policy={policy.value}"
        )
