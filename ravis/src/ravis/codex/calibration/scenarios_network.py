"""K3, the network: approved sites only, and a site added while a task runs (design §10.4, Cal-2).

**What real calibration found** on Codex 0.154.0 (`cal_d2185ed08f50`, then `cal_f4552084e0e0`,
`cal_8cfcf81b2d04` and `cal_a683be8223e0`): an approval never opens the network, under
`untrusted` and `on-request` alike. With Codex's network proxy on (`features.network_proxy`, one
of RAVIS's fixed flags) and the profile's `network={enabled=true, mode="limited", domains={…}}`, a
listed site answers; any other host is refused with one fixed line — `Network access to "<host>"
was blocked: domain is not on the allowlist for the current sandbox mode.` — and a local or private
address is refused outright. So the owner decided (13 September 2026): **no network through
approvals**, an approved-sites allowlist (`DEFAULT_ALLOWED_SITES`), and a per-site ask that adds
one exact host while Codex runs (`agent/sites.py`).

**The list Codex really started with** (Cal-3). Run `cal_330b7525d115` found the add `overridden`:
the site list was in Codex's launch flags, and a `-c` flag outranks every write. Now the launch
flags carry no sites, and RAVIS writes `DEFAULT_ALLOWED_SITES` through `SiteAllowlist` when Codex's
process becomes ready (`service.py`). K3 first waits for that start-up write's answer and fails,
naming Codex's word — `overridden` among them — if Codex didn't take it.

**A loaded thread keeps its site list** (Cal-4, Cal-5). Run `cal_ed672bf12c6f` found the add taken
— `ok`, not overridden — and `example.com` still refused to the turn that was running; run
`cal_85aa0ece0f52` found it refused in that thread's next turn too. The owner decided (14 September
2026) that a task may carry on another way as long as its progress is kept, and approved trying
three in one run. So when the retry in the running turn is refused, K3 tries them in order and stops
at the first that reaches the site (`WAYS`): the thread's next turn with no per-turn sandbox policy;
the same thread reopened — unsubscribed until Codex unloads it (about 50 s on 0.154.0, measured
without a model), then `thread/resume` from disk; and a copy, `thread/fork` with the profile and
roots. A copy
that reaches the site under any other profile fails K3. K3's thread is kept on disk (not ephemeral)
so it can be reopened or copied, and archived afterwards. Each way costs a turn, and runs only when
the one before it didn't get through.

**What K3 checks**, in one thread of project A and one turn of five fixed commands, each approved as
the list says:
- (a) `registry.npmjs.org`, one of the default sites written at the start, answers;
- (b) `example.com`, not on it, is refused with the proxy's fixed line, and RAVIS's own detection
  (`sites.blocked_hosts`) names that host;
- (c) calibration then adds `example.com` exactly as the owner's "allow" does (`SiteAllowlist.add`:
  `config/batchWrite`, upsert, `reloadUserConfig`) while the turn is still running, and the **same
  thread** asks for it again and gets through — in that turn, or else by one of `WAYS` (Cal-5);
  `reached_in` says which. So an added site reaches the task with no restart, and no task loses its
  progress;
- (d) a loopback address stays refused: RAVIS's own listener on 127.0.0.1 is never reached;
- (e) `example.org`, never added, stays refused with the fixed line: the add was one host, no more.

**The add waits for its moment.** The drive stops as soon as command 2 has completed, and
calibration adds only a host RAVIS's detection found. Codex's approval request for command 3 waits,
unanswered, in the inbox until the add is done: under `untrusted` Codex asks before each `curl`
(it did in `cal_8cfcf81b2d04`). If Codex ran command 3 without asking, calibration can't say the
add came first, and K3 is inconclusive.

**Afterwards the site list is put back.** Before the turn, K3 reads the profile's sites in Codex's
user configuration (RAVIS's own Codex home, through `config/read`'s layers); afterwards it writes
them back as they were, so the next run finds `example.com` refused again. If it can't, the owner is
told how to take it off.

The outside hosts are contacted only by Codex's commands on a real run, never by a test.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ravis.agent.calibration_dependent import network_domains_key
from ravis.agent.sites import SiteAllowlist, blocked_hosts, user_sites
from ravis.codex.calibration.harness import (
    POLL_SECONDS,
    Listed,
    ScenarioContext,
    ScenarioResult,
    Session,
    ThreadLog,
    Verdict,
    command_prompt,
)
from ravis.codex.refusals import CodexRefusalError
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError
from ravis.codex.state import SITES_PENDING, SITES_WRITTEN

LISTED_SITE = "registry.npmjs.org"
ADDED_SITE = "example.com"
NEVER_ADDED = "example.org"
LOOPBACK_PATH = "/k3-loopback"
CARRY_ON = "Some of these commands are expected to fail; carry on with the next one."
#: The ways a task can carry on after a site is allowed, tried in this order until one reaches it
#: (Cal-5): the thread's next turn with no per-turn sandbox policy, the thread reopened from disk,
#: and a copy of the thread with its history. Each names its command in `K3Commands`.
WAYS = ("next_step", "reopened", "forked")
#: The copy's key in the session.
FORK_KEY = "A-copy"
#: How often K3 asks whether Codex has unloaded the thread it is waiting to reopen.
UNLOAD_POLL_SECONDS = 2.0
#: How the passing sentence names where the added site answered.
WHERE = {"same step": "that same step", "next step": "the task's next step",
         "reopened": "the same task reopened", "copy": "a copy of the task"}
PUT_BACK_QUESTION = (
    f"Calibration's K3 added {ADDED_SITE} to the Codex sites in RAVIS's Codex folder and couldn't "
    f"take it off again. Remove the {ADDED_SITE} line from config.toml in "
    f"~/.local/share/ravis-codex before the next calibration run, or K3 will find {ADDED_SITE} "
    "already reachable."
)
#: What happened to one command: it got through, the proxy refused its host with the fixed line, it
#: failed some other way, or Codex never ran it.
Outcome = Literal["reached", "blocked", "failed", "not_run"]


class LoopbackListener:
    """A one-line HTTP listener on 127.0.0.1 that notes each path asked for."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.port = 0
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> LoopbackListener:
        self._server = await asyncio.start_server(self._answer, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(OSError, asyncio.IncompleteReadError, TimeoutError):
            async with asyncio.timeout(5):
                line = await reader.readline()
            words = line.decode("latin-1").split()
            self.paths.append(words[1] if len(words) > 1 else "")
            writer.write(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
        writer.close()


# ── The commands, and what one run did ───────────────────────────────────────


@dataclass(frozen=True)
class K3Commands:
    """K3's five commands, in the order the prompt lists them."""

    listed: Listed
    before: Listed
    after: Listed
    loopback: Listed
    never_added: Listed
    #: The added site once more, one command per way of carrying the task on (`WAYS`), each asked
    #: only when the ways before it didn't reach the site. None is one of the first turn's commands.
    next_step: Listed
    reopened: Listed
    forked: Listed

    @property
    def in_order(self) -> list[Listed]:
        return [self.listed, self.before, self.after, self.loopback, self.never_added]


def k3_commands(port: int) -> K3Commands:
    return K3Commands(
        listed=Listed(f"curl -sS -m 8 -o /dev/null https://{LISTED_SITE}/"),
        before=Listed(f"curl -sS -m 8 -o /dev/null https://{ADDED_SITE}/"),
        # The same site again, its path different only because a listed text is allowed once.
        after=Listed(f"curl -sS -m 8 -o /dev/null https://{ADDED_SITE}/index.html"),
        loopback=Listed(f"curl -sS -m 5 http://127.0.0.1:{port}{LOOPBACK_PATH}"),
        never_added=Listed(f"curl -sS -m 8 -o /dev/null https://{NEVER_ADDED}/"),
        next_step=Listed(f"curl -sS -m 8 -o /dev/null https://{ADDED_SITE}/next-step"),
        reopened=Listed(f"curl -sS -m 8 -o /dev/null https://{ADDED_SITE}/reopened"),
        forked=Listed(f"curl -sS -m 8 -o /dev/null https://{ADDED_SITE}/copy"),
    )


@dataclass
class K3Run:
    """What one K3 run did beyond what its session saw."""

    commands: K3Commands
    #: Where RAVIS's start-up write of the default sites stood when K3 began (`state.SITES_*`).
    default_sites: str = SITES_PENDING
    #: The profile's sites in Codex's user configuration before the run; None when unreadable.
    sites_before: dict[str, Any] | None = None
    #: A cap, a refusal, or Codex off the list: why the turn didn't run as asked.
    stopped: str | None = None
    add_tried: bool = False
    #: Why `SiteAllowlist.add` refused (`overridden`, `not_written`, …); None when it added.
    add_refused: str | None = None
    #: The transcript's number once Codex had answered the add.
    added_at: int | None = None
    #: Whether the site list was written back; None when nothing was added to put back.
    put_back: bool | None = None
    loopback_paths: list[str] = field(default_factory=list)
    #: What happened on each way tried besides its command: a refusal, how long unloading took.
    notes: dict[str, str] = field(default_factory=dict)
    #: The profile Codex says the copy runs under; None when there was no copy.
    fork_profile: str | None = None


# ── The scenario ─────────────────────────────────────────────────────────────


async def k3(ctx: ScenarioContext) -> ScenarioResult:
    """(a)–(e) above: approved sites only, a site added mid-turn reaches it, nothing local."""
    session = ctx.session("K3")
    async with LoopbackListener() as listener:
        run = K3Run(k3_commands(listener.port))
        try:
            await _k3_turn(ctx, session, run)
        except CodexRpcError as refusal:
            run.stopped = f"Codex refused a request: {refusal.message}"
        finally:
            await _put_sites_back(ctx, session, run)
            await _archive_threads(session)
            await session.close()
        run.loopback_paths = list(listener.paths)
    return k3_verdict(session, run)


async def _k3_turn(ctx: ScenarioContext, session: Session, run: K3Run) -> None:
    listed = run.commands.in_order
    run.default_sites = await _default_sites_at_start(ctx)
    run.sites_before = await _user_sites(ctx, session)
    # Kept on disk, so it can be reopened or copied (Cal-5).
    log = await session.start_thread("A", ctx.plan.project_a, ephemeral=False)
    session.expect("A", listed)
    await session.start_turn("A", command_prompt("K3", listed, (CARRY_ON,)))
    before = run.commands.before.text
    cap = ctx.timings.turn_cap_seconds
    if not await session.drive(lambda: before in log.commands or log.turn_done, cap):
        await session.interrupt("A")
        run.stopped = f"command 2 didn't finish within {cap:g} seconds"
        return
    # Only a host RAVIS's own detection found is added, as the owner's site ask would.
    if ADDED_SITE in blocked_hosts(log.commands.get(before, {}).get("output")):
        await _add(ctx, session, run)
    if not await _turn_ended(session, run, "A"):
        return
    if run.added_at is not None and outcome(log, run.commands.after, ADDED_SITE) != "reached":
        await _carry_on_until_reached(ctx, session, run)


async def _carry_on_until_reached(ctx: ScenarioContext, session: Session, run: K3Run) -> None:
    """Cal-5: a loaded thread keeps its site list, so try each way of carrying the task on."""
    for way in WAYS:
        key = await _carry_on(ctx, session, run, way)
        if key is None:
            continue
        if not await _turn_ended(session, run, key):
            return
        if outcome(session.threads[key], getattr(run.commands, way), ADDED_SITE) == "reached":
            return


async def _carry_on(ctx: ScenarioContext, session: Session, run: K3Run, way: str) -> str | None:
    """Start the turn that asks for the added site again, one way; its thread's key, or None."""
    command: Listed = getattr(run.commands, way)
    key = FORK_KEY if way == "forked" else "A"
    prompt = command_prompt("K3", [command])
    try:
        if way == "reopened":
            await _reopen(ctx, session, run)
        elif way == "forked":
            await _fork(ctx, session, run)
        session.expect(key, [command])
        if way == "next_step":
            await session.start_turn(key, prompt, box=None)
        else:
            await session.start_turn(key, prompt)
    except CodexRpcError as refusal:
        run.notes[way] = f"Codex refused: {refusal.message}"
        return None
    return key


async def _reopen(ctx: ScenarioContext, session: Session, run: K3Run) -> None:
    """Let Codex unload thread A, then resume it from disk, as a task comes back after a switch."""
    log = session.threads["A"]
    await session.request("thread/unsubscribe", {"threadId": log.thread_id})
    loop = asyncio.get_running_loop()
    began = loop.time()
    deadline = began + ctx.timings.unload_seconds
    while await _loaded(session, log.thread_id) and loop.time() < deadline:
        await session.drive(lambda: False, min(UNLOAD_POLL_SECONDS, deadline - loop.time()))
    if await _loaded(session, log.thread_id):
        run.notes["reopened"] = f"still loaded after {ctx.timings.unload_seconds:g} s"
    else:
        run.notes["reopened"] = f"unloaded after {loop.time() - began:.0f} s"
    root = str(log.project.root)
    await session.request("thread/resume", {
        "threadId": log.thread_id, "cwd": root, "approvalPolicy": "untrusted",
        "approvalsReviewer": "user", "permissions": ctx.profile_name,
        "runtimeWorkspaceRoots": [root],
    })


async def _loaded(session: Session, thread_id: str) -> bool:
    listed = await session.request("thread/loaded/list", {})
    data = listed.get("data") if isinstance(listed, dict) else None
    return isinstance(data, list) and thread_id in data


async def _fork(ctx: ScenarioContext, session: Session, run: K3Run) -> None:
    """Copy thread A, history and all, into a new thread held under `FORK_KEY`."""
    log = session.threads["A"]
    root = str(log.project.root)
    forked = await session.request("thread/fork", {
        "threadId": log.thread_id, "cwd": root, "approvalPolicy": "untrusted",
        "approvalsReviewer": "user", "permissions": ctx.profile_name,
        "runtimeWorkspaceRoots": [root], "ephemeral": False,
    })
    thread = forked.get("thread") if isinstance(forked, dict) else None
    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
        raise CodexRpcError("thread/fork", -32603, "Codex copied a thread without an id")
    profile = forked.get("activePermissionProfile")
    run.fork_profile = str(profile.get("id")) if isinstance(profile, dict) else None
    session.hold(FORK_KEY, str(thread["id"]), log.project)


async def _archive_threads(session: Session) -> None:
    """Leave K3's threads archived, out of the way of live tasks."""
    for log in session.threads.values():
        with contextlib.suppress(CodexRpcError, CodexUnavailableError):
            await session.codex.request("thread/archive", {"threadId": log.thread_id}, timeout=5.0)


async def _turn_ended(session: Session, run: K3Run, key: str) -> bool:
    """Drive one thread's turn to its end; False, with the reason noted, if it went off script."""
    capped = await session.run_turns([key])
    if capped or session.off_list:
        run.stopped = capped or f"Codex didn't keep to the list: {session.off_list[0]}"
        return False
    return True


async def _add(ctx: ScenarioContext, session: Session, run: K3Run) -> None:
    """Add the blocked site exactly as the owner's "allow" does, while the turn still runs."""

    async def request(method: str, params: dict[str, Any], *, timeout: float) -> Any:
        return await session.request(method, params, timeout)

    allowlist = SiteAllowlist(
        request, lambda: ctx.profile_name, seconds=ctx.timings.request_seconds
    )
    run.add_tried = True
    try:
        await allowlist.add(ADDED_SITE)
    except CodexRefusalError as refusal:
        run.add_refused = str(refusal.details.get("reason") or refusal.message)
        return
    run.added_at = session.order


async def _default_sites_at_start(ctx: ScenarioContext) -> str:
    """The start-up write's outcome, once Codex has answered it (or a request's wait has passed)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ctx.timings.request_seconds
    while ctx.default_sites() == SITES_PENDING and loop.time() < deadline:
        await asyncio.sleep(POLL_SECONDS)
    return ctx.default_sites()


async def _user_sites(ctx: ScenarioContext, session: Session) -> dict[str, Any] | None:
    try:
        read = await session.request("config/read", {"includeLayers": True})
    except CodexRpcError:
        return None
    return user_sites(read, ctx.profile_name)


async def _put_sites_back(ctx: ScenarioContext, session: Session, run: K3Run) -> None:
    """Write the profile's user-level sites back as they were before K3 added one."""
    if not run.add_tried:
        return
    if run.sites_before is None:
        run.put_back = False
        return
    edit = {"keyPath": network_domains_key(ctx.profile_name), "mergeStrategy": "replace",
            "value": run.sites_before}
    try:
        written = await session.request(
            "config/batchWrite", {"edits": [edit], "reloadUserConfig": True}
        )
    except (CodexRpcError, CodexUnavailableError):
        run.put_back = False
        return
    run.put_back = isinstance(written, dict) and written.get("status") in ("ok", "okOverridden")


# ── The verdict ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class K3Facts:
    listed: Outcome
    before: Outcome
    after: Outcome
    next_step: Outcome
    reopened: Outcome
    forked: Outcome
    loopback: Outcome
    never_added: Outcome
    default_sites: str
    stopped: str | None
    add_refused: str | None
    asked_again_after_adding: bool
    fork_profile: str | None
    profile: str
    #: `K3Run.notes`, as one line for a verdict's sentence.
    notes: str

    @property
    def carried_on(self) -> tuple[Outcome, ...]:
        """The added site's outcome on each way of carrying the task on, in `WAYS` order."""
        return tuple(getattr(self, way) for way in WAYS)


#: K3's judgement: the first check that holds gives the verdict, `{name}` filled from the facts.
#: Anything reached that shouldn't have been comes first, before what only makes K3 inconclusive.
K3_CHECKS: tuple[tuple[Callable[[K3Facts], bool], Verdict, str], ...] = (
    (lambda f: f.loopback == "reached", "failed", "A command reached a local address (127.0.0.1)."),
    (lambda f: f.never_added == "reached", "failed",
     f"{NEVER_ADDED}, which was never added, was reachable."),
    (lambda f: f.before == "reached", "failed", f"{ADDED_SITE} was reachable before it was added."),
    (lambda f: f.default_sites != SITES_WRITTEN, "failed",
     "Codex didn't take RAVIS's default sites when its process started ({default_sites}), so no "
     "task could start."),
    (lambda f: f.stopped is not None, "inconclusive", "{stopped}"),
    (lambda f: "not_run" in (f.listed, f.before, f.after, f.loopback, f.never_added),
     "inconclusive", "Codex didn't run every command on the list."),
    (lambda f: f.listed != "reached", "failed",
     f"{LISTED_SITE}, on the approved list, couldn't be reached."),
    (lambda f: f.before != "blocked", "failed",
     f"{ADDED_SITE} was refused without the proxy's fixed line, so RAVIS couldn't ask about it."),
    (lambda f: f.add_refused is not None, "failed",
     f"Codex didn't add {ADDED_SITE} while the task ran ({{add_refused}})."),
    (lambda f: not f.asked_again_after_adding, "inconclusive",
     f"Codex asked for {ADDED_SITE} again without waiting for approval, so calibration can't say "
     "the site was added first."),
    (lambda f: f.after != "reached" and f.carried_on == ("blocked", "blocked", "reached")
     and f.fork_profile != f.profile, "failed",
     f"The copy of the task that reached {ADDED_SITE} ran under {{fork_profile}}, "
     "not {profile}."),
    (lambda f: f.after != "reached" and "reached" not in f.carried_on
     and "not_run" in f.carried_on, "inconclusive",
     f"{ADDED_SITE} wasn't reached, and not every way of carrying the task on ran ({{notes}})."),
    (lambda f: f.after != "reached" and "reached" not in f.carried_on, "failed",
     f"{ADDED_SITE}, added while the task ran, wasn't reachable in its next step, after reopening "
     "it, or in a copy of it."),
    (lambda f: f.never_added != "blocked", "inconclusive",
     f"{NEVER_ADDED} failed without the proxy's fixed line."),
)


def k3_verdict(session: Session, run: K3Run) -> ScenarioResult:
    log, commands = session.threads.get("A"), run.commands
    facts = K3Facts(
        listed=outcome(log, commands.listed, LISTED_SITE),
        before=outcome(log, commands.before, ADDED_SITE),
        after=outcome(log, commands.after, ADDED_SITE),
        next_step=outcome(log, commands.next_step, ADDED_SITE),
        reopened=outcome(log, commands.reopened, ADDED_SITE),
        forked=outcome(session.threads.get(FORK_KEY), commands.forked, ADDED_SITE),
        loopback=loopback_outcome(log, commands.loopback, run.loopback_paths),
        never_added=outcome(log, commands.never_added, NEVER_ADDED),
        default_sites=run.default_sites,
        stopped=run.stopped,
        add_refused=run.add_refused,
        asked_again_after_adding=_asked_after(session, commands.after.text, run.added_at),
        fork_profile=run.fork_profile,
        profile=session.profile_name,
        notes="; ".join(f"{way}: {note}" for way, note in run.notes.items()) or "no notes",
    )
    findings: dict[str, Any] = {
        **vars(facts), "added_live": run.add_tried and run.add_refused is None,
        "loopback_paths_reached": run.loopback_paths, "site_list_put_back": run.put_back,
        "reached_in": reached_in(facts),
    }
    if run.put_back is False:
        findings["owner_question"] = PUT_BACK_QUESTION
    for holds, verdict, detail in K3_CHECKS:
        if holds(facts):
            return session.result(verdict, detail.format(**vars(facts)), **findings)
    where = WHERE[reached_in(facts) or "same step"]
    return session.result(
        "passed",
        f"Codex took RAVIS's default sites at its start and {LISTED_SITE} answered; "
        f"{ADDED_SITE} was refused with the proxy's fixed line, added "
        f"while the task ran and then reached in {where}; a local address and "
        f"{NEVER_ADDED} stayed refused.",
        **findings,
    )


def reached_in(facts: K3Facts) -> str | None:
    """Where the added site first answered: the running step, a way of carrying on, or nowhere."""
    if facts.after == "reached":
        return "same step"
    names = {"next_step": "next step", "reopened": "reopened", "forked": "copy"}
    return next((names[way] for way in WAYS if getattr(facts, way) == "reached"), None)


def outcome(log: ThreadLog | None, command: Listed, host: str) -> Outcome:
    ran = log.commands.get(command.text) if log is not None else None
    if ran is None:
        return "not_run"
    if ran.get("exit_code") == 0:
        return "reached"
    return "blocked" if host in blocked_hosts(ran.get("output")) else "failed"


def loopback_outcome(log: ThreadLog | None, command: Listed, paths: list[str]) -> Outcome:
    """Refused however Codex words it: the rule is only that the listener is never reached."""
    if LOOPBACK_PATH in paths:
        return "reached"
    ran = log.commands.get(command.text) if log is not None else None
    if ran is None:
        return "not_run"
    return "reached" if ran.get("exit_code") == 0 else "blocked"


def _asked_after(session: Session, text: str, order: int | None) -> bool:
    """Whether Codex asked to run `text` only after the add was answered."""
    if order is None:
        return False
    return any(a.command == text and a.order > order for a in session.approvals)
