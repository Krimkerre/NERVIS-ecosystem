# What NERVIS remembers, and which store owns it

> Inventory taken 23 September 2026, against NERVIS 0.34.89, on the owner's Mac. The question
> behind it: *"I've been reading about AI second brains — is this something we would need, or
> do we need Obsidian?"* The answer turned out to be neither. NERVIS already has five stores
> that remember things and **four separate paths that carry memory into a prompt**, and they do
> not know about each other. This is the map, the overlaps, and a proposal for who owns what.
>
> Nothing here is a decision yet. It is written to be argued with.

## 1. The short version

A "second brain" is a folder of notes that can be linked, searched, and read back. NERVIS has
that already: six markdown files, 240 KB, in `nervis/knowledge/`, version-controlled, retrieved
by both wording and meaning — plus a seventh file it writes itself when somebody says *"remember
that…"*. Obsidian is an editor over exactly this kind of folder. It would be a pleasant window
onto `nervis/knowledge/`; it should not become a dependency, because the storage, the retrieval
and the history are the parts that matter and this repository already does all three.

What is actually missing is not a tool. It is **an owner per kind of fact**. Today five stores
answer the question "what does NERVIS know?", four prompt paths deliver it, two of them ignore
the bar that is supposed to keep a conversation private, and one of them silently drops two
thirds of what it finds.

## 2. Every store that holds something a person would call knowledge

| Store | What it holds | Written by | Where | Read back | Retention |
|---|---|---|---|---|---|
| **Shipped notes** | How the four services work and why: 6 files, 240 KB, ~27 sections in `nervis.md` alone | People, by hand | `nervis/knowledge/*.md` | Scored per question (§3, path E) | Forever, reviewed by hand |
| **Learned notes** | Sentences the owner told NERVIS, each with the date and the sentence that prompted it | NERVIS, on a confirmed button — offered when asked for *or* when a standing statement is noticed | `nervis/knowledge/learned.md` | Same retrieval as the shipped notes | Forever, deleted by heading |
| **Conversations** | 238 conversations, 1,032 turns on this Mac | NERVIS, per turn | `chat_conversation`, `chat_message` | Four ways (§3) | **Forever. Nothing expires** |
| **Conversation summaries** | One rolling summary per long conversation (2,000 chars max) | NERVIS, in the background after a reply | `chat_summary` | Path A | Until the conversation is deleted |
| **Settings** | 74 rows: preferences, personas, the NAS address, which voice, which pool | Screens | `setting` | Directly | Forever |
| **Voices, proposals, background runs, notifications** | 7 voices; 16 accept/decline outcomes; 65 unattended runs; 290 notifications | NERVIS | their own tables | Screens | Forever (dismissed notifications: 30 days) |
| **Files** | Uploads, attachments, the library | People | Workspace rooms | Files tab | Attachments and trash: 14 days. Library: forever |
| **The browser's copy** | The last 50 conversations, per browser profile | The page | `localStorage` | The history drawer | Until site data is cleared |

Operational machinery — events (14 days), traces, logs (14 days), migrations, circuit breakers,
the install identity — is deliberately out of scope here. Nobody thinks of those as "what NERVIS
knows about me".

## 3. The four-and-a-bit paths into a prompt

This is the part that matters, and the part nobody had written down. On a single chat turn, up to
**five** different mechanisms can put remembered text in front of the model:

| | Path | What it sends | Size | Switch | Default |
|---|---|---|---|---|---|
| **A** | Compaction summary | A summary of this conversation's older turns, **dated** | ≤ 2,000 chars | `chat.compaction` | **on** |
| **B** | Cross-conversation recall | Keyword matches from *other* conversations, **the whole history** | 3 passages, ~4,800 chars (was ~400) | `recall.enabled` | off by default, **on here** |
| **C** | Persona digest | The **person's** last 6 turns in each of the 5 newest conversations, dated, no matching at all | ~4,100 chars, **every turn** | `chat.memory` = `all` | `session` by default, **`all` here** |
| **D** | Quoted older turns | Turns from this conversation's summarised part that share words with the question, each **dated** | ≤ 3,000 chars, ≤ 4 turns | follows `chat.compaction` | on |
| **E** | Shipped + learned notes | The documentation sections that match the question | ≤ 6,000 chars | — | always |

