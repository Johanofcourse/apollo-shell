# Apollo Shell Roadmap

## Phase 1: Foundation (Done)
- [x] FPL data collection (live, 15-minute polling)
- [x] SQLite storage
- [x] Web dashboard (`dashboard.py`, localhost only — not public-facing)
- [x] NWS weather integration (fetching + storing active alerts)
- [x] Basic weather/outage correlation — see
      [`docs/product-review-weather-correlation.md`](./product-review-weather-correlation.md)
      for what's built vs. still proposed (weather-match confidence
      scoring and its display layer are now built too, see Phase 2
      below; restoration confidence is the remaining proposed piece)

## Phase 2: Multi-Source Intelligence (In Progress)
- [x] Second live Florida utility integrated end-to-end (incident-level
      data, richer than FPL's — real cause, live status, and an actual
      restoration estimate)
- [x] Historical storm data backfilled — 17 storms so far (2018-2025),
      every utility per storm, not just one — kept private, not on GitHub
- [x] Historical severity data layered in from an independent public
      source, cross-checked against our own outage numbers, extended
      to cover winter events (snow/ice/wind-chill) and severe
      thunderstorm/tornado events, not just tropical systems
- [x] Data integrity pass — idempotent writes across every table, so
      re-running any collector can't silently duplicate data
- [x] Historical storm backfill reached 2018 (Alberto, 5/27-5/29/2018,
      the earliest PSC ESF12 report available) - the "storm data
      acquisition" target is now complete: 17 storms, 2018-2025, every
      hurricane/tropical storm/major weather event PSC has a report
      series for. New storms keep getting added going forward too, as
      PSC publishes reports for them - not a one-time milestone.
- [x] **Weather-match confidence scoring** (see product review doc for
      the full writeup). Confidence that a given outage is genuinely
      weather-related at all - driven primarily by whether the matched
      alert's event type could plausibly cause an outage (a "Severe"
      Rip Current Statement never outranks a "Moderate" Tornado
      Warning), with NWS's own severity field as a secondary modifier.
      Output is a high/medium/low label, not a numeric percentage. This
      is **not** confidence about how long the outage will take to fix,
      which is a separate, later thing (see "restoration confidence" in
      Phase 3) - easy to conflate these two, worth keeping distinct on
      purpose. Display layer (confidence bars + severity badges +
      a KPI summary strip in `dashboard.py`) is now built too.
- [x] Third live Florida utility integrated end-to-end — Duke Energy,
      same pattern as TECO (incident-level data, live county rollups,
      system alerts, correlation). Its live map blocks a plain
      automated fetch, so this one needed a human driving a real
      browser through devtools to find, same as how TECO's was found
- [x] Fourth live Florida utility integrated end-to-end — JEA
      (Jacksonville Electric Authority). A genuinely different vendor
      than TECO/Duke (Kubra's "Storm Center" product, not an Azure/Apigee-
      hosted API), found by reading the outage-map page's own JS bundle
      rather than live browser devtools. JEA's feed is a county-rollup
      shape like FPL's (not incident-level like TECO/Duke), but reports
      by ZIP code, not county - resolved via the same FCC reverse-geocode
      TECO's incidents already use (each ZIP's own bounding-box center,
      not a separate ZIP crosswalk dataset), cached per process since
      JEA's service-area ZIPs don't change between polls. Also the first
      source with a labeled confidence on its own restoration estimate
      (`etr_confidence`), not just an ETR value. Own dedicated tables
      (`jea_outages`, `jea_outage_events`), not shared with FPL's - see
      `apollo_shell/fetch_jea_outages.py` for why (FPL's dashboard
      section/correlation read with no utility filter, so sharing would
      have silently mixed JEA rows into "FPL").
- [ ] More Florida utilities beyond FPL/TECO/Duke/JEA, one at a time,
      same proven pattern
- [x] **County storm-history query tool**, internal only (see
      `apollo_shell/consolidate_historical.py` and the `/history` route
      in `dashboard.py`). The 17 per-storm databases stay untouched,
      independently-verified source of truth; a new, separate,
      regeneratable `historical_consolidated.db` pulls just the
      derived/summary data (outage events + independent NOAA severity
      readings, not raw per-utility snapshots) into one queryable store.
      Pick a county, see every storm that has real data for it, side by
      side with the independent severity reading for the same
      county/storm. Real finding along the way: historical TECO/Duke
      data was never in utility-specific tables at all - PSC's
      county-level reports cover 40+ Florida utilities per storm
      (co-ops, municipals, not just the three we track live), all under
      one shared `outage_events` shape. This is explicitly an internal
      tool right now, not a public feature - see Phase 4 for what
      actually opening it up would require.
