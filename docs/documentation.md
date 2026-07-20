# Apollo Shell — where did this thing wander off to?

Started as a "shell." Somewhere along the way it became an outage
detective instead. No regrets.

## Done
- Live outage data flows in continuously from seventeen independent
  Florida utilities, alongside National Weather Service alerts, now
  covering every one of Florida's 67 counties with a real, verified
  live source
- Correlation logic: outages and weather alerts sharing a county and a
  time window get matched up, each match carrying its own confidence
  label
- A 15-minute poller ("Apollo Sentinel") running unattended, day and
  night, since April
- Outage lifecycle tracking: real start/end times per incident, not
  just frozen snapshots
- A local live-status dashboard — open outages, resolved durations,
  weather, correlation matches, and the health of every data source, at
  a glance
- A live per-county lookup tool, pulling together outages, weather, and
  heat advisories for any of Florida's 67 counties
- A genuinely separate public-facing page - its own app, its own real
  isometric Florida county map (opens on current Live Severity by
  default, one click over to the all-time Historical Pattern view),
  plus a narrative summary, a per-county Outage History (real start/end
  times for individual outages this project has directly observed,
  distinct from the independently-sourced storm archive), a bold
  hazard-placard visual identity (custom SVG iconography, stenciled
  display type, diagonal-cut framing), running on a real always-on
  server now, wired to the same live data as everything else
- **Real historical storm data**, not just live-forward collection — 17
  storms, 2018-2025, across every utility per storm, queryable by
  county. Kept off GitHub.
- A second, independent public source layered on top of the historical
  data — reported wind severity per county, cross-checked against our
  own outage numbers.
- A year-round heat-advisory tracker, plus a drill-down into which
  zones are currently under one
- A real idempotency bug, found early on and fixed for good: re-running
  a historical import used to silently duplicate every event. Guarded
  at the database level now, not just "remember not to do that."
- Real restoration-time guidance, honestly scoped to what each utility
  actually supports rather than one invented model for everyone: FPL
  gets a historical-precedent pair (major storms vs. everyday outages,
  further split by real wind severity, kept as separate numbers on
  purpose, never blended), TECO and LWBU each get an accuracy check on
  their own existing restoration estimate instead, since they already
  have one FPL doesn't, and Duke and JEA get the simpler shape - a
  plain duration precedent, real and individually-tracked, with no
  estimate of their own to check. TECO, FPUC, and LWBU's live open
  outages also show their own restoration estimate inline, not just in
  the aggregate accuracy numbers
- Street-to-county resolution for the four utilities that only ever
  report a combined multi-county total: real street names geocoded
  against each utility's own known territory, cached forever, so a
  live outage can now surface which of its counties are actually
  affected
- Outage History pages, and the public map on narrow screens
- A real CI pipeline (GitHub Actions) running the full test suite on
  every push, so that specific check can't quietly get skipped
- A seventeenth utility, added the honest way - ranked by real
  footprint in this project's own historical storm archive rather than
  guessed at, with real native per-county granularity across 15
  counties, no combined-territory blur at all

## The plot twist (July 2, 2026)
Same night, different rabbit hole: went looking at whether other
Florida utilities' own public information had richer live data than
FPL's — one of them did, dramatically so. It hands over, per outage incident: real
coordinates, a stated *cause* ("tree limb," "equipment damage,"
genuinely once "squirrel"-adjacent), live crew status, and — the thing
this project had already concluded didn't exist anywhere — a real
per-incident restoration estimate, live.

Built as its own module, with its own lifecycle tracking and its own
bridge into weather correlation.

Cleaned up two things before they became real problems: this utility
already existed in the historical data under a different name (now
reconciled to one canonical name); and its free-text cause/status
fields got a derived category alongside the original text, never
replacing it, so "squirrel in tree" stays exactly as written *and*
becomes something filterable.

Also found a real deployment bug and double-checked a timezone
assumption that turned out to already be correct — good reminders that
"it works" and "it's been checked" aren't the same sentence.

## Where the historical data actually comes from (July 2, 2026)
Deliberately vague on specifics here. Broad strokes: one public source
publishes after-action situation reports during declared storm
emergencies, county-by-county, per utility, with a real
restoration-time field FPL's live feed doesn't have. A second, separate
public archive covers historical storm/weather events with narrative
detail, occasionally including wind speeds. Both took real digging —
and ruling out a few dead-end or fabricated sources — to confirm as
real and usable.

## Live right now
- The poller runs unattended from seventeen independent Florida
  utilities now, up from the original one, with a real live source
  covering every one of Florida's 67 counties
- Real weather alerts are correlating with real outages statewide, each
  match carrying its own confidence label
- A real per-county lookup tool now pulls outages, weather, and heat
  together for whichever county someone's actually asking about
- The public page's own per-county view goes further: current status,
  active alerts, and a real itemized outage history (individual
  start/end occurrences, not just a summary number) for whichever
  county gets clicked or searched
- 17 historical storms are queryable by county, and every collection
  source's health is tracked so a quiet one gets noticed, not assumed
- The public page's own expensive all-time correlation read is
  precomputed once per poll cycle now, not recalculated per visitor -
  a real load-time bug (up to 44s) fixed at the source, not papered
  over with a longer cache
- A real FPL outage open right now gets two honest restoration numbers
  on its county's page (major-storm precedent, split by real wind
  severity where enough data exists; everyday-outage precedent); a real
  TECO or LWBU outage gets an accuracy read on that utility's own
  stated estimate instead, plus its live per-incident ETR shown inline;
  a real Duke or JEA outage gets a plain duration precedent - all only
  appear when actually relevant, never as a standalone historical
  curiosity
- TCEC/EREC/CHELCO/GCEC's live outages now resolve to real affected
  counties from their raw street lists, geocoded and cached rather than
  left as one blended multi-county total
