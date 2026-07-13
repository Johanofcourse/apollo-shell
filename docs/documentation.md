# Apollo Shell — where did this thing wander off to?

Started as a "shell." Somewhere along the way it became an outage
detective instead. No regrets.

## Done
- FPL outages and NWS weather alerts both get fetched *and saved* now
  (alerts used to be parsed and thrown straight in the trash)
- Correlation logic (`apollo_shell/correlate.py`): outage + weather
  alert sharing a county and a time window get matched up
- A 15-minute poller (`main.py`, "Apollo Sentinel") running live in
  the background via launchd, unattended, overnight, screen dark
- Outage lifecycle tracking (`outage_events`): real start/end times,
  not a single frozen snapshot
- A local live-status dashboard (`dashboard.py`, Flask, localhost
  only) — open outages, resolved durations, weather, correlation
  matches, at a glance
- **Real historical storm data**, not just live-forward collection —
  two hurricanes imported end to end, across every utility in the
  affected counties, real multi-day durations. Kept off GitHub.
- A second, independent public source layered on top — reported wind
  severity per county, matched against our own outage numbers.
  Confirms the obvious in FPL's core territory, and taught us where it
  *doesn't* hold: storm-wide weather vs. one utility's numbers is
  misleading in counties where that utility barely operates.
- A real idempotency bug, found and fixed: re-running a historical
  import used to silently duplicate every event — including inflating
  a real 7-day blackout into two fake ones. Guarded at the database
  level now, not just "remember not to do that."

## The plot twist
Same night, different rabbit hole: went looking at whether other
Florida utilities' outage maps had richer live data than FPL's — one
of them did, dramatically so. After some digging into how its live map
actually works, found that it hands over, per outage incident: real
coordinates, a stated *cause* ("tree limb," "equipment damage,"
genuinely once "squirrel"-adjacent), live crew status, and — the thing
this project had already concluded didn't exist anywhere — a real
per-incident restoration estimate, live.

Built as its own module, same one-file-per-source pattern, with its
own lifecycle tracking (simpler than FPL's — this source hands us a
real incident ID) and its own bridge into weather correlation
(location reverse-mapped to a county, since this source only gives
coordinates).

Cleaned up two things before they became real problems: this utility
already existed in the historical data under a different name (now
reconciled to one canonical name); and its free-text cause/status
fields got a derived category alongside the original text, never
replacing it, so "squirrel in tree" stays exactly as written *and*
becomes something filterable.

Also found a real deployment bug and double-checked a timezone
assumption that turned out to already be correct — good reminders that
"it works" and "it's been checked" aren't the same sentence.

## Where the historical data actually comes from
Deliberately vague on specifics here. Broad strokes: one public source
publishes after-action situation reports during declared storm
emergencies, county-by-county, per utility, with a real
restoration-time field FPL's live feed doesn't have. A second, separate
public archive covers historical storm/weather events with narrative
detail, occasionally including wind speeds. Both took real digging —
and ruling out a few dead-end or fabricated sources — to confirm as
real and usable.

## Live right now
- The poller runs unattended from three independent sources now,
  `outages.db` growing on its own
- Real weather alerts are correlating with real (small) outages — the
  mechanism works, just waiting on a real storm to test it against
  something dramatic
- The new incident-level source is the most interesting thing running
  — first live data with an actual restoration-time field, still just
  a handful of minor incidents so far

## The honest gaps
- Guessing when the lights come back on for a *live* FPL outage? Still
  no — FPL's feed hasn't changed. A second utility's feed does carry a
  restoration estimate, but it's undocumented and could change or
  break without warning — an asterisk FPL's boring, stable feed never
  needed.
- *Historical* storms improved a lot though: real multi-day,
  multi-utility restoration data for two hurricanes, plus verified
  severity context. Two storms is still a small sample, not yet
  something to average.
- Plan: keep working backwards through more past storms, one at a
  time, each independently verified (every storm so far has had at
  least one real data-quality surprise once checked).
- The original plan — AI input parser, command history, an actual
  interactive shell — is still benched. "Map power outages against
  weather" turned out to be the more interesting rabbit hole.

