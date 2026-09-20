# SIRVIS — what it does and how it decides

SIRVIS measures models on this machine and publishes the evidence. It does not
route requests and does not choose models for anybody: it produces the numbers
RAVIS's preferences can be built on.

## What it measures

Benchmark runs against local runtimes, recording throughput, latency,
time-to-first-token, and whether a build emits well-formed tool calls. Results
carry the machine, the runtime, the model build and the conditions — not just a
score, because a number without its conditions cannot be compared to anything.

## How it decides what a result means

**A result is VALID or SUSPECT, and SUSPECT is the interesting one.** A run that
happened under memory pressure, thermal throttling or swap is marked suspect,
with every reason attached. The verdict *is* the list of warnings: validity is
"suspect if warnings else valid", so the notes are the whole of the
justification rather than a caveat beside it. On a Mac the heat reading is macOS's thermal state; on
Linux (since SIRVIS 0.19.6) it is the kernel's temperature sensors, and a figure the machine doesn't
report is left out rather than counted as fine.

**Withdrawn measurements leave a tombstone.** A deleted result is recorded as
deleted, because otherwise there is no way to tell a measurement somebody
retracted from one nobody ever took.

**Recommendations are weighted and say so.** A recommendation names the evidence
behind it and its coverage, so an operator can see whether it rests on eighty
runs or two.

## What an operator can ask it for

The machines, runtimes, models and instances it knows about; benchmark runs and
their results with provenance; the job queue; model leases; residency — what is
loaded right now; and its recommendations with the evidence under them.

Since 12 September 2026 it can also find and download models. Discover searches
Hugging Face for GGUF and MLX models, lists each version's size, and checks it
against the free disk before anything starts: a model that does not fit is
refused, and one that would leave less than 20 GB free, or use more than half of
what is free, asks for confirmation first. LM Studio does the downloading and
SIRVIS keeps the record, so closing the page or restarting loses nothing. A model
behind a licence on Hugging Face is shown but not offered, because LM Studio
would need a Hugging Face login to fetch it.

Discover can sort by most downloaded in the last 30 days or of all time, most
liked, trending, recently updated, newest, or smallest first, and **Top 10**
shows the ten most popular in whichever order is chosen. Hugging Face can sort by
downloads in the last 30 days, likes, trending, last update and creation date,
but not by all-time downloads or by size. So for "all time" and "smallest first"
SIRVIS reads the 200 most downloaded matches and puts them in order itself, which
means a model nobody downloads any more can be missing from "all time". **Runs on this Mac** keeps models
whose usual version fits in three quarters of this Mac's memory. That is an
estimate from the model's parameter count, at 4-bit for GGUF and at the precision
in the name for MLX; the Sizes button shows the real files. When Hugging Face's
count is missing or clearly wrong, as it is for a model split into pieces, the
size in the model's name (such as 27B) is used instead.

A **Max size** slider limits the list to models estimated at or under a chosen
size, from 1 GB to 128 GB or any size; with Runs on this Mac on as well, the
smaller of the two limits applies. Clicking a model shows its details: who
published it, parameters, architecture, context length, licence, the model it is
based on, what it is for, downloads, likes, when it was created and updated, and
a link to its Hugging Face page. Each downloadable version is marked when it runs
on this Mac, judged by its real file size.

Since 12 September 2026 SIRVIS also knows how big each installed model is on disk, read from LM
Studio's own listing, so its model list, the menu bar app and the memory check before loading can
all show and use a real size.

## What a crash does to a benchmark

A job that was *running* when the service stopped is marked failed on the next
start, with the reason "the service stopped while this job was running". A job
still *queued* is left alone and the worker picks it up, because it never
started and survives honestly.

**An interrupted benchmark is never re-run on its own.** Re-running it would
load a model and occupy the machine for minutes on work nobody was told had
restarted — and would do it again after every crash. Resubmitting is the
operator's call, and the failed row says exactly what happened so the decision
can be made on evidence.

## Stopping a benchmark, and what bounds one that will not stop

**Cancel takes effect within one generation.** The flag is read before every
generation — warmups, measured repetitions and tool trials alike — so the
longest a stop waits is the read already in flight. It was read only between
whole tests until 9 September 2026, which on a three-repetition spec meant a
dozen generations, with the twenty-four tool trials starting afterwards
regardless; pressing cancel bought twenty-five further generations.

**A single generation may not run longer than its budget**, five minutes by
default and settable per benchmark. The cooperative stop cannot help with a read
that never returns, and the runtime's own timeout does not close that gap: it
bounds the interval between chunks, so a model producing one token a second runs
forever and one producing nothing takes ten minutes to admit it. Past the
budget the read is cut off, the run fails naming the model and the phase, and
the model is released.

Mid-generation cancellation is otherwise deliberately absent: killing a running
read loses the memory readings and partial results that are most of what a run
ending early is worth.

**A set of tool trials that was cut short publishes no reliability figure.**
Four attempts and twenty-four are not the same measurement, and that rate
outlives the run it came from. The raw attempts are kept; the verdict is not,
because otherwise a good build looks unreliable because somebody cancelled once.

## What a failed benchmark does with the model

It gives it back. Everything after the model is acquired runs inside the block
that releases it — the memory sample, the log write, the variant confirmation,
the inventory read, the thermal reading. Until 9 September 2026 those five sat
*outside* it, so any of them failing left the model resident with nothing to
reclaim it, and the next run found the machine full because of a run that had
already given up. The sharpest case was the variant gate: the one check designed
to stop a run was also the one that leaked a model every time it fired.