- Outage History no longer hard-stops at 15 rows - real Prev/Next
  pagination - and the public page's map and long explainer text
  actually work on a phone screen now, not just desktop

## The honest gaps
- Real restoration guidance now covers every utility with enough real
  data to support it - FPL, TECO, Duke, JEA, and LWBU, each in the
  shape its own feed actually supports rather than one model forced
  onto all five
- FPUC's live feed still reports one combined total across its five
  non-adjacent counties, no per-county split - a real, unresolved
  limitation of the source itself, not something this project's code
  can parse around
- *Historical* storms are the strong suit: 17 of them, 2018-2025, real
  multi-day restoration data, cross-checked severity context
- The original plan — AI input parser, command history, an actual
  interactive shell — is still benched. "Map power outages against
  weather" turned out to be the more interesting rabbit hole.

## End of a long one (July 2-3, 2026)
Real late-night session. Went from re-explaining the correlation logic
to digging into a new utility's live feed, fixing half a dozen real
bugs along the way that would've quietly poisoned the data. Nothing
dramatic weather-wise that night — small potatoes, no real storm to
test any of it against yet. Fine — the point wasn't finding a storm,
it was making sure the thing is ready to trust when one shows up.

## The 2018-2025 backfill, and the confidence label (July 4, 2026)
A later session, all business: backfilled every remaining storm on
record back to May 2018 — 17 storms total, every utility per storm,
each independently checked (a utility-name mismatch turned out to be a
recurring bug, not a one-off). A cross-storm sweep afterward caught the
last stragglers without needing to know Florida utility geography by
heart.

Also shipped a real weather-match confidence label — event-type
plausibility drives it first (a "Severe" Rip Current Statement should
never outrank a "Moderate" Tornado Warning), severity only nudges it
within an already-plausible type. High/medium/low, not a percentage,
shipped straight into the dashboard with confidence bars, severity
badges, and a KPI strip.

## The design detour (July 4-5, 2026)
A different kind of session: less "fix a bug," more "what should this
look like." Used an isolated design sandbox — never touching the real
dashboard or live data — to explore a visual language first.

Pivoted partway through, from bright and bold to something closer to
Swiss wayfinding instrumentation — flat, typographic, dark mode,
signal colors doing double duty as status lamps. Added a telemetry
sidebar, a plain-English confidence explainer, and expanded the county
log to all 22 tracked counties, worst-verdict-first.

The map was its own saga: a gauge (cut, too empty), a simplified
region grid (too blocky), two hand-traced attempts at real county
shapes (both visibly off). The actual fix was pulling real public
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

## An old quirk, revisited: Elsa's storm-history recaps (July 5, 2026)
A much earlier bug got a proper look back: the narrative text for
Hurricane Elsa sometimes restated the storm's own historical peak wind
(from days earlier, out in the Caribbean) in the same paragraph as the
real local reading for whatever county the record actually described -
and the original wind extractor, which just grabbed the biggest number
in the text, couldn't tell the two apart. Checking directly against the
data: this touched 21 records across 10 counties (Pinellas hit hardest,
plus nine others) - but only in Elsa's dataset. None of the other 16
storms have this pattern at all. A quirk of how that one storm's
narratives happened to get written, not a systemic problem across the
whole historical archive.

## The Miami-Dade saga (July 8, 2026)
Started with a simple ask - let people query a county's real storm
history - and turned into the best bug hunt this project's had. Built a
consolidated historical database and a real query page, then noticed
Miami-Dade had zero records anywhere while its neighbors had plenty.
Turned out to be real: every county-matching pattern in the report
parser was missing one punctuation character, and Miami-Dade is the
only Florida county with that character in its name - every single
Miami-Dade row, in every report, in all 17 storms, had been silently
skipped since the very first backfill.

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
fetch failure used to just be a line in a log file nobody watched), and,
after asking "what else might be lurking," a couple more small real
fixes plus the thing this project never had before - an actual
integrity-check script and a real automated test suite, including a
test that reproduces the replay bug itself and proves the fix actually
holds.

## Heat gets its own moment (July 9, 2026)
A simple question - "do we track heat advisories?" - turned out to have
a one-word answer (yes, already, for free) and a more interesting
follow-up. Heat Advisory and Excessive Heat Warning are just ordinary
weather-alert types, already flowing through the same table as every
flood/wind/tornado alert since day one. Checking the real numbers: 20
Heat Advisory alerts logged since July 4th, covering 6 of the first 9
days of July somewhere in Florida - zero Excessive Heat Warnings yet.
The data was never missing, just never surfaced as its own thing.

Shipped a small "heat this month" strip on the internal dashboard -
days-with-advisory count, a tier breakdown, and a live "active now"
badge - as the first, smallest piece of a bigger idea: eventually a
genuinely public-facing heat advisory view (a much lower-risk thing to
publish than the outage data, since it's just re-surfacing already-
public weather alerts), and further out, a natural-language query
interface over the historical data - narrow and task-scoped on
purpose. Both are real ideas now on the roadmap, neither started.

Two quick follow-ups once the strip was actually in front of a real
person: "85 zones" meant nothing without a way to see which ones, so
that count is now a link to a small page listing the actual forecast
zones under an active advisory, plus a plain-language note on what a
"zone" even is (finer-grained than a county). And every raw machine
timestamp on the dashboard and history pages now renders as actual
prose ("July 2, 2026, 1:19 AM") instead.

## The isometric map, and a fourth utility in one night (July 9, 2026)
Two more things landed the same night. First, the county map got a real
isometric pass in the design sandbox - a true angled projection on the
real county boundary data already in place, each county given a modest
real extrusion (height tied to its verdict severity, not just
decoration), depth-sorted so nearer shapes draw over farther ones
correctly. "Some meat, nothing too intense" was the brief, and that's
what it stayed - a relief map, not a skyline.

