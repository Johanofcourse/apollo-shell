# Apollo Shell — where did this thing wander off to?

Started as a "shell." Somewhere along the way it became an outage
detective instead. No regrets.

## Done
- FPL outages and NWS weather alerts both get fetched *and actually
  saved* now (they weren't, for a while — alerts were being parsed
  and then thrown straight in the trash)
- Correlation logic (`apollo_shell/correlate.py`): does an outage
  share a county and a time window with a weather alert? If so, they
  get introduced to each other
- A 15-minute poller (`main.py`, "Apollo Sentinel") running live in
  the background via launchd — fetch, save, correlate, log, repeat,
  forever, quietly. Configured to survive the laptop staying awake
  overnight, screen dark, on AC power.
- Outage lifecycle tracking (`outage_events` table): we finally know
  when an outage *starts* and *ends*, not just "customers out: 41"
  frozen in a single snapshot
- A local live-status dashboard (`dashboard.py`, Flask, localhost
  only) — open outages, resolved durations, recent weather, and
  current correlation matches, at a glance instead of a terminal query
- **Real historical storm data**, not just live-forward collection.
  Two past hurricanes imported end to end, across *every* utility
  operating in the affected counties (not just FPL) — real multi-day
  outage durations, not simulated ones. Sitting in their own local
  databases, deliberately kept off GitHub.
- A second, independent public data source layered on top of the
  historical storms — reported wind severity per county, matched
  against our own outage numbers. Confirms the obvious (worse wind,
  worse/longer outages) in FPL's actual core territory, and taught us
  a real lesson where it *doesn't* hold: comparing storm-wide weather
  against one single utility's numbers is misleading in counties
  where that utility barely operates.
- A real idempotency bug, found and fixed: re-running a historical
  import against an already-populated database used to silently
  duplicate every event in it — including inflating a real 7-day
  blackout into two identical fake ones. Now guarded at the database
  level (uniqueness constraints), not just "remember not to do that."

