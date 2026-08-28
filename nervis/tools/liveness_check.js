/* Does a card tell the truth about its own data?
 *
 * This gate exists because one defect was found eight separate times in a
 * single day, always by a person looking at the screen rather than by a check:
 * a card rendering real data from a running service, wearing a PROTOTYPE badge.
 * Providers had seven. The Sessions screen shipped with six, hours after the
 * first batch was fixed. "Evidence in the ranking" had thirty-two live rows
 * under a faded card.
 *
 * The cause is structural rather than careless. Liveness is opt-in per card and
 * was expressed ten different ways — `fromSirvis`, `p.live`, `ev.source`,
 * `j.source`, `tr.live`, `spend.live`, `s.live`, `ev.live`, `live`, `real` — so
 * every new card is a fresh chance to forget, among ten idioms to forget
 * between. `render_check.js` proves a screen does not crash; nothing proved a
 * screen was honest.
 *
 * **A ratchet, not a standard.** Sixty-three cards hardcode their class today
 * and many of them are right to: an empty state, a reference table, a card
 * about something unbuilt. Demanding all of them change at once would mean
 * marking cards live to satisfy a tool, which is the failure this is meant to
 * prevent, pointed the other way. So the count may fall and may not rise, the
 * same shape as the complexity ratchet next door.
 */
const fs = require("fs");
const path = require("path");

const FILE = path.join(__dirname, "..", "index.html");

/* The ceiling. Lower it when cards are wired; never raise it without saying
 * why in the commit message. */
const CEILING = 63;

const source = fs.readFileSync(FILE, "utf8");

/* A card that opens with a literal class attribute cannot express liveness at
 * all — whatever its data does, it renders the same. A card whose class carries
 * a `${...}` has at least been asked the question. */
const hardcoded = source.match(/<div class="card [a-z ]+"><h3>/g) || [];
const derived = source.match(/<div class="card [a-z]*\$\{/g) || [];

/* Which titles, so a reviewer can see what is still fixed rather than a bare
 * number. Titles are how a person finds the card on screen. */
const titles = (source.match(/<div class="card [a-z ]+"><h3>([^<]{0,40})/g) || [])
  .map((match) => match.replace(/^.*<h3>/, "").trim())
  .filter(Boolean);

const unique = [...new Set(titles)].sort();

if (hardcoded.length > CEILING) {
  console.error(
    `${hardcoded.length} cards hardcode their class, above the ceiling of ${CEILING}.\n`
  );
  console.error("A card that cannot express liveness renders a live service's");
  console.error("data under a PROTOTYPE badge — the defect this gate exists for.");
  console.error("Key the class off whatever the screen already knows:\n");
  console.error('  <div class="card full${x.live?\' live\':\'\'}">\n');
  console.error("If the card is genuinely static — an empty state, a reference");
  console.error("table, something unbuilt — leave it and lower nothing.\n");
  console.error(`Newest titles include: ${unique.slice(0, 6).join(", ")}`);
  process.exit(1);
}

if (hardcoded.length < CEILING) {
  console.log(
    `${hardcoded.length} cards hardcode their class — below the ceiling of ` +
      `${CEILING}. Lower CEILING in ${path.basename(__filename)} to ${hardcoded.length}.`
  );
  process.exit(1);
}

console.log(
  `${hardcoded.length} hardcoded, ${derived.length} deriving liveness — at the ceiling.`
);