Second, and bigger: JEA joined the live utilities as a fourth source,
found and built start to finish in one sitting. JEA's outage map ran on
a completely different backend than the others - nothing could be
copied over directly. A few wrong turns along the way before the real
data resolved cleanly.

That data turned out to be genuinely richer in one respect than
anything already in this project - a labeled confidence on JEA's own
restoration-time estimate, not just the estimate itself. It also
reports by ZIP code, not county, which every other source here uses -
resolved by converting each ZIP into its own county using the same
lookup already built for an earlier source, cached once per process
since JEA's ZIP list doesn't move between polls. JEA got its own
dedicated tables rather than sharing FPL's, on purpose, so the two
utilities' numbers could never get silently blended together.

## A public-facing Storm History, and the same lesson twice (July 12, 2026)
Picked up the design sandbox again to sketch what a genuinely
public-facing page could look like, starting from the one piece already
clear to publish: storm history. Landed on a real question to build
around - "if a storm like this hits, what happens to my area?" - which
meant translating the internal tool's language into plain sentences,
and treating the 17 storms as the real chronological timeline they are
instead of a table.

The one design note worth keeping: "no report for this storm" has to
keep meaning "we don't have a record of it," never accidentally read
as "this county wasn't affected" - the exact distinction the Miami-Dade
bug hunt existed to establish in the first place. Built the mockup to
show all 17 storms for every county, honest gaps included, with real
data (Miami-Dade: 12 of 17, matching the known number) rather than
placeholder content, and wired county selection two ways - clicking the
map directly, or a plain search box - since precisely tapping a tiny
county shape on a touchscreen is its own real problem.

Then the same lesson got applied a second time, somewhere closer to
home: the actual internal history page had the identical blur - it
only ever listed storms with real data for a county, silently leaving
the rest out, so "12 of 17" was never actually visible, just 12 cards
with no mention of the other 5. Fixed for real this time, logic only -
the internal tool stays plain and data-dense on purpose, that's not a
gap to close, just a different job than the public-facing mockup. Every
county's page now lists all 17 storms, honest "no report" rows and all,
verified against the same real numbers (Miami-Dade 12/17, Duval 13/17).

## Why the dashboard got slow (July 12, 2026)
A plain complaint - "dashboard is taking a while to load" - turned into
a real, measured diagnosis rather than a guess. Every page load was
silently recomputing all correlation results from scratch, each one a
plain nested loop over its entire raw history against every weather
alert - fine when the tables were small, not fine once the live tables
had each grown into the tens of thousands of rows from weeks of
15-minute polling (a fresh row every cycle, forever, whether or not
anything actually changed). Timed it directly instead of assuming:
about 34 seconds combined, on every single load.

The underlying fix wasn't to make the matching faster - it was to
notice the data behind it only actually changes once every 15 minutes,
while the page itself auto-refreshes every 60 seconds. A short-lived
cache made almost every reload reuse an answer that genuinely hadn't
changed yet: cold load still ~34s, everything after that within a few
minutes, about 0.05s. Deliberately the smaller fix over a deeper
rewrite that would solve the same problem more thoroughly but risks
quietly changing the matching logic's actual behavior in the process.

## Two incident IDs, decoded, one of them worth translating (July 12, 2026)
A passing question about why two utilities' incident numbers looked so
different turned into an actual investigation rather than a shrug.
Checked both against real data instead of guessing: one really does
encode its own date plus a per-day counter - confirmed directly against
real first-seen dates. The other isn't a date at all - tracked how much
the number grew over ten real days and it's clearly some much larger
enterprise-wide counter running far faster than actual outages could
ever produce, not anything specific to power.

Only one of those was worth acting on: that utility's dashboard now
shows just the meaningful part of the number, since the date part was
always redundant with the row's own "Started" column right next to it.
The other stays exactly as sent - there's nothing real underneath it to
translate, and showing a fake decoded label would be worse than showing
an honest opaque one.

## Checking on one outage after it's over (July 12, 2026)
A natural next question once you can see incident IDs clearly: can you
actually look one up? Turned into a real, small feature - click any row
in a "Recently Resolved" table and land on that one incident's own page.

The interesting part was that "one outage" means something different
depending on the source. Two utilities hand us a real incident id, and
because both quietly re-log a fresh snapshot every single poll cycle
while something's active, a specific incident already had a whole real
timeline sitting in the data - status changes, cause, customer count,
restoration estimate, all with real timestamps - nobody had ever built
a page to actually show it. The other two utilities don't have that;
neither has ever given us a discrete incident identity, only a
county-level number. So "one outage" there means one specific county
occurrence, told apart from any other time that same county had trouble
by exactly when it started.

Worth remembering: this is the same raw material the long-deferred
restoration-confidence idea would eventually need to learn from - not
that model itself, which is still waiting on a lot more resolved-
incident history to accumulate, just the part where you can finally go
look at one incident's whole story by hand.

## A dashboard row that didn't add up (July 12, 2026)
Asked to explain a single row - Nassau, 1244 correlated outages, Rip
Current Statement x280 - and answering it honestly meant actually
reading the logic behind it instead of describing what the label
implied. What it turned up: the correlation check was matching *every*
raw poll snapshot against active weather alerts, with no check for
whether an outage was actually happening. Two of the sources log a
fresh row every 15 minutes for every county/ZIP regardless of whether
anything was wrong, so "a heat advisory happened to be active while
nothing was actually wrong" was quietly counting as a correlated
outage, same as a real one.