## End of a long one
Real late-night session. Went from re-explaining the correlation logic
to reverse-engineering a live utility feed, fixing half a dozen real
bugs along the way that would've quietly poisoned the data. Nothing
dramatic weather-wise that night — small potatoes, no real storm to
test any of it against yet. Fine — the point wasn't finding a storm,
it was making sure the thing is ready to trust when one shows up.
Poller's still out there, doing its quiet thing.

## The 2018-2025 backfill, and the confidence label
A later session, all business: backfilled every remaining storm PSC
has a report for, back to Alberto in May 2018 — 17 storms total, every
utility per storm, each independently checked (the utility-name
mismatch turned out to be a recurring bug, not a one-off — same fix
needed for FPL, TECO, and Duke in turn). A cross-storm sweep afterward
caught the last stragglers without needing to know Florida utility
geography by heart.

Also shipped a real weather-match confidence label
(`weather_match_confidence()`) — event-type plausibility drives it
first (a "Severe" Rip Current Statement should never outrank a
"Moderate" Tornado Warning), severity only nudges it within an
already-plausible type. High/medium/low, not a percentage, and shipped
straight into the dashboard with confidence bars, severity badges, and
a KPI strip.

## The design detour
A different kind of session: less "fix a bug," more "what should this
look like." Used Claude's Artifact tool as a disconnected sandbox —
never touching the real dashboard or live data — to explore a visual
language first.

Pivoted partway through, from bright and bold to something closer to
Swiss wayfinding instrumentation — flat, typographic, dark mode,
signal colors doing double duty as status lamps. Added a telemetry
sidebar, a plain-English confidence explainer, and expanded the county
log to all 22 tracked counties, worst-verdict-first.

The map was its own saga: a gauge (cut, too empty), a simplified
region grid (too blocky), two hand-traced attempts at real county
shapes (both visibly off). The actual fix was pulling real US Census
county boundary data and projecting/simplifying it with code instead
of by eye — which is also how Monroe's real Keys islands and a few
other counties' barrier islands showed up correctly for the first
time, because they're just actually in the data. Ended on a flat,
north-up map with a subtle raised-slab shadow and a grid backdrop.

Explicit design goal going forward: intuitive for someone with no
assumed familiarity with web-app conventions, not designed around
tech-savvy habits (hover-only reveals, icon-only controls, jargon).
Nothing from this session is in the live app yet — porting the
settled design into the real dashboard is the deliberate next step.

## An old quirk, revisited: Elsa's storm-history recaps
A much earlier bug got a proper look back: NOAA's narrative text for
Hurricane Elsa sometimes restated the storm's own historical peak wind
(from days earlier, out in the Caribbean) in the same paragraph as the
real local reading for whatever county the record actually described -
and the original wind extractor, which just grabbed the biggest number
in the text, couldn't tell the two apart. Checking directly against the
data: this touched 21 records across 10 counties (Pinellas hit hardest,
plus Charlotte, Sarasota, Hillsborough, Polk, Manatee, Hernando, Lee,
Levy, Citrus) - but only in Elsa's dataset. None of the other 16
storms have this pattern at all. A quirk of how that one storm's
narratives happened to get written, not a systemic problem across the
whole historical archive.

## The Miami-Dade saga
Started with a simple ask - let people query a county's real storm
history - and turned into the best bug hunt this project's had. Built a
consolidated historical database and a real query page, then noticed
Miami-Dade had zero records anywhere while its neighbors had plenty.
Turned out to be real: every county-matching regex in the PDF parser
was missing a hyphen in its character class, and Miami-Dade is the only
Florida county with one in its name - every single Miami-Dade row, in
every report, in all 17 storms, had been silently skipped since the
very first backfill.

Fixing it surfaced a second, worse bug: replaying a historical report
series a second time on top of data that's already there doesn't just
skip duplicates like everything else in this project - it fabricates
extra fake outages, because the lifecycle tracker decides "is this
currently open" by asking the live database, which only makes sense
moving forward in time. Re-ran all 17 storms clean from scratch to fix
it properly, re-applying a data-entry-error correction that got
temporarily undone along the way. Miami-Dade now has real data in 12 of
17 storms, confirmed genuinely absent in the other 5.