Five paths, five different framings, no shared budget, and no de-duplication between them. With
B and C both on, the same sentence from last week's conversation can arrive twice in one request,
described two different ways.

**C had a sixth problem the table did not show, and it reached the owner** (23 September 2026):
it carried NERVIS's own replies as well as the person's, unfenced, with a three-day-old closing
line sitting in the position nearest the question. Chat copied it into an unrelated conversation
and greeted the owner by the name of a visitor who was not there. C now carries their side only.
That is a trim, not the merge this section argues for — B and C still overlap.

## 4. What is wrong today

Each of these was verified against the running system, not inferred.

> **Items 1, 2, 4 and 5 were settled the same day.** The bar now applies to both paths
> (NERVIS 0.34.90); recall delivers what it finds (0.34.91); capture fires without a password
> and every switch moved into chat (0.34.92); `learned.md` is git-ignored because the repository
> is public, and notes cross the link instead. The rest stand as written.
>
> **One correction to this document.** §3 recorded recall's default (off) where it should have
> recorded this machine's state. `recall.enabled` is `1` here and `chat.memory` is `all`: paths
> B and C have both been running on real conversations throughout.

**1. The "Private" bar does not apply to recall.** *(fixed, 23 September 2026 — one reader in
`chat.barred()`, honoured by both paths, excluded in the query rather than after it.)* Marking a conversation private writes its id
into `chat.memory_excluded`. The persona digest (C) honours that list. `recall.py` never reads it
— its only filter is *"not the conversation I am in"*. So with recall on, a conversation the owner
marked private is still searched, and still quotable into any other conversation. Two switches,
two exclusion policies, one screen. **This is the one to fix first**, because it is the only
finding here that breaks a promise the interface makes.

**2. Recall delivers a third of what it finds.** *(fixed, 23 September 2026 — the block and each
passage now carry this module's own bounds; three of three conversations delivered.)* `recall.block()` fences its passages without
passing a size, so it inherits `MAX_FIELD_CHARS = 400` and clips the *whole joined block*.
Measured: three 600-character passages plus their answers — 3,600 characters — arrive as 1,398
characters of which only **one conversation of the three is named**. Passages two and three are
cut off mid-sentence. The search works; the delivery does not.

**3. Three of the four conversation paths hand the model undated text.** *(fixed, 23 September
2026, in two passes. The persona digest first — `[Introducing You to Benny, last spoken in on
2026-09-20]` — because it was the same bug as the one below; then the compaction summary, which
names the stretch of days it covers, and the quoted turns, which carry the date each was said on.
All four paths are dated now, and the dates are the conversation's rather than today's, so the
summary note in front of the question stays byte-identical between turns.)* Only recall says when
something was said. The compaction summary, the quoted turns and the persona digest all arrive
with no date, so a model cannot tell last night's decision from one superseded in August. NERVIS's
own house rule — that a stale record which gets believed is worse than none — is not being applied
to its own memory.

**4. Capture exists and has never been used.** *(fixed, 23 September 2026 — NERVIS notices the
shape of a standing statement and offers a chip; NERVIS 0.34.92.)* Saying *"remember that the GPU
box has an RX 6800"* offers a button; pressing it files the sentence with its date and origin. The
machinery is built, tested and documented — and `learned.md` does not exist on this machine.
Nothing has ever been filed. Worth knowing before building anything new: the gap is not
capability, it is habit.

The diagnosis was half right. The gap was not habit either — it was that the trigger was a
password. Nobody says *remember that* to a chat window, so the feature could only ever be used by
somebody who had read its documentation. What widened is the trigger; the confirmation step,
and the rule that only the person's own words are stored, did not move. The owner chose that
shape over automatic filing when asked. **And the switch was in the wrong place**, which is the
same finding wearing different clothes: *"hiding the option in some settings menu out of chat is
bonkers"*. Every memory switch now lives in chat's own parameters panel.

**5. Learned notes would be committed to git.** *(settled, 23 September 2026: ignored, and
carried over the link instead — `GET`/`POST /api/v1/learned/peer`.)* `nervis/knowledge/learned.md` is not ignored, so
personal facts would land in the repository and travel to the other computer with a `git pull`.
That may be exactly what is wanted — it is a free sync — but it should be a decision rather than
an accident, because "remember that my sister's birthday is…" would be in a public-ish repo.

**6. Recall's reach is the newest 400 messages.** *(fixed, 23 September 2026 — the word match
moved into the query, so the limit bounds matching turns rather than recent ones; NERVIS 0.34.95.
Measured at the time: 61 of 237 conversations reachable, and two of three ordinary questions
returning nothing at all.)* A conversation slips out of reach entirely once 400 newer messages
exist, silently, with no indication anywhere. On this Mac, 1,032 messages exist: well over half
the history is already unreachable by that path.