Checked how much it actually mattered before touching anything: one
utility's match count dropped 59% once filtered to real outages only
(18,151 -> 7,495), another's dropped 84% (596 -> 97) - worse
proportionally, since its ZIP-level polling logs even more "nothing
happening" snapshots per real outage. The other two utilities turned
out to have never had this problem at all - their feeds only ever
report incidents that are actually open, so there was never a
zero-customer row to leak in in the first place. A simple filter fixed
both, which also nearly halved one utility's correlation compute time
as a free side effect.

The bigger question raised alongside this one didn't stay open for
long, though - asked to explain one more row a little later the same
night ("Broward: Air Quality Alert x190... is that a day, a month,
since we started tracking?"), and this time the honest answer was
"good question, let's actually fix that" instead of just explaining the
mechanism.

Two things were tangled up in that one number, so both got fixed at
once. First, no time bound at all - these counts had been all-time
since the poller first started, silently growing less meaningful by
the day. Second, even bounded, the count was still wrong at the unit
level: it counted every overlap between a snapshot and an alert, not
anything close to "190 alerts." A single Air Quality Alert lasting a
day and a half, checked every 15 minutes, racks up well over a hundred
matches against the same one real event.

The fix: a real time window on every correlation query (a toggle
between 7 and 30 days now sits right on the dashboard, defaulting to
30), and the alert tally switched from counting matches to counting
*distinct alerts* - de-duplicated by the alert's own real identity.
Same for the outage side. Broward's "x190" became something closer to
a small, believable number of actual distinct events - the kind of
thing someone could read out loud and have it mean something.

Then, completely unrelated - asked to make the new toggle buttons a bit
"more bubbly," rounder, with real depth - and while checking that a
color swap hadn't broken anything, the exact same bug turned up a
second time, in a different spot. The combined statewide confidence
strip at the top of the page still read "low x27118," even after
everything else got fixed. Confidence turned out to be its own little
loose end: it's purely a property of the alert itself, not of which
outage it happened to overlap, so it needed the identical
one-distinct-alert-not-one-per-match treatment the rest of the fix
already got - just living in a second place nobody had touched yet.
Fixed the same way, reusing the same dedup logic instead of writing it
twice. "low x27118" is now "high x73, medium x222, low x75."

The buttons themselves ended up pink, for a much less technical reason:
the original orange-on-black combination looked a little too much like
a certain other well-known site's branding. Swapped it for the same
pink already living in the design language - kept the orange exactly
where it still means something (the severity badges, the confidence
bar's low segment), and moved on.

## The first real shape of a public page (July 12, 2026)
A bigger question arrived next: what would it actually look like to
ship this for real, not just as an internal tool? Answering it honestly
meant admitting the existing design sandbox quietly mixed two different
things - a heat panel and storm history section that were already clear
to publish, sitting right next to a live per-county verdict map whose
publish-safety had never actually been decided, just assumed. Said so
plainly before building anything further.

The map question resolved fast, and in the opposite direction expected:
keep it, live verdict and all. The reasoning was refreshingly direct -
a sufficiently attentive visitor might work out that the site draws on
several utilities' own public information, and that's fine, because the
information itself is genuinely useful to the person looking at it.
Not every risk is worth avoiding at the cost of the thing actually
being useful. The internal-only ops telemetry - utility breakdowns,
confidence-score bars, top alert types - didn't get the same reprieve,
and rightly so: that's monitoring language for someone running the
system, not information a resident showed up looking for.

What came out of it is a genuinely new page concept, not an update to
the old design sandbox - a real pivot. Same map, same real county data,
same heat panel, same storm history timeline, restructured as an actual
front page: a plain-language lede explaining what the site is, the map
and two live numbers up top, heat and storm history given full-width
room to breathe instead of living as sidebar widgets, and a footer that
says outright what the page does and doesn't show - a derived read on
weather and outages, not a live feed replay, and not a substitute for
checking your own utility's outage map. Nothing here is live in the
real app yet - still a concept, still waiting on an actual test/
production environment that doesn't exist yet either.

## The Panhandle hunt, and a bug that needed catching twice (July 12, 2026)
One more section landed in the public concept that night - a page
showing every active weather alert statewide, not just heat. Real and
small: two alerts active at the time, one of them a rip current
statement for the Panhandle coast, which turned out to matter more than
expected.

Because the real project that night was finding out who actually serves
power to that missing corner of the state. A real lead turned up fast -
FPL runs a whole separate regional feed for the Panhandle, clearly
built around exactly that geography. Getting past it wasn't so fast -
real automated-traffic protection sits in front of the actual data, and
it held that first night. Worth another pass with fresh eyes, not a
dead end.

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
at - the real feed was found, same shape as the one this project
already knows how to read, just a different address.

Turns out this isn't really a fifth utility at all, just a second door
into the same one - Gulf Power became FPL on paper back in 2021, and
whoever maintains the Panhandle feed never got around to merging the
two. So it went in the way that actually matched reality: folded
straight into the existing FPL pipeline, no new tables, no new
correlation function, nothing built from scratch. Fetch both feeds,
combine the results, treat it as one utility, because it genuinely is
one. Closed eight of the ten missing counties outright. Three remain -
probably someone smaller, still unfound, still an honest gap instead of
a guessed-away one.

## Peak isn't the same thing as right now (July 13, 2026)
A comparison against a well-known public outage tracker's live count
for Palm Beach surfaced a real, honest confusion: our dashboard said
2,343 customers out, theirs said 233. Neither number was wrong. Pulling
every raw poll since that outage began showed a real spike to 2,343
hours earlier, climbing back down to 230 by the time of the comparison
- our "peak" column was showing the whole episode's high-water mark,
not what's happening this exact minute, with nothing on the page saying
so.

Fixed at the source rather than papered over: every utility's open-
outage view now also shows a live "current" reading sitting right next
to the existing peak column, on both the real dashboard and the top
summary strip that had the same conflation baked into its own math. The
public-page concept got the identical fix and, while at it, its first
real narrative summary paragraph, built entirely from a live query
against our own database, not copied text - and deliberately didn't
borrow another aggregator's utility-count figure either, checking our
own historical roster first rather than assuming their number applied
to us.

## Three utilities, one dead end, and a night of real bugs caught early (July 13, 2026)
Kept pulling the same thread: what else is a real, live Florida utility
we're not tracking yet. City of Tallahassee came first - a real catch
before it ever shipped: an early data pull looked like a valid but
empty result, schema and all, until a real request came back shaped
completely differently. It had quietly been reading the wrong layer of
data the whole time. Caught by checking the source's own layer listing
directly, not by anything crashing. A second, smaller version of the
same lesson: a "zone" field was internally numbered by database row,
not by the digit written into each zone's own name - same shape of trap
as the Miami-Dade and DeSoto county-name mismatches from earlier in
this project, just wearing a different costume.

Talquin Electric Cooperative came next - real, and genuinely useful: it
closed two of the three remaining Panhandle gaps (Gadsden, Liberty)
outright, confirmed by matching its live county list against our own
historical storm data and getting an exact match.

In between, a real dead end, handled the honest way: a small Georgia
cooperative looked like a plausible lead for the last gap given how
close it sits to the state line, the same reasoning that made an
earlier real find work. It wasn't - their own published service area is
entirely in Georgia, nothing in Florida. Said so plainly rather than
force a fit, including owning that the original lead was a guess, not
confirmed fact.

Florida Public Utilities Corporation closed the night - and it came
with a real, unresolved limitation instead of a clean win. Its live
feed reports exactly one combined total across five non-adjacent
counties, no per-county split, despite a real search for a richer
per-county view that never turned one up. Built honestly instead of
guessed around: a fixed placeholder county label that can't match a
real weather alert, so its correlation function returns empty by
design, not silently broken. At the time, this was the closest thing to
real coverage one particular Panhandle county had - just as part of one
blended five-county number, not a verified reading for that county on
its own. That gap was properly closed later on (see below).

## A map that finally shows what's happening right now (July 13, 2026)
The isometric county map had one real gap the whole time: its color and
height were always driven by historical weather-correlation pattern,
never by what's actually happening at this moment - the live outage
count only ever showed up in a tooltip, never in the map itself. Added
a second view instead of replacing the first: a toggle between
"Historical Pattern" (unchanged) and "Live Severity," the second one
tiered by real current-outage percentage where a county's customer base
is known, falling back to a raw-count tier for the handful of counties
only covered by sources with no live base. Caught two rough spots in
the rewrite before shipping: a needless workaround standing in for
something that could just be done directly, and a conditional quietly
returning the same value on both sides.

## The last county, closed (July 14, 2026)
One real gap had been sitting there since the Panhandle work began: a
single county with no true per-county live reading of its own, only
ever showing up blended into someone else's combined total. Rather than
guess at who might serve it, the answer came from data already on
hand - cross-referencing every historical storm report for that one
county turned up a real, consistently-appearing rural cooperative, the
same kind of lead that had worked before for other real finds this
project made.

It turned out to run on the identical underlying platform as three
utilities already integrated - same feed shape, same combined-territory
situation, same "wait for a real event" gap on its own per-region
detail. Confirmed its real six-county territory the same way as always:
against the historical record, not assumed. Closing it means every one
of Florida's 67 counties now has a real, verified live source behind
it - not a milestone chased for its own sake, just the natural end of
a thread that started with one simple question: who else out there is
a real, live Florida utility this project isn't tracking yet.

## A second app, not a second coat of paint (July 14, 2026)
The public-facing design concept had been sitting as a frozen mockup
for a while - real numbers, but hardcoded, no live wiring, no backend.
The plan for turning it into something real settled on two things at
once: keep the internal dashboard exactly what it already is (dense,
operator-facing, for whoever's actually running this), and build the
public concept as a genuinely separate thing, not a mode of the same
app. Two different audiences, two different jobs - no reason to force
them into one codebase just because they read the same data.

"Separate" meant something concrete: its own Flask app, its own port,
its own template folder, and - the part worth being strict about -
zero imports from the internal dashboard's own code, in either
direction. The two only ever share the same read-only data layer
underneath, the same way the poller and the internal dashboard already
do. Getting there honestly meant a real refactor first: three pieces
of logic that had only ever lived inside the internal dashboard's own
file (how a county's current status gets assembled, how historical
storm data gets queried, how a raw timestamp becomes a sentence) moved
out into their own small shared modules. Not just duplicated - moved,
so there's exactly one version of each doing the reasoning, and the
existing test suite kept passing, unchanged, the whole way through.

The map turned out to need real work of its own. The original real
county boundary data behind the earlier design concept had only ever
lived inside that one disconnected mockup - never actually saved
anywhere in this project. So it got pulled fresh from the same kind of
public source as before, projected and simplified the same way (real
surveyed coordinates, not hand-traced), and this time saved as a real,
regeneratable file instead of a one-off. Rendered it standalone first,
before wiring anything else to it, just to look at it - recognizably
Florida, panhandle and all, real islands and barrier islands showing up
correctly because the underlying data is real.

Coloring each county by current status turned out to need a real
design decision, not just a lookup. Some sources report a real
percentage of customers affected; others (the ones that hand over a
real incident instead of a county rollup) never have a clean
denominator to compute one against. Rather than pretend they're the
same kind of number, a county's severity now comes from whichever
signal it actually has - a real percentage where one exists, a coarser
customer-count tier where it doesn't - collapsed into one honest label
per county, worst source wins. The statewide headline numbers (how many
counties are clear, how many customers are affected right now) come
from that same single pass over live data, not a separate query
pretending to agree with the map.

Runs locally for now, same as the internal dashboard always has -
putting it on the actual internet is still its own later decision, not
something that happens by default just because the code exists.

## The rebuild: fetching the real thing instead of remembering it (July 14, 2026)
First pass above shipped, then failed a look-at-it-live test almost
immediately: wrong colors, a flat map where an isometric one was
promised, no narrative summary, and a real comma-joining bug the
moment an alert or a storm entry rendered with more than one item in
it. The root cause was process, not code - the actual design mockup
was temporarily unreachable when the build started, so it got built
from a remembered description instead of the real file. A reasonable
fallback in the moment, but not actually the same thing, and it showed.

Once the mockup was reachable again, the honest fix was a real,
close port instead of another guess: the same isometric projection
math, the same color and type system as the internal dashboard (this
page was always meant to share that identity, not invent its own), the
same narrative-summary block, and a toggle between two ways of coloring
the map - one all-time, from how often a county's weather alerts have
actually lined up with a real outage historically, one live, from
current severity right now. The all-time view needed a new pass over
every real per-county correlation function at once, tallied per
county - a genuine new piece of logic, not a copy of anything that
existed before.

Two real bugs only showed up once the page was checked with something
that actually runs its JavaScript, not a static thumbnail: a
county-name casing mismatch that silently grayed out the entire map,
and the same comma-joining bug from the first pass, still present
because an alert's list of areas comes back as one joined string, not
a real list, unless something along the way splits it. A third real
bug was a 44-second page load, caused by recomputing that new all-time
tally from scratch on every single view - fixed the same way an
identical problem was already solved once on the internal dashboard, a
short-lived cache, since the underlying data only actually changes once
a poll cycle.

## A fourteenth utility, and one number instead of two (July 14, 2026)
Lake Worth Beach Utilities joined next - a single real city inside Palm
Beach County, already covered indirectly through FPL's county-wide
number but never on its own. Its live feed turned out to offer
something genuinely new: a real, always-present city-wide total
(current customers out over total customers served) alongside a
separate, richer feed of individual outages - street, cause, and crew
status per incident, more detail than any other source here reports
directly.

That shape raised a real design question rather than a parsing one:
with two views of the same real outages, which one counts? Reusing
both would double the same real customers into every statewide total.
The answer followed a principle this project had already reached for
once before, in the other direction - Florida Public Utilities
Corporation's combined total and its real per-incident view exist side
by side, and only one of them feeds the statewide numbers. Here, the
city-wide total became that one authoritative number (a real
percentage, not a raw count) and the individual incidents stayed
exactly what they are: real, useful, per-outage detail, on their own
dashboard section, never summed into anything statewide.

## The fifteenth utility, found the same honest way as the fourteenth (July 16, 2026)
A live poller restart was quietly grinding through a genuinely
different kind of wait - a cloud VM stuck behind a real capacity
shortage on a free-tier compute shape, nothing to do about it but check
back periodically. Rather than let that dead time actually be dead
time, the same question that once found the twelfth utility came back
around: who else out there is a real, live Florida utility this
project isn't tracking yet - ranked honestly by how much they'd
actually matter, not just the next name on a list.

Cross-referencing every utility that's ever appeared in this project's
own historical storm data, one candidate stood out by a wide margin -
more than double the size of the next real option, and present in
two-thirds of the storms on file. A real live outage map for it was
confirmed before anything else got built, same discipline as every
integration before it. It turned out to run on the identical
underlying vendor platform the fourth utility already uses, so the
resolution logic didn't need to be invented twice.

Its feed also hinted at something richer - real per-incident detail,
not just a summary number - but that layer only ever showed up empty
the day it was checked, for an honest reason: nothing was actively
wrong at that exact moment for it to describe. Rather than guess at a
shape from an empty response, only the real, confirmed part shipped -
a real city-wide percentage for a county that already had some
coverage, just never a clean customer-base denominator to trust before
now.

## The sixteenth, the same ranking's runner-up (July 16, 2026)
Once the fifteenth was live, the same historical-storm ranking's #2
candidate was the obvious next move - a large real cooperative, edged
out only narrowly the first time around. Its live feed turned out to be
the simplest integration yet: a fully public, unauthenticated data file
with real native per-county numbers, no tracking code or gate to work
around at all. It also exposes a genuinely live per-incident array with
real start and restoration-estimate times - unlike the fifteenth's, this
one isn't empty, just out of scope for this pass.

## Off the laptop, onto a real server (July 17, 2026)
Everything above had been running off a laptop under a desk - fine for
a hobby project, less fine for something meant to run day and night
without depending on someone's Wi-Fi staying up. Moving it onto a real,
always-on server was next.

Landed on Oracle Cloud's free tier - genuinely free, not a trial, with
enough real headroom (an ARM-based shape, up to four cores and 24GB) to
run all three real apps - the poller, the internal dashboard, the
public page - at once. The region choice mattered more than it
sounds: free-tier resources lock to whichever region gets picked
first, no changing it later.

Actually getting the instance running was the real fight, not the
setup - the same popular free-tier shape everyone wants, and the
region was consistently out of capacity. Seven real attempts across
all three of the region's independent capacity zones, spread over
about a day and a half, before one finally landed. Every single
failure was a genuine capacity shortage, confirmed by the shape of the
error itself, not a misconfiguration second-guessed after the fact.

Networking had its own real snag: the fast quick-create path didn't
expose a way to actually assign a public IP - a real, documented
console limitation, not a mistake made along the way. Built it
properly instead, through the full networking console: a private
network, a route out to the real internet, and a subnet - the same
handful of pieces any real server needs before it's reachable at all.

Migrating the actual project meant treating code and secrets
differently on purpose. The code came over the normal way - cloned
straight from the same place this project already lives. The real
database (already carrying real accumulated data, never started
fresh) and the real API credentials moved over a direct, private copy
instead, never through anywhere public.

Getting all three real processes running unattended, restarting
themselves on their own if they ever crashed, surfaced two genuine,
unrelated gotchas along the way - both looked identical at first (the
exact same cryptic failure code), but turned out to have entirely
different real causes once actually investigated instead of assumed
to be the same problem twice.

With everything verified running clean - a full automated test suite,
several complete live cycles, every route actually loading - the last
step was the real cutover: the laptop's own copy stood down for good,
and the server became the only live source. Two independent backups
sit underneath it now: the laptop pulls a fresh copy of the real
database twice a day, and the server's own disk gets a real, automatic
snapshot once a day on Oracle's side - both genuinely free, neither
one depending on the other.

## A long day of real bugs, not one big feature (July 17, 2026)
Started by comparing the internal dashboard against the public page and
finding they didn't quite agree - the beginning of a real bug-fixing
run, not a planned sprint.

A live Duke incident with no reverse-geocoded location crashed the
whole public page - the actual bug turned out one layer deeper: TECO
and Duke's own incident-tracking code overwrote an already-known-good
county with `None` the moment a single later lookup failed, instead of
keeping the last real value. Fixed, then backfilled every incident
already sitting in that bad state (34 of 40 recovered on retry once
the fix went in). Nearby, a real county-name mismatch: FPL stores "De
Soto" and "St Lucie" with a plain space, this project's own canonical
names use a period or none at all - confirmed with a real test before
fixing that a genuine match would've been silently invisible on the
live map the moment it ever happened.

City of Tallahassee turned out to have never actually worked - the
live feed's own ticket field, the whole basis this project used to
track one incident across polls, was empty on every single real
incident checked, every time. A hundred percent silent data loss since
the day it was integrated, caught while investigating why one of its
tables sat at zero rows. Redesigned as a county-rollup source instead
(the same shape FPL and Talquin already use), since there's no
reliable per-incident identity in that feed to hang tracking off of at
all.

Also: the repeat "still down" email for the two chronic sources got
throttled down to nothing (the one-time "it's back" email stays, since
that's still real, wanted news), and two separate real number
mismatches between the dashboard and the public page got run down and
fixed - one where the public page's own headline customer count
silently couldn't include a combined-territory source's number at all,
one where the dashboard's own statewide total was missing an entire
real utility's incident view.

## Restoration confidence, for real this time (July 17-18, 2026)
The long-deferred idea finally got a real, honest shape - not one
model for every utility, two genuinely different ones for the two
utilities that can actually support them.

FPL can never get a live incident-level model (its feed only ever
reports a county-wide total, and a busy county's number often never
resets cleanly to zero between real, separate outages), so it gets a
historical-precedent pair instead, kept as two distinct numbers on
purpose: "Major Storms," a real min/median/max restoration range from
the 17-storm archive, and "Everyday Outages," the same idea from this
project's own live tracking - a genuinely different, more common
question the storm archive alone can't answer. The everyday version
needed one real filter (anything over 96 hours excluded as a likely
blurred multi-outage reading, a cutoff read straight off a real, sharp
break in the live data, not guessed), the major-storm side needed a
real correction instead: the working definition of "major storm" had
assumed a wind-speed threshold that turned out not to describe three
of the real seventeen at all, once actually checked against their real
names.

TECO got something else entirely, because it already has something FPL
doesn't - a real per-incident restoration estimate on every ticket.
Instead of inventing a range, the honest question there is whether
that number can already be trusted: comparing every real resolved
incident's first stated estimate against when it actually closed.
Turns out TECO's own number is a good one - a statewide median of about
two and a half hours *earlier* than promised, on-time-or-early roughly
two times out of three.

Duke got a third shape the same day, once its own real limits were
actually checked instead of assumed. Its own feed has no restoration-
estimate field at all, so there's nothing to check the way TECO's
number gets checked - but Duke already reports real, individually-
tracked incidents, not a blurred county-wide aggregate the way FPL
does, so it never needed FPL's outlier filter either. The simplest of
the three in the end: a plain duration precedent, real min/median/max
from 7,195 real closed incidents statewide, only one of them over 48
hours the whole time. Real number: about an hour and twenty minutes,
typically, statewide.

JEA still doesn't get any version as of this session - real
per-incident data doesn't exist for it at all, county-rollup only like
FPL, so whatever it eventually gets has to be FPL's shape, not Duke's
or TECO's. Real, honest, still-open work, not something this session
got to. (Closed the following night - see below.)

FPL's Major Storms range also got a real refinement the next day (July
18): it was one blended min/median/max across every storm regardless of
strength, so a glancing tropical storm and an actual Category 3 landed
in the same number. Split by real wind severity instead, using the
National Hurricane Center's own 74mph hurricane-force threshold against
the same `historical_storm_severity` table this project already had
sitting unused for exactly this - "Hurricane-Force Storms" and
"Weaker Storms" now report separately, falling back to the old blended
number for a county that's only ever seen one bucket.

## Closing the utility gaps: JEA, LWBU, and four co-ops that only know their own street names (July 18-19, 2026)
Two real per-incident sources still had zero restoration signal after
the July 17-18 push, for two different honest reasons. JEA had never
gotten one at all - closed the same way Duke's shape was reached, a
plain min/median/max duration precedent from JEA's own closed
incidents, since JEA has no restoration-estimate field of its own to
check the way TECO's gets checked. Lake Worth Beach Utilities had never
gotten the TECO-style check either, despite carrying the same real
per-incident restoration estimate - built the same way, and along the
way caught two real bugs a clean local fixture had been hiding: LWBU's
timestamps sometimes arrive timezone-aware and sometimes naive, which a
straight subtraction can't handle, and LWBU doesn't always zero-pad
fractional seconds, which the VM's older Python rejects outright where
a newer one wouldn't. Both only showed up once tested against the
VM's real messy data, not the clean local kind. A third bug, lower
stakes: the new accuracy check first landed pointed at the wrong table
- LWBU's rollup view, not its incident-level one - fixed by gating on
the real incident table directly. TECO, FPUC, and LWBU also picked up
a live per-incident ETR display the same night: any of their ongoing
outages that already carries the utility's own restoration estimate
now shows it inline, not just in the aggregate accuracy numbers.

The other four - TCEC, EREC, CHELCO, and GCEC - share a real, different
limitation: each hands over a combined multi-county total plus a raw
list of affected street names, with no county tag attached to either.
Closing that meant real geocoding, constrained to each utility's own
known counties rather than an open nationwide search (an unconstrained
lookup for a real CHELCO street matched three counties nowhere near
CHELCO's territory; the same name constrained to CHELCO's own four
came back correct). Every resolved street gets cached forever, so a
given name only ever gets looked up once. A real bug turned up during a
second audit pass the following night: a genuine network failure and a
confirmed zero-result response were both collapsing into the same
"no match," so a transient hiccup could get permanently cached as
"this street isn't in this county." Split into two real outcomes -
a confirmed no-match still caches as `None`, a technical failure now
raises its own exception and gets retried next cycle instead - and 69
stale entries already poisoned by the old logic got cleared from the
VM.

The rest of the same stretch was mobile and reading-experience work,
prompted by actually loading the public page on a phone: the county map
now scales to the viewport instead of overflowing it, five walls of
explainer text collapsed behind "what is this section?" toggles, the
Storm History county search became a real type-to-filter combobox
instead of a plain dropdown, and a genuine overflow bug got found (a
run of county names with no spaces - "Bay/Calhoun/Gulf/Jackson/
Walton/Washington" - couldn't line-break on a narrow screen) and fixed
after an initial wrong guess at the cause. Outage History moved from a
hard 15-row ceiling to real pagination. And the public-facing wording
got one more pass, softening a few remaining phrases that read as
implying a direct read of utilities' own systems rather than their
published public information - the same privacy stance already applied
everywhere else, just closing the last few spots that had drifted from
it.

A second full audit sweep closed out the stretch - the same eight-
category check (test suite, data integrity, wiring, live smoke test,
known bug classes, real-number cross-reference, infrastructure and
ops hygiene) run once already this week - and it's what caught the
street-resolver network-failure bug above before it could quietly
mismark more streets. 562 tests passing by the end of it, up from 474
at the start of this whole stretch.

## A test suite that stops relying on memory (July 19, 2026)
The 562-test suite had one real gap left, process rather than code:
nothing forced it to actually run. Wired up GitHub Actions to run the
exact same suite automatically on every push to `main` - no new tests
written, just guaranteeing the existing ones can't quietly get skipped
on a rushed night. Pinned to Python 3.9 specifically, to match the VM's
real production interpreter rather than the older local dev version -
the same kind of version gap that let LWBU's fractional-seconds bug
slip past local testing earlier this same stretch.

Deliberately narrow, on purpose: this covers exactly one of the eight
categories in a full audit sweep, the automated test suite itself.
Data integrity checks, live smoke tests, and infrastructure/ops hygiene
all still need real running access - the live database, live URLs, the
actual VM - that a public repo's CI shouldn't be handed secrets or SSH
access to reach. Those stay a manual ask, same as always; CI just means
the one piece that *can* run in a clean sandbox now always does.

## A real gate in front of main, not just a report card (July 19, 2026)
CI reporting pass/fail after a commit already landed on `main` was
still an honest gap - it could tell you something broke, but nothing
stopped it from getting there in the first place. Closed with a real
GitHub ruleset on `main`: a pull request is now required for every
change, the "test" status check has to pass before that PR can merge,
and force-pushes/deletions on `main` are blocked outright. Required
approvals set to 0 on purpose - solo repo, no second person to ever
approve a PR, so the real gate is the passing test suite, not a review.

Verified for real, not just trusted: a direct push to `main` was tried
first and actually rejected by GitHub before anything got redone
properly as a branch, a real pull request, and a real merge click.

## A seventeenth utility, ranked instead of guessed (July 19, 2026)
The same honest question came up again - who else out there is a real,
live Florida utility this project isn't tracking yet - but answered a
new way this time: cross-referencing every utility that's ever
appeared in this project's own 17-storm historical archive and ranking
what's left by real footprint (records, storms, counties, peak
customers), the same method that found the fifteenth and sixteenth
utilities. One candidate stood out clearly - more than double the next
real option by record count, present in 13 of the 17 storms, real
coverage across 15 counties.

Its real live feed turned out to be the richest shape confirmed yet:
genuine native per-county numbers (no combined-territory blur at all,
unlike four of this project's existing sources), on the exact same
public, unauthenticated platform the sixteenth utility already runs
on - same JSON shape, same lack of any tracking code or WAF gate,
just a different per-county grouping key confirmed against its own
real response rather than assumed identical. Its feed also carries a
real per-incident array with what looks like a genuine restoration
estimate - noted and left deliberately out of scope for this pass,
the same honest disclosure the sixteenth utility's own unused array
already got.

Built the same way as every integration before it: its own dedicated
tables, its own lifecycle tracking, its own weather-correlation
function, wired into the same live severity map and historical
confidence tally every other real per-county source feeds - nothing
shared or blended with an existing utility's numbers. Verified against
the real live endpoint before and after building, not assumed to
still match a captured example.