**The load ceiling counts loads in flight, not only finished ones.** Two callers
wanting different cold models used to see an empty table, both pass the check,
and both load on a manager configured for one — the ceiling failing at exactly
the moment it exists for.

## Showing and deleting an installed model

Since SIRVIS 0.19.7 each row on SIRVIS → Models has **Show files**, which opens the model's folder
in Finder (or the Linux file manager, or Windows Explorer under WSL), and **Delete**, which moves
the model to the Trash after a second click — never erased, so it can be put back. A model LM
Studio installed from its catalogue is two folders, a small description under `~/.lmstudio/hub`
and the weights under `~/.lmstudio/models`; both go. Delete is refused while the model is loaded,
keeps anything another installed model still needs, and refuses a model that came with LM Studio
itself (the small embedding model), which only uninstalling LM Studio removes.

## Models on another machine through LM Link

LM Studio's **LM Link** lets one machine use models installed on another (say, a model hosted on
the Mac used from a Linux laptop). Since SIRVIS 0.19.9 those models are listed apart: SIRVIS →
Models and SIRVIS's front page show this machine's models first, then a heading "On <other
machine> · LM Link" with the other machine's. They have no Show files or Delete (their files are on
that machine) and SIRVIS won't benchmark them here (the numbers would be that machine's). The
counts at the top of the pages are this machine's alone. The menu bar app and the Linux tray do the
same: a divider, "On <other machine> (LM Link)", and a model loaded there reads "Loaded on <other
machine>" rather than as loaded here. Loading one from here works — it loads into the other
machine's memory. Before 19 September 2026 LM Studio's listing gave these models no mark, so a
model loaded on the Mac showed as loaded on the laptop. Used for real on 20 September: the owner
loaded a Mac-hosted model from the ThinkPad and cancelled a load part-way, and both behaved as
described here. Since SIRVIS 0.19.10 a linked model doesn't
use up this machine's limit on loaded models (it sits in the other machine's memory); the Runtime
screen marks it "LM Link". Since SIRVIS 0.19.11 the limit counts every model in this machine's
memory, whoever loaded it — by hand in LM Studio, by LM Studio itself, or by your other machine
through LM Link — so the Mac's limit sees what the ThinkPad loads on it. Asking SIRVIS for one of
those takes no extra place. A refusal says "also in memory, loaded outside SIRVIS" and names them;
unload them in LM Studio to free a place. Since SIRVIS 0.19.12 a model whose requester gave up
while it was still loading (a stopped benchmark, a closed tab) is unloaded again as soon as it
lands, instead of staying in memory held by nobody. A linked model counts as local for private and
local-only requests: the owner decided on 19 September 2026 that both laptops are theirs.

## When LM Studio or Hugging Face is down

SIRVIS checks every ten seconds whether LM Studio is answering (a read that loads nothing).
While it isn't, the SIRVIS screens show a yellow banner, "LM Studio isn't answering", listing
what stops until it is back: loading and unloading models, Show files and Delete, listing
installed models, starting downloads and benchmarks, and recommendations that know what is
installed. Buttons that can't work are greyed out with that reason. Results, Runtime Sets and
past downloads still read normally. Fix: open LM Studio and start its server; the banner goes
by itself within ten seconds.

Hugging Face is only judged by SIRVIS's last real search: if it failed, a banner says Hugging
Face failed the last read and Discover's search and new downloads may not work. SIRVIS never
contacts Hugging Face on its own for this, so the banner clears the next time a search works.
Since 19 September 2026 (SIRVIS 0.19.8, NERVIS 0.34.50).

## What it will not do

It does not delete a model on its own — not to make room for another, and not a failed partial
download. Only the Delete button does, and only to the Trash. It cannot cancel or pause a download either: LM Studio carries the
transfer and offers no way to stop it.

It does not load a model because somebody asked a question. Loading is leases
and the queue, owned in one place, because two things loading models at once on
one machine is how a benchmark measures swap instead of a model.

When it stops, it lets go of what it loaded (since 12 September 2026). Every
session is released and every model SIRVIS loaded — for the menu bar app, RAVIS,
the dashboard or a benchmark — is unloaded, so stopping the stack frees that
memory. A model somebody loaded in LM Studio by hand is left alone. If LM Studio
does not answer, SIRVIS waits at most six seconds for the unloads and its log
names what may still be loaded; the whole stop fits inside the twelve seconds
the launcher gives it. Before this, a stop left those models loaded and held by
nobody, and after a restart SIRVIS saw them as somebody else's.

It also keeps answering while it works. Loading a model and reading the
machine during a benchmark used to hold SIRVIS up for as long as they took, so
it looked down on the dashboard and in the menu bar in the middle of a load or a
run. Since 12 September 2026 that work happens off to the side and SIRVIS keeps
answering.

It does not report a score without its conditions, and it does not average a
counted metric — tool-call rates are counted rather than averaged, so they carry
no median and a reader asking for one is told that rather than given throughput.

It will not grow comparison analytics (the owner's decision, 18 September 2026). Comparing one model's
formats, Pareto charts, regression detection and historical comparison were struck rather than
postponed: they pay off only for someone who benchmarks local builds as a habit, and this stack mostly
routes to hosted models. The charts of what SIRVIS already measures stay. Later that day the owner
also struck everything else specified and not built — among it the Playground (NERVIS chat covers it),
a command line, extra Discover filters, and Compare, Inspect and Find variants on installed models.