- [x] **`/history` now lists every storm explicitly, including the ones
      with nothing for a given county** (2026-07-12) - it used to just
      silently omit storms with no report, which blurred "no data for
      this storm" into "confirmed unaffected." Every county's page now
      shows all 17 storms, with an honest "no report for this storm in
      our data" row for the gaps instead of leaving them out. Verified
      against the real numbers: Miami-Dade correctly shows 12 of 17,
      Duval 13 of 17. Deliberately logic-only, not a visual redesign -
      the internal tool stays plain and data-dense on purpose; the
      polished version of this same idea (map-click county selection,
      plain-language timeline) was prototyped separately in the Artifact
      design sandbox as a model for an eventual real public-facing page,
      not merged into this internal one.
- [x] **Pipeline error alerting**, tied to the dashboard. Every fetch
      cycle already had its own try/except so one source failing
      couldn't crash the others, but the only record was a print() line
      into a growing text log nobody was watching. Now every caught
      failure lands in a real `pipeline_errors` table, with a per-source
      health status (healthy / warning / critical - critical means 3+
      failures in the last hour, i.e. failing nearly every 15-minute
      cycle right now, not just a one-off blip) surfaced as a strip at
      the top of the dashboard. Deliberately doesn't cover the FCC
      geocoding timeouts found while building this (TECO/Duke's lat/lon
      -> county lookups) - that failure already degrades gracefully
      three layers deep with no database handle in scope, not worth the
      refactor unless it gets a lot worse.
- [x] **Real bug hunt following the Miami-Dade fix, plus a first real
      test suite.** Asking "any other counties missing, any other odd
      parses" turned up two more real, if minor, issues: `weather_alerts`
      had 5 legacy rows with a NULL `alert_id` (from before that field
      existed) that could bypass the uniqueness guard entirely if it
      ever happened again - fixed with a deterministic synthetic-ID
      fallback in `fetch_weather.py`. `storm_severity.py`'s ice-detection
      regex had an unescaped `.` in `three.quarters`, matching any
      character instead of just a hyphen/space - fixed, verified zero
      effect on the 17 storms' already-stored data. Also added:
      `apollo_shell/check_data_integrity.py`, a reusable script
      formalizing the ad hoc SQL sanity checks this project has
      repeatedly done by hand (impossible values, bad durations,
      duplicate keys, cross-storm anomalies, county coverage, pipeline
      health, consolidated-db sync) into one command; and a real pytest
      suite (42 tests, `tests/`) - the project had zero automated tests
      before this. Includes a direct regression test for the replay bug
      above, verified to actually catch it (reverted the fix, watched
      3/4 tests fail, restored it, watched them pass).
- [x] **"Heat this month" dashboard strip.** Heat Advisory/Excessive Heat
      Warning were already flowing into `weather_alerts` like any other
      NWS alert, just never surfaced as their own thing -
      `OutageDatabase.get_heat_advisory_summary()` counts distinct
      calendar days (not raw rows - NWS splits one advisory into several
      zone-specific rows per day) with an active advisory this month,
      plus a live "active now" badge. First step toward the user's
      actual ask: a public-facing heat-advisory view, not just internal
      awareness - see Phase 4.
- [x] **Heat zone drill-down + plain-English timestamps.** Two small
      follow-ups after actually looking at the dashboard: the "active
      now - N zones" badge is now a link to a new `/heat` page listing
      the actual NWS forecast zones under an active advisory (with a
      plain-language explainer of what a "zone" is, since it's finer-
      grained than a county); and every raw ISO timestamp shown on the
      dashboard/history pages (`2026-07-02T01:19:57.483375`) now renders
      through a `humanize` Jinja filter as prose (`July 2, 2026, 1:19
      AM`) instead of the raw machine format.

## Phase 2.5: Dashboard Redesign (In progress — design exploration)
- [x] Visual direction settled on, explored entirely in an isolated
      Claude Artifact sandbox (never connected to live data, never
      touching the real app) before any porting work: a flat,
      typographic, Swiss/wayfinding-instrumentation dark-mode look -
      signal colors (magenta/yellow/lime/cyan) coded as status
      indicators, separate from any single decorative accent hue.
      Includes a telemetry sidebar, a plain-English explainer for the
      confidence label, and the full county log (all 22 tracked
      counties) sorted worst-verdict-first.
