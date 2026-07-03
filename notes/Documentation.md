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
- The poller is out there right now, polling, unattended. `outages.db`
  is growing on its own.
- Real weather alerts have started showing up and correlating with
  real (small) outages — the mechanism works, we're just still
  waiting on a real storm to test it against anything dramatic.

## The honest gaps
- Can we guess when the lights come back on for a *live, ongoing*
  outage? Still not really — FPL's own live feed still only gives us
  three fields, nothing has changed there.
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