Also shipped: a real pipeline error alert on the dashboard (every caught
fetch failure used to just be a print() line nobody watched), and,
after asking "what else might be lurking," a couple more small real
fixes (a NULL-id gap in weather alerts, a sloppy regex in the ice
detector) plus the thing this project never had before - an actual
integrity-check script and a real pytest suite, including a test that
reproduces the replay bug itself and proves the fix actually holds.

## Heat gets its own moment
A simple question - "do we track heat advisories?" - turned out to have
a one-word answer (yes, already, for free) and a more interesting
follow-up. Heat Advisory and Excessive Heat Warning are just ordinary
NWS alert types, already flowing through the same `weather_alerts` table
as every flood/wind/tornado alert since day one. Checking the real
numbers: 20 Heat Advisory alerts logged since July 4th, covering 6 of
the first 9 days of July somewhere in Florida - zero Excessive Heat
Warnings yet. The data was never missing, just never surfaced as its
own thing.

Shipped a small "heat this month" strip on the internal dashboard -
days-with-advisory count, a tier breakdown, and a live "active now"
badge - as the first, smallest piece of a bigger idea the user's after:
eventually a genuinely public-facing heat advisory view (a much lower-
risk thing to publish than the outage data, since it's just re-surfacing
NWS's own already-public alerts), and further out, a natural-language
query interface over the historical data - a "dumb AI" in the user's
own words, narrow and task-scoped on purpose, likely backed by a cheap
model like DeepSeek rather than anything general-purpose. Both are real
ideas now on the roadmap (Phase 4), neither started.

Two quick follow-ups once the strip was actually in front of a real
person: "85 zones" meant nothing without a way to see which ones, so
that count is now a link to a small `/heat` page listing the actual
NWS forecast zones under an active advisory, plus a plain-language note
on what a "zone" even is (finer-grained than a county - "Coastal
Broward County" and "Inland Broward County" are two separate zones).
And every raw ISO timestamp on the dashboard and history pages, the
kind with a `T` and six decimal places nobody asked for, now renders as
actual prose ("July 2, 2026, 1:19 AM") through one shared Jinja filter.

## The isometric map, and a fourth utility in one night
Two more things landed the same night. First, the county map got a real
isometric pass in the design sandbox - the same 40-degree rotated
version that got cut months earlier came back, done properly this time:
a true 30-degree projection on the real Census county data already in
place, each county given a modest real extrusion (height tied to its
verdict severity, not just decoration), depth-sorted so nearer shapes
draw over farther ones correctly. "Some meat, nothing too intense" was
the brief, and that's what it stayed - a relief map, not a skyline.

Second, and bigger: JEA joined FPL, TECO, and Duke as a fourth live
utility, found and built start to finish in one sitting. JEA's outage
map turned out to run on a completely different vendor platform than
TECO or Duke - not something either of their integrations could be
copy-pasted from. Finding it meant downloading the outage map's own JS
bundles and reading through the minified source for the real API routes,
since there was no live browser/devtools tool available this session -
corrected two wrong guesses along the way (a plural/singular typo in one
route, a wrong deployment-id field in another) before the real chain
resolved cleanly: a "current state" call, a "configuration" call, then
the actual live report.

That report turned out to be genuinely richer in one respect than
anything already in this project - a labeled confidence
(`etr_confidence`) on JEA's own restoration-time estimate, not just the
estimate itself. It also reports by ZIP code, not county, which every
other source here uses - resolved by reverse-geocoding each ZIP's own
bounding-box center through the same FCC lookup TECO's incidents already
use, no separate ZIP-to-county dataset needed, cached once per process
since JEA's ~38 ZIPs don't move between polls. JEA got its own dedicated
tables rather than sharing FPL's, on purpose: FPL's dashboard section
and correlation both read without a utility filter, so sharing would
have silently mixed JEA's numbers into "FPL"'s.

## A public-facing Storm History, and the same lesson twice
Picked up the artifact's design sandbox again to sketch what a genuinely
public-facing page could look like, starting from the one piece already
clear of Phase 4's gates: storm history. Landed on a real question to
build around - "if a storm like this hits, what happens to my area?" -
which meant translating the internal tool's language (peak percentage
out, ETR, "NOAA Storm Events") into plain sentences, and treating the
17 storms as the real chronological timeline they are instead of a
table.

The one design note worth keeping: "no report for this storm" has to
keep meaning "we don't have a record of it," never accidentally read
as "this county wasn't affected" - the exact distinction the Miami-Dade
bug hunt existed to establish in the first place. Built the mockup to
show all 17 storms for every county, honest gaps included, with real
data (Miami-Dade: 12 of 17, matching the known number) rather than
placeholder content, and wired county selection two ways - clicking the
isometric map directly, or a plain search box - since precisely tapping
a tiny county shape on a touchscreen is its own real problem.

Then the same lesson got applied a second time, somewhere closer to
home: the actual internal `/history` route had the identical blur - it
only ever listed storms with real data for a county, silently leaving
the rest out, so "12 of 17" was never actually visible, just 12 cards
with no mention of the other 5. Fixed for real this time, logic only -
the internal tool stays plain and data-dense on purpose, that's not a
gap to close, just a different job than the public-facing mockup. Every
county's page now lists all 17 storms, honest "no report" rows and all,
verified against the same real numbers (Miami-Dade 12/17, Duval 13/17).

## Why the dashboard got slow
A plain complaint - "dashboard is taking a while to load" - turned into
a real, measured diagnosis rather than a guess. Every page load was
silently recomputing all four correlation functions from scratch, each
one a plain-Python nested loop over its entire raw history against
every weather alert - fine when the tables were small, not fine once
`outages`/`teco_incidents`/`duke_incidents` had each grown into the
tens of thousands of rows from weeks of 15-minute polling (a fresh row
every cycle, forever, whether or not anything actually changed). Timed
it directly instead of assuming: about 34 seconds combined, on every
single load.

The underlying fix wasn't to make the matching faster - it was to
notice the data behind it only actually changes once every 15 minutes,
while the page itself auto-refreshes every 60 seconds. A short-lived
cache made almost every reload reuse an answer that genuinely hadn't
changed yet: cold load still ~34s, everything after that within five
minutes, about 0.05s. Deliberately the smaller fix over rewriting the
matching in SQL - that would solve the same problem more thoroughly,
but risks quietly changing the matching logic's actual behavior in the
process, and this got the real problem (unusable load times) solved
without that risk.

## Two incident IDs, decoded, one of them worth translating
A passing question about why Duke's incidents look like `20260712000423`
and TECO's look like `A202619308291` turned into an actual investigation
rather than a shrug. Checked both against real data instead of guessing:
Duke's really is `YYYYMMDD` plus a per-day counter - an incident first
seen on July 3rd genuinely starts with `20260703`, one from July 12th
starts with `20260712`, confirmed directly. TECO's isn't a date at all -
tracked how much the number grew over ten real days (about 100,000) and
it's clearly some enterprise-wide ticket counter running far faster than
actual outages could ever produce, not anything specific to power.

Only one of those was worth acting on: Duke's dashboard now shows
`Incident #423` instead of the full digit string, since the date part
was always redundant with the row's own "Started" column right next to
it. TECO's stays exactly as sent - there's nothing real underneath it
to translate, and showing a fake decoded label would be worse than
showing an honest opaque one.

## Checking on one outage after it's over
A natural next question once you can see incident IDs clearly: can you
actually look one up? Turned into a real, small feature - click any row
in a "Recently Resolved" table and land on that one incident's own page.

The interesting part was that "one outage" means something different
depending on the source. TECO and Duke hand us a real incident id, and
because both quietly re-log a fresh snapshot every single poll cycle
while something's active, a specific incident already had a whole real
timeline sitting in the data - status changes, cause, customer count,
ETR, all with real timestamps - nobody had ever built a page to actually
show it. FPL and JEA don't have that; neither has ever given us a
discrete incident identity, only a county-level number. So "one outage"
there means one specific county occurrence, told apart from any other
time that same county had trouble by exactly when it started - which,
conveniently, is already how the database tells them apart internally.

Worth remembering: this is the same raw material the long-deferred
restoration-confidence idea (Phase 3) would eventually need to learn
from - not that model itself, which is still waiting on a lot more
resolved-incident history to accumulate, just the part where you can
finally go look at one incident's whole story by hand.

## A dashboard row that didn't add up
Asked to explain a single row - Nassau, 1244 correlated outages, Rip
Current Statement x280 - and answering it honestly meant actually
reading the query behind it instead of describing what the label
implied. What it turned up: `find_correlations()` was matching *every*
raw poll snapshot against active weather alerts, with no check for
whether an outage was actually happening. FPL and JEA both log a fresh
row every 15 minutes for every county/ZIP regardless of whether
anything was wrong, so "a heat advisory happened to be active while
nothing was actually wrong" was quietly counting as a correlated
outage, same as a real one.

Checked how much it actually mattered before touching anything: FPL's
match count dropped 59% once filtered to real outages only (18,151 ->
7,495), JEA's dropped 84% (596 -> 97) - worse proportionally, since its
ZIP-level polling logs even more "nothing happening" snapshots per real
outage. TECO and Duke turned out to have never had this problem at all
- their feeds only ever report incidents that are actually open, so
there was never a zero-customer row to leak in in the first place.
Fixed with one line each (`WHERE customers_out > 0`), which also nearly
halved FPL's correlation compute time as a free side effect.

The bigger question raised alongside this one didn't stay open for
long, though - asked to explain one more row a little later the same
night ("Broward: Air Quality Alert x190... is that a day, a month,
since we started tracking?"), and this time the honest answer was
"good question, let's actually fix that" instead of just explaining the
mechanism.

Two things were tangled up in that one number, so both got fixed at
once. First, no time bound at all - these counts had been all-time
since the poller first started back in April, silently growing less
meaningful by the day. Second, even bounded, the count was still wrong
at the unit level: it counted every *(outage snapshot, alert) pair* a
15-minute poll cycle happened to overlap, not anything close to "190
alerts." A single Air Quality Alert lasting a day and a half, checked
every 15 minutes, racks up well over a hundred matches against the same
one real event.

The fix: a real `days=` window on every correlation query (a toggle
between 7 and 30 days now sits right on the dashboard, defaulting to
30), and the alert tally switched from counting matches to counting
*distinct alerts* - NWS's own alert id, de-duplicated. Same for the
outage side. Broward's "x190" became something closer to a small,
believable number of actual distinct events - the kind of thing someone
could read out loud and have it mean something.

Then, completely unrelated - asked to make the new toggle buttons a bit
"more bubbly," rounder, with real depth - and while checking that the
color swap hadn't broken anything, the exact same bug turned up a
second time, in a different spot. The combined statewide confidence
strip at the top of the page still read "low x27118," even after
everything else got fixed. Confidence turned out to be its own little
loose end: it's purely a property of the alert itself, not of which
outage it happened to overlap, so it needed the identical
one-distinct-alert-not-one-per-match treatment the rest of the fix
already got - just living in a second function nobody had touched yet.
Fixed the same way, reusing the same dedup logic instead of writing it
twice. "low x27118" is now "high x73, medium x222, low x75."

The buttons themselves ended up pink, for a much less technical reason:
the original orange-on-black combination looked a little too much like
a certain other well-known site's branding. Swapped it for the same
pink already living in the Artifact's design language - kept the
orange exactly where it still means something (the severity badges,
the confidence bar's low segment), and moved on.

## The first real shape of a public page
A bigger question arrived next: what would it actually look like to
ship this for real, not just as an internal tool? Answering it honestly
meant admitting the existing design sandbox quietly mixed two different
things - a heat panel and storm history section that were already clear
to publish, sitting right next to a live per-county verdict map whose
publish-safety had never actually been decided, just assumed. Said so
plainly before building anything further.

The map question resolved fast, and in the opposite direction expected:
keep it, live verdict and all. The reasoning was refreshingly direct -
a sufficiently attentive visitor might work out that the site pulls
from several utilities' outage maps, and that's fine, because the
information itself is genuinely useful to the person looking at it.
Not every risk is worth avoiding at the cost of the thing actually
being useful. The internal-only ops telemetry - utility breakdowns,
confidence-score bars, top alert types - didn't get the same reprieve,
and rightly so: that's monitoring language for someone running the
system, not information a resident showed up looking for.

What came out of it is a genuinely new artifact, not an update to the
old design sandbox - a real pivot, so it earned its own URL. Same
isometric map, same real Census county data, same heat panel, same
storm history timeline, restructured as an actual front page: a plain-
language lede explaining what the site is, the map and two live KPIs up
top, heat and storm history given full-width room to breathe instead of
living as sidebar widgets, and a footer that says outright what the
page does and doesn't show - a derived read on weather and outages, not
a live feed replay, and not a substitute for checking your own
utility's outage map. Nothing here is live in the real app yet - still
a concept, still waiting on an actual test/production environment that
doesn't exist yet either.

## The Panhandle hunt, and a bug that needed catching twice
One more section landed in the public concept that night - Current
Weather Alerts, showing every active NWS alert statewide, not just
heat. Real and small: two alerts active at the time, one of them a rip
current statement for the Panhandle coast, which turned out to matter
more than expected.

Because the real project that night was finding out who actually serves
power to that missing corner of the state. A real lead turned up fast -
FPL runs a whole separate regional map for the Panhandle, distinct
JS bundle and everything, clearly built around exactly that geography.
Getting past it wasn't so fast - real bot protection (Incapsula) sits in
front of the actual data, and it held. Confirmed that much for certain
by tripping the same wall on our own already-working FPL integration
with the wrong headers on purpose - so this isn't a guess about what's
blocking things, it's a checked fact. Worth another pass with fresh
eyes, not a dead end.

Before chasing a whole separate system, checked the obvious alternative
first: maybe the Panhandle was sitting in data we already have, quietly
dropped by our own code the way Miami-Dade once was. It wasn't - the
main feed genuinely never mentions those counties at all. Good to rule
out before building anything.

The county-gap count itself needed fixing twice in the same sitting.
First "St Lucie" (no period, straight from FPL's own feed) got flagged
as missing against a canonical "ST. LUCIE" - not a real gap, just
punctuation. Fixed that, then found the exact same shape of mistake
again five minutes later: "De Soto," two words, in the real feed,
flagged as missing against "DESOTO." Both are the same lesson the
original Miami-Dade bug already taught this project once - a spelling
mismatch reads exactly like a real gap until you check the raw source
directly. The real, final count: ten counties missing, every one of
them Panhandle, no outliers left once both mistakes were caught.

The wall held for a night, then came down the next time it got looked
at. Same page, same Incapsula protection - the difference was filtering
the browser's network log by size the *right* direction. Sorting for
the biggest transfers had buried the actual answer under a 3MB jQuery
file; the real payload was a tiny 7KB JSON response, easy to miss
looking the wrong way, easy to spot once someone thought to look for
small instead of big. `fplmaps.com/northwest/feeds/CountyOutages.json` -
same shape as the feed this project already knows how to read, just a
different address and a different page to claim as the referer.

Turns out this isn't really a fifth utility at all, just a second door
into the same one - Gulf Power became FPL on paper back in 2021, and
whoever maintains fplmaps.com never got around to merging the two maps.
So it went in the way that actually matched reality: folded straight
into the existing FPL pipeline, no new tables, no new correlation
function, nothing TECO/Duke/JEA each needed built for them from
scratch. Fetch both feeds, combine the results, treat it as one
utility, because it genuinely is one. Closed eight of the ten missing
counties outright. Three remain - Calhoun, Gadsden, Liberty - probably
someone smaller, still unfound, still an honest gap instead of a
guessed-away one.