**7. Two encodings in the settings table, again.** `recall.enabled` is stored as the string `"1"`,
while every neighbouring setting is JSON. Migration 11 exists because that exact confusion broke
the voice profile pointer once already.

**8. Dead fields that look alive.** `chat_message.route_decision_id` and
`chat_summary.through_message_id` are always empty; `chat.memory_skips_current` is on the export
allowlist and read nowhere. Each one is a thing a future reader will believe.

**9. No links, no review.** There is no way for one note to point at another, and nothing is ever
re-read or expired. That is the one place where "second brain" thinking has something real to add.

## 5. Proposed ownership

One owner per kind of fact; everything else is a cache or a view of it.

| Kind of fact | Owner | Everything else |
|---|---|---|
| How the system works | Shipped notes (`nervis/knowledge/*.md`) | Chat quotes them; they overrule learned notes on conflict — already true |
| Something the owner told NERVIS | Learned notes (`learned.md`) | Retrieved by the same path; conflicts shown, not silently resolved — already true |
| What was said, ever | `chat_message` | The browser's copy is a cache; the summary is a lossy view; recall and quotes are searches over it |
| Preferences and configuration | `setting` | Exported through the allowlist; screens are views |
| What the owner accepted or declined | `proposal_outcome` | Used to shape the next offer |
| Files | The workspace | Attachments are per-conversation copies that expire |

The rule that follows: **no new store.** Every one added is another thing that can go stale,
another thing to sync between two computers, and another path into a prompt that the other four
cannot see.

## 6. What to do, smallest first

1. ~~**Make the Private bar mean one thing.** One exclusion list, honoured by every path that reads
   conversations.~~ **Done:** `chat.barred()`, read by both paths, excluded in the query.
2. ~~**Deliver what recall finds** — pass a real size to the fence, or cap per passage rather than
   per block. Today two thirds is thrown away after the work of finding it.~~ **Done:** the block
   and each passage carry this module's own bounds; three of three conversations delivered.

   2b. ~~**Make capture reachable** — the trigger is a phrase nobody says.~~ **Done:** offered on
   the shape of a statement, once per sentence, still confirmed by hand. And every memory switch
   moved out of Settings into chat, where the memory is used.
3. ~~**Put a date on every remembered thing that reaches the model.** Recall already does it; the
   other three paths need one line each.~~ **Done:** all four paths are dated. It was not one
   line each — the two compaction paths had no timestamps to hand, because the read they use
   returns the shape a provider is sent, and widening it would have put an `at` key on every
   message going to a model.
4. ~~**Decide about `learned.md` and git** — ignore it (private, per machine, travels only through
   the link) or keep it tracked (shared, in history, visible in diffs). Either is defensible; the
   accident is not.~~ **Done:** ignored, and shared through the link.
5. **Then, and only then, links.** A note that can point at another note, and retrieval that
   follows the pointer one hop. That is the genuinely new capability, and it is worth having the
   house in order first.
6. **Not now:** review and decay (a "still true?" prompt after some months). Worth doing once
   there is enough in `learned.md` for it to matter. Today it would be a mechanism with nothing
   to act on.

## 7. About Obsidian

It is an editor for a folder of markdown files, with links, backlinks and a graph view. Opening
`nervis/knowledge/` in it would work tonight and require nothing. What it should **not** be is the
place NERVIS's memory lives: this repository already gives those files version history, a gate
that checks the claims in them (`tools/knowledge_check.py`), and a retrieval path tuned against
measured scores. Obsidian offers none of that and would add a desktop application to the list of
things that must be installed for the system to remember anything.

Use it as a window if the editing experience appeals. Keep the files as the truth.
