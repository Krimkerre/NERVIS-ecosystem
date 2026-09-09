/* What NERVIS says out loud about its own ecosystem, and when it says it.
 *
 * The spoken status line is the one surface nobody can scroll back to. It is
 * heard once, at the moment the dashboard opens, by somebody who is usually
 * looking at something else — so a sentence that was true three seconds ago is
 * not a smaller problem here than on screen, it is a bigger one.
 *
 * Two rules, both from the same report: "RAVIS still reports as unresponding
 * when the stack reboots, and recovers later."
 *
 *   - **Nothing is said while anything still has no reading.** The launcher
 *     starts five services at once and NERVIS is one of them, so its first
 *     sweep lands in the seconds before a peer's port is open. `discovering`
 *     is NERVIS's word for "no reading yet" and it cannot last: the probe loop
 *     settles every entry inside its startup window. Holding the line costs a
 *     few seconds of silence and buys a sentence that is true when heard.
 *   - **No count.** "All 4 services are up and well" invites the listener to
 *     check a number against what they think is running, and the number is the
 *     part most likely to disagree — a peer NERVIS was never told about is not
 *     in it, and the local runtimes are deliberately counted somewhere else.
 *
 *   node tools/voice_check.js
 */

const { loadPage } = require("./page_context.js");

const { exported } = loadPage();
const { SPEECH, unsettled } = exported;

const service = (key, label, state) => ({
  key, label, state, awaiting_first_contact: false,
});

const UP = [
  service("ravis", "RAVIS", "healthy"),
  service("sirvis", "SIRVIS", "healthy"),
  service("codeserver", "code-server", "healthy"),
];

const failures = [];

/* 1 · A cold start says nothing at all, and the same items said later do. */
const starting = [service("ravis", "RAVIS", "discovering"), ...UP.slice(1)];
if (!unsettled(starting)) {
  failures.push("a service with no reading yet did not count as unsettled, so "
    + "the welcome speaks a status about a peer nobody has managed to ask.");
}
if (unsettled(UP)) {
  failures.push("a fully-read registry counted as unsettled, so the line would "
    + "never be spoken at all.");
}

/* And the holding is `welcome()`'s own behaviour, not only a rule it could
   forget to consult. Driven rather than read: the failure this exists for is a
   line that *is* spoken during a restart, so the check has to be one that
   speaks. */
const spoken = [];
SPEECH.announcing = () => true;
SPEECH.whenAudioIsAllowed = (run) => run();
SPEECH.say = (text) => spoken.push(text);

SPEECH.welcome(starting);
if (spoken.length) {
  failures.push(`the welcome spoke while a service still had no reading: "${spoken[0]}"`);
}
if (SPEECH.welcomed) {
  failures.push("the welcome spent its one chance on a cold start, so the line "
    + "that would have been true is never said at all.");
}
SPEECH.welcome(UP);
if (!spoken.length) {
  failures.push("the welcome never spoke once every service had been read, so "
    + "holding it turned into silence.");
}

/* An entry still being discovered is never named as a fault: it is left out of
   the sentence entirely, for the paths that compose a line anyway. */
const said = SPEECH.welcomeLine(starting);
if (/RAVIS/.test(said)) {
  failures.push(`a service with no reading yet was named in the status line: "${said}"`);
}

/* 2 · No number, in any of the three shapes the line takes. */
const shapes = [
  SPEECH.welcomeLine(UP),
  SPEECH.welcomeLine([service("ravis", "RAVIS", "degraded"), ...UP.slice(1)]),
  SPEECH.welcomeLine([service("ravis", "RAVIS", "unreachable"), ...UP.slice(1)]),
];
for (const line of shapes) {
  if (/\d/.test(line)) {
    failures.push(`the status line quoted a count: "${line}"`);
  }
}

/* 3 · **What a transition is called depends on where it came from.** Reported
 * from the room: LM Studio was started for the first time in a session and the
 * dashboard said "LM Studio is back to healthy, sir" — a sentence that claims it
 * had been healthy, stopped being healthy, and recovered. It had never answered
 * at all. `discovering` is this page's word for "no reading yet", so a peer
 * leaving that state has connected rather than returned. */
const { spokenChange } = exported;
if (spokenChange("discovering", "healthy") !== "has connected") {
  failures.push('a peer answering for the first time was announced as "back to '
    + `healthy": ${JSON.stringify(spokenChange("discovering", "healthy"))}`);
}
if (spokenChange("unreachable", "healthy") !== "is back to healthy") {
  failures.push("a peer that really did recover lost its recovery wording: "
    + JSON.stringify(spokenChange("unreachable", "healthy")));
}
if (!/not answering|stopped/.test(spokenChange("healthy", "unreachable"))) {
  failures.push("a peer that stopped answering was not announced as such.");
}

/* 4 · Silence is not the fix — a service that is really down is still named. */
if (!/RAVIS is not answering/.test(shapes[2])) {
  failures.push(`an unreachable service was not named: "${shapes[2]}"`);
}
if (!/All services are up and well/.test(shapes[0])) {
  failures.push(`a healthy ecosystem did not read as one: "${shapes[0]}"`);
}

if (failures.length) {
  for (const failure of failures) console.error("  • " + failure);
  console.error(`${failures.length} spoken-status failure(s)`);
  process.exit(1);
}
console.log("the spoken status waits for a reading, names what is down, and counts nothing");
