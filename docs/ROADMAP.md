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
- [x] Historical storm data backfilled (two major hurricanes, every
      utility per storm, not just one) — kept private, not on GitHub
- [x] Historical severity data layered in from an independent public
      source, cross-checked against our own outage numbers
- [x] Data integrity pass — idempotent writes across every table, so
      re-running any collector can't silently duplicate data
- [ ] Correlation confidence scoring (see PRD — proposed, not designed
      in detail yet)
- [ ] More Florida utilities, one at a time, same proven pattern as the
      first two (Duke Energy is next)

## Phase 3: Predictive (Blocked on data, not code)
- [ ] Restoration-time estimation from historical patterns

This phase is **not a normal engineering task with a schedulable
timeline.** It requires enough accumulated real outage-duration data
before an estimate would mean anything — no amount of additional code
shortens that. Rough real numbers as of this writing: FPL alone is
producing on the order of dozens of resolved outages per day live, so
"enough for a first rough look" is more like weeks, "enough to trust"
is more like a month or two, and that's still only one season's worth
of conditions. Historical storm data (2 major hurricanes so far)
covers a different, rarer category of outage entirely and doesn't
substitute for everyday-outage volume.

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