## The TECO plot twist
Same night, different rabbit hole. Went looking at whether other
Florida utilities' outage maps might have richer live data than FPL's
— and one of them does, dramatically so. Found (through a browser's
own network traffic, not anything documented) a second utility's live
map backend that hands over, per individual outage incident: real
coordinates, an actual stated *cause* ("tree limb," "equipment
damage," genuinely once "squirrel"-adjacent), live crew status, and —
the thing this whole project spent ages concluding didn't exist
anywhere — a real per-incident estimated restoration time, live,
today.

Built it as its own clean module, matching the project's one-file-
per-source habit, with its own lifecycle tracking (easier than FPL's,
since this source hands us a real incident ID instead of making us
infer continuity from numbers crossing zero) and its own bridge into
the weather-correlation logic (reverse-geocoding coordinates into a
county, since this source only gives us where, not which county).

Also cleaned up two things before they became real problems:
- This same utility already existed in the historical storm data
  under its formal name — now both live and historical data agree on
  one canonical name instead of silently being two different things
  that happen to mean the same utility.
- The cause/status fields are free text and always will be — instead
  of fighting that, added a derived category alongside the original
  text (never replacing it), so "squirrel in tree" stays exactly as
  written *and* becomes something code can actually filter on.

Found a real bug deploying this live (a migration backfill gap), and
double-checked a nagging timezone assumption that turned out to
already be correct rather than just leaving it as a question mark.
Both good reminders that "it works" and "it's been checked" are not
the same sentence.

## Where the historical data actually comes from
Deliberately not documenting the specific sites/endpoints here. Broad
strokes: one public source publishes after-action situation reports
during declared storm emergencies, county-by-county, per utility,
with an actual restoration-time field — the thing FPL's live feed
doesn't have. A second, unrelated public federal archive covers
historical storm/weather events with narrative detail, occasionally
including reported wind speeds. Both took real digging (and some
verification of a few sources that turned out to be dead ends or
outright fabricated) to confirm were real and usable.

## Live right now
- The poller is out there right now, polling, unattended, from three
  independent sources instead of two. `outages.db` is growing on its
  own.
- Real weather alerts have started showing up and correlating with
  real (small) outages — the mechanism works, we're just still
  waiting on a real storm to test it against anything dramatic.
- The new incident-level source is quietly the most interesting thing
  running right now — it's the first live data we've collected that
  actually has a restoration-time field in it. Still just a handful of
  minor incidents so far, nothing dramatic, but the pipe is open.

## The honest gaps
- Can we guess when the lights come back on for a *live, ongoing* FPL
  outage? Still not really — FPL's own live feed still only gives us
  three fields, nothing has changed there. But we now have a *second*
  live utility whose feed actually does carry a restoration estimate —
  it's real, but it's an undocumented endpoint someone could change or
  block without warning, so "we have this now" comes with an asterisk
  that FPL's boring, stable, public JSON file never needed.
- But for *historical* storms, the picture improved a lot tonight:
  we now have real, multi-day, multi-utility restoration data for two
  major hurricanes, plus independently-verified severity context for
  most of the affected counties. Two storms is still a small sample —
  not enough to trust an average, but enough to stop being purely
  hypothetical.
- The plan going forward: keep working backwards through more past
  storms (one at a time, each one gets independently verified before
  trusting it — every storm so far has had at least one real data
  quality surprise once actually checked).
- The original plan — AI input parser, command history, an actual
  interactive shell — is still quietly benched. Turns out "map power
  outages against weather" was the more interesting rabbit hole.

## The 2018-2025 backfill, and the confidence label
A later session, all business: went back through the archive and
pulled every remaining storm PSC has a report series for, one at a
time, all the way back to Alberto in May 2018 — 17 storms total now,
every utility per storm, each one independently sanity-checked before
being trusted (this is where the two-utility-name-mismatch bug turned
out to be a recurring species, not a one-off — same fix needed for
FPL, TECO, and Duke in turn, plus a PDF-extraction truncation that
mangled a real co-op's name). A full cross-storm sweep afterward,
comparing every (utility, county) pair against every other storm's,
caught the last couple of stragglers without needing to already know
Florida utility geography by heart.

On top of that: a real weather-match confidence label
(`weather_match_confidence()`), separate from NWS's own severity
field on purpose — a "Severe" Rip Current Statement should never
outrank a "Moderate" Tornado Warning, so event-type plausibility
drives the label first, severity only nudges it within an
already-plausible type. Landed as high/medium/low, not a percentage,
and shipped straight into the real dashboard alongside confidence
bars, severity badges, and a KPI strip up top.

## The design detour
Then a very different kind of session: less "fix a bug," more "what
should this thing actually look like." Used Claude's Artifact tool as
a disconnected sandbox — never touching the real dashboard or live
data — to iterate on a visual language before touching
`dashboard.py`/`templates/dashboard.html` for real.

Went through a real pivot partway through: started bright and
Big-Sur-icon bold, then scrapped that for something closer to Swiss
wayfinding-system instrumentation — flat, typographic, dark mode,
signal colors (magenta/yellow/lime/cyan) doing double duty as status
lamps. Added a telemetry sidebar, an explainer for what the confidence
label actually means in plain English, and expanded the county log
from a curated handful to all 22 tracked counties, sorted worst-verdict-first.

The map, though, was its own saga. Tried a gauge (looked empty, cut).
Tried a simplified 6-region isometric grid (too blocky, didn't read as
Florida). Tried hand-tracing a real Florida county map, twice — first
from memory, which looked "like a bad steak" by the account of the
person watching it render; second from an actual reference image,
which was better but still visibly hand-eyeballed, gaps and all. The
real fix: pulled actual US Census county boundary data (a public
GeoJSON, not a picture) and wrote a script to project and simplify it
— Douglas-Peucker, not a human's steady hand. That's when Monroe's
seven separate landmasses (mainland plus six real Keys islands), and
Lee's, Pinellas's, and Franklin's barrier islands, showed up
correctly for the first time, because they're just... actually there
in the data. Ended on a flat, north-up, real county map with a subtle
raised-slab shadow and a plain grid backdrop — genuinely accurate this
time, not approximated.

Nothing here touched the live app yet. That's the deliberate next
step, not started: port the agreed-on look from the sandbox into the
real dashboard once it's actually settled.

## End of a long one
Real late-night session, this one. Went from "let's re-explain the
correlation logic" to reverse-engineering a live utility API through
someone else's browser tab, and somewhere in the middle fixed about
half a dozen real bugs that would've quietly poisoned the data if
nobody had gone looking. Nothing dramatic happened weather-wise
tonight — small potatoes, a handful of minor incidents, no real storm
to test any of this against yet. That's fine. The point of tonight
wasn't finding a storm, it was making sure the thing is actually ready
to trust when one shows up. Poller's still out there right now, doing
its quiet thing. Good stopping point.
