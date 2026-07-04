# Apollo Shell Roadmap

## Phase 1: Foundation (Done)
- [x] FPL data collection (live, 15-minute polling)
- [x] SQLite storage
- [x] Web dashboard (`dashboard.py`, localhost only — not public-facing)
- [x] NWS weather integration (fetching + storing active alerts)
- [x] Basic weather/outage correlation — see
      [`docs/PRD-weather-correlation.md`](./PRD-weather-correlation.md)
      for what's built vs. still proposed here (e.g. confidence
      scoring is not built yet, don't check that off based on this file)

## Phase 2: Multi-Source Intelligence (In Progress)
- [x] Second live Florida utility integrated end-to-end (incident-level
      data, richer than FPL's — real cause, live status, and an actual
      restoration estimate)
- [x] Historical storm data backfilled — 9 storms so far (2022-2025),
      every utility per storm, not just one — kept private, not on GitHub
- [x] Historical severity data layered in from an independent public
      source, cross-checked against our own outage numbers, extended
      to cover winter events (snow/ice/wind-chill) and severe
      thunderstorm/tornado events, not just tropical systems
- [x] Data integrity pass — idempotent writes across every table, so
      re-running any collector can't silently duplicate data
- [ ] Correlation confidence scoring (see PRD — proposed, not designed
      in detail yet)
- [x] Third live Florida utility integrated end-to-end — Duke Energy,
      same pattern as TECO (incident-level data, live county rollups,
      system alerts, correlation). Its live map blocks a plain
      automated fetch, so this one needed a human driving a real
      browser through devtools to find, same as how TECO's was found
- [ ] More Florida utilities beyond FPL/TECO/Duke, one at a time, same
      proven pattern

## Phase 3: Predictive (Blocked on data, not code)
- [ ] Restoration-time estimation from historical patterns
- [ ] Connect live data to the historical storm dataset — e.g. "current
      live weather severity resembles what we saw during Storm X in
      this county, which took about Y hours to restore." Explicitly
      deferred, not just unstarted: needs (1) running the same
      severity extractors against live weather alert text, not just
      historical NOAA narratives; (2) a real decision on querying
      across 9 separate storm databases vs. consolidating them into
      one; (3) enough live volume that "closest historical match" is
      more than 1-2 data points per county. Revisit once Phase 3's
      data-volume bar (below) is actually met.

This phase is **not a normal engineering task with a schedulable
timeline.** It requires enough accumulated real outage-duration data
before an estimate would mean anything — no amount of additional code
shortens that. Rough real numbers as of this writing: FPL alone is
producing on the order of dozens of resolved outages per day live, so
"enough for a first rough look" is more like weeks, "enough to trust"
is more like a month or two, and that's still only one season's worth
of conditions. Historical storm data (9 storms so far, 2022-2025)
covers a different, rarer category of outage entirely and doesn't
substitute for everyday-outage volume — and even across 9 storms, most
individual counties only show up in 2-4 of them, so it's still a thin
sample per county, not something to treat as a reliable average yet.

## Phase 4: Scale (Open question — not yet committed)
- [ ] More utility integrations beyond Florida

  Bigger than it sounds: a different state means a different utility
  regulator, a different NWS forecast region, and the Florida-county
  validation logic built into the historical importer would need to
  generalize. Worth treating as its own design decision when it comes
  up, not an incremental add.

## Explicitly not planned
- **A public API for other developers.** The accumulated database —
  not the code — is this project's actual asset, specifically
  *because* it's hard for anyone else to replicate, and it's been
  deliberately kept off GitHub for that reason. A public API would
  hand that asset to anyone who asks, which directly contradicts that
  decision. Not on the roadmap; would need a real, separate decision
  to reverse, not something to slide in as a routine "eventually" item.
