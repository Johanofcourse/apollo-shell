# Apollo Shell Roadmap

## Phase 1: Foundation (Done)
- [x] FPL data collection (live, 15-minute polling)
- [x] SQLite storage
- [x] Web dashboard (`dashboard.py`, localhost only — not public-facing)
- [x] NWS weather integration (fetching + storing active alerts)
- [x] Basic weather/outage correlation — see
      [`docs/product-review-weather-correlation.md`](./product-review-weather-correlation.md)
      for what's built vs. still proposed here (e.g. confidence
      scoring is not built yet, don't check that off based on this file)

## Phase 2: Multi-Source Intelligence (In Progress)
- [x] Second live Florida utility integrated end-to-end (incident-level
      data, richer than FPL's — real cause, live status, and an actual
      restoration estimate)
- [x] Historical storm data backfilled — 12 storms so far (2020-2025),
      every utility per storm, not just one — kept private, not on GitHub
- [x] Historical severity data layered in from an independent public
      source, cross-checked against our own outage numbers, extended
      to cover winter events (snow/ice/wind-chill) and severe
      thunderstorm/tornado events, not just tropical systems
- [x] Data integrity pass — idempotent writes across every table, so
      re-running any collector can't silently duplicate data
- [ ] Historical storm backfill continuing back to 2018 (Alberto,
      5/27-5/29/2018, is the earliest PSC ESF12 report available) - the
      real target for "storm data acquisition," not an open-ended
      commitment. New storms keep getting added going forward too, as
      PSC publishes reports for them.
- [ ] **Weather-match confidence scoring** (see product review doc —
      proposed, not designed in detail yet). This is confidence that a
      given outage is genuinely weather-related at all (e.g. a match
      against a "Severe" NWS alert is stronger evidence than a match
      against a "Minor" one) - **not** confidence about how long the
      outage will take to fix, which is a separate, later thing (see
      "restoration confidence" in Phase 3). Easy to conflate these two,
      worth keeping distinct on purpose.
- [x] Third live Florida utility integrated end-to-end — Duke Energy,
      same pattern as TECO (incident-level data, live county rollups,
      system alerts, correlation). Its live map blocks a plain
      automated fetch, so this one needed a human driving a real
      browser through devtools to find, same as how TECO's was found
- [ ] More Florida utilities beyond FPL/TECO/Duke, one at a time, same
      proven pattern

## Phase 3: Predictive (Blocked on data, not code)
- [ ] **Restoration-time estimation with its own confidence** ("there's
      roughly a 50% chance this outage is fixed within a day," e.g.) -
      this "restoration confidence" is a different thing from Phase 2's
      weather-match confidence above: that one asks "was this outage
      really caused by weather," this one asks "how long will it take."
      A real answer needs both eventually, but they're separate pieces
      built at separate times, not one feature.
- [ ] Connect live data to the historical storm dataset — e.g. "current
      live weather severity resembles what we saw during Storm X in
      this county, which took about Y hours to restore." Explicitly
      deferred, not just unstarted: needs (1) running the same
      severity extractors against live weather alert text, not just
      historical NOAA narratives; (2) a real decision on querying
      across separate storm databases (12 so far) vs. consolidating
      them into one; (3) enough live volume that "closest historical match" is
      more than 1-2 data points per county. Revisit once Phase 3's
      data-volume bar (below) is actually met.

This phase is **not a normal engineering task with a schedulable
timeline.** It requires enough accumulated real outage-duration data
before an estimate would mean anything — no amount of additional code
shortens that. Rough real numbers as of this writing: FPL alone is
producing on the order of dozens of resolved outages per day live, so
"enough for a first rough look" is more like weeks, "enough to trust"
is more like a month or two, and that's still only one season's worth
of conditions. Historical storm data (12 storms so far, 2020-2025)
covers a different, rarer category of outage entirely and doesn't
substitute for everyday-outage volume — and even across 12 storms, most
individual counties only show up in a handful of them, so it's still a
thin sample per county, not something to treat as a reliable average yet.

## Phase 4: Public-Facing Query Layer (Future consideration, not started)
- [ ] Let people query the **derived/aggregated** data (historical
      severity vs. outage duration, weather-match confidence,
      eventually restoration confidence) - never the raw live utility
      feeds themselves. The raw feeds are the least valuable and riskiest
      thing to expose (anyone motivated enough could find TECO's, Duke's,
      or JEA's live map backend the same way we did, with nothing but a
      browser's dev tools); the accumulated, cleaned, cross-checked
      historical comparison is the actual hard-won asset, and it's safer
      to show precisely because it can't just be re-scraped in an
      afternoon.
- Gated on all three of:
  1. Historical storm backfill reaching 2018 (Phase 2)
  2. Weather-match confidence scoring actually built, not just proposed
     (Phase 2)
  3. Enough live volume for restoration confidence to mean anything
     (Phase 3's data-volume bar)
- Not scoped in detail yet - revisit once the above are real, not before.

## Phase 5: Scale (Open question — not yet committed)
- [ ] More utility integrations beyond Florida

  Bigger than it sounds: a different state means a different utility
  regulator, a different NWS forecast region, and the Florida-county
  validation logic built into the historical importer would need to
  generalize. Worth treating as its own design decision when it comes
  up, not an incremental add.

## Explicitly not planned
- **Any public pass-through of the raw live utility feeds** (FPL, TECO,
  Duke, or any future one) - never planned, not up for reconsideration.
  These feeds are undocumented/reverse-engineered, not official public
  APIs, and re-publishing them at any real scale is a different (and
  worse) risk than using them for our own private analysis. See Phase 4
  above for what *is* now under real consideration - the derived,
  cleaned, aggregated layer, not this.