- [x] A real Florida county map, colored by verdict - went through
      several iterations (a gauge, a simplified region grid, two
      hand-traced attempts) before landing on actual US Census county
      boundary data, projected and simplified with code rather than
      approximated by eye. Correctly shows multi-part counties (Monroe
      mainland + its real Keys islands, Lee's barrier islands, etc.)
      because the underlying data is real, not guessed.
- [ ] **Design philosophy, explicit and binding on this work:** built
      for an average, non-technical person, not tech-savvy assumptions
      (hover-only reveals, icon-only controls, jargon). A real tension
      already identified - the current sandbox build leans on hover
      (map tooltips, the confidence explainer, expand-for-detail county
      rows), which has no equivalent on a touchscreen. This needs to
      become tap-to-reveal or always-visible before a mobile pass, not
      retrofitted after.
- [ ] Planned interaction, not yet built: clicking a county on the map
      should link/scroll to that same county's row in the county log -
      currently two unlinked views of the same 22 counties.
- [ ] Port the settled design from the Artifact sandbox into the real
      `dashboard.py`/`templates/dashboard.html` - not started. The
      sandbox is exploration only; nothing here is live yet.
- [ ] Eventual mobile browser/app version - explicitly named as the
      harder problem this desktop redesign is meant to prepare for.
      Not scoped yet, deliberately - revisit once the desktop version
      is actually settled and ported.

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
      historical NOAA narratives; (2) enough live volume that "closest
      historical match" is more than 1-2 data points per county.
      Revisit once Phase 3's data-volume bar (below) is actually met.
      The consolidation half of this is now done (see Phase 2.5) - the
      remaining blocker is purely live data volume, not the database
      question anymore.

This phase is **not a normal engineering task with a schedulable
timeline.** It requires enough accumulated real outage-duration data
before an estimate would mean anything — no amount of additional code
shortens that. Rough real numbers as of this writing: FPL alone is
producing on the order of dozens of resolved outages per day live, so
"enough for a first rough look" is more like weeks, "enough to trust"
is more like a month or two, and that's still only one season's worth
of conditions. Historical storm data (17 storms so far, 2018-2025)
covers a different, rarer category of outage entirely and doesn't
substitute for everyday-outage volume — and even across 17 storms, most
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
  1. [x] Historical storm backfill reaching 2018 (Phase 2) - done
  2. [x] Weather-match confidence scoring actually built, not just
     proposed (Phase 2) - done
  3. [ ] Enough live volume for restoration confidence to mean anything
     (Phase 3's data-volume bar)
- Not scoped in detail yet - revisit once the above are real, not before.
- [ ] **Public-facing heat advisory view.** Different risk profile than
      the outage/utility data this whole gate exists for - heat
      advisories are NWS's own already-public alerts, not a reverse-
      engineered utility feed, so this one doesn't carry the same
      re-scraping risk Phase 4's gate is mainly guarding against. Still
      not started; the internal dashboard strip (Phase 2, above) is the
      only piece that exists today.
- [ ] **Natural-language query over the historical/derived data** ("dumb
      AI" - user's term, h/t Halo's dumb AIs: narrow, task-scoped,
      no pretense of general intelligence) - a cheap LLM (user mentioned
      DeepSeek for cost) answering plain-English questions against the
      accumulated historical/derived tables, not the live raw feeds.
      Purely conceptual right now - no design work done yet on scope
      (what it's allowed to query), grounding (how it avoids inventing
      numbers not actually in the data), or cost/abuse controls for a
      public-facing LLM endpoint. Revisit once Phase 4's gates are met.

## Phase 5: Scale (Open question — not yet committed)
- [ ] More utility integrations beyond Florida

  Bigger than it sounds: a different state means a different utility
  regulator, a different NWS forecast region, and the Florida-county
  validation logic built into the historical importer would need to
  generalize. Worth treating as its own design decision when it comes
  up, not an incremental add.

## Explicitly not planned
- **Any public pass-through of the raw live utility feeds** (FPL, TECO,
  Duke, JEA, or any future one) - never planned, not up for reconsideration.
  These feeds are undocumented/reverse-engineered, not official public
  APIs, and re-publishing them at any real scale is a different (and
  worse) risk than using them for our own private analysis. See Phase 4
  above for what *is* now under real consideration - the derived,
  cleaned, aggregated layer, not this.
