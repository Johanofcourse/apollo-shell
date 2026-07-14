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
      automated fetch, so this one needed a human working through the
      live site directly to find, same as how TECO's was found
- [x] Fourth live Florida utility integrated end-to-end — JEA
      (Jacksonville Electric Authority). A genuinely different vendor
      than TECO/Duke (Kubra's "Storm Center" product, not an Azure/Apigee-
      hosted API). JEA's feed is a county-rollup
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
- [x] **Dashboard load time fixed with a correlation cache.** Every page
      load was recomputing `find_correlations()`/`find_teco_correlations()`/
      `find_duke_correlations()`/`find_jea_correlations()` from scratch -
      each one nested-loops its *entire* raw history (tens of thousands
      of rows once `outages`/`teco_incidents`/`duke_incidents` grew past
      a few weeks of 15-minute polling) against every weather alert in
      plain Python. Measured at ~34s combined on 2026-07-12. Since the
      underlying data only actually changes once per poll cycle but the
      page auto-refreshes every 60s, a short-TTL (5 min) in-memory cache
      in `dashboard.py` cut nearly every reload down to ~0.05s -
      deliberately the smaller, lower-risk fix over rewriting the
      matching into SQL, which would fix the root cause but risks a
      subtle correctness difference from the current county-normalizing
      logic. Worth revisiting if the raw tables keep growing indefinitely
      (they do, by design - a fresh row per county/incident every cycle
      forever) since the *cold*-cache load time keeps getting worse too.
- [x] **Decoded Duke's/TECO's incident ID formats, then made Duke's
      readable.** Duke's `incident_id` (e.g. `20260712000423`) turned out
      to be literally `YYYYMMDD` + a 6-digit per-day sequence number -
      confirmed directly against real first-seen dates, not guessed.
      Since the row's own "Started" column already shows that date, the
      dashboard now shows just `Incident #423` - the one part of the ID
      that's actually new information. TECO's `incident_id` (e.g.
      `A202619308291`) does *not* decode to a date - checked its growth
      rate against real data (~100,000/10 days) and it's almost certainly
      TECO's shared enterprise ticket sequence, not anything outage-
      specific - so it's left exactly as TECO sends it rather than
      faking a translation. New `_incident_label()` Jinja filter in
      `dashboard.py`, detected by shape (14 digits, first 8 a real
      calendar date) rather than utility name, so it stays correct if
      either format ever changes. 6 new tests.
- [x] **Per-incident detail lookup (`/incident`)**, reached by clicking
      any row in the four "Currently Open"/"Recently Resolved" tables -
      not something meant to be reached by typing an id from memory.
      TECO/Duke have a real `incident_id`, so one id finds everything on
      file for it: every lifecycle episode plus the *full raw snapshot
      timeline* (both tables log a fresh row every poll cycle while
      active, so status/cause/customer-count/ETR changes over time are
      already sitting in the data, not just a start/end pair). FPL/JEA
      never give us a discrete incident identity, only a county-level
      rollup, so a specific occurrence there is identified by
      `(county, start_time)` instead - the exact natural key their own
      `outage_events`/`jea_outage_events` unique index already enforces.
      JEA's history additionally sums across every ZIP in the county per
      timestamp (its raw snapshots are per-ZIP, the lifecycle is per-
      county), matching the same aggregation `sync_jea_outage_events()`
      already does. New `OutageDatabase.get_teco_incident_detail()` /
      `get_duke_incident_detail()` / `get_fpl_outage_detail()` /
      `get_jea_outage_detail()`, a new `templates/incident.html`
      (plain, internal-tool style, not the Artifact's design language -
      this is a dashboard.py feature, not a public-facing one). This is
      also the same raw data Phase 3's restoration-confidence idea would
      eventually train on - not that model itself (still blocked on data
      volume, not code), just the querying layer for individual past
      incidents. 6 new tests.
- [x] **Fixed a real over-counting bug in FPL's/JEA's weather correlation.**
      Asked to explain a dashboard row ("Nassau: 1244 correlated outages,
      Rip Current Statement ×280...") led to checking the actual query -
      `find_correlations()`/`find_jea_correlations()` matched *every* raw
      poll snapshot against active weather alerts, with no filter on
      whether an outage was actually happening. Both FPL's and JEA's raw
      tables log a fresh row every 15-minute cycle for every county/ZIP
      regardless of whether anything was wrong, so "a weather alert was
      active while nothing was happening" was counting as a "correlated
      outage." Checked directly against the real data before fixing:
      inflating FPL's match count by ~59% (18,151 -> 7,495) and JEA's by
      ~84% (596 -> 97, proportionally worse since JEA's ZIP-level polling
      logs even more "nothing happening" rows per real outage). TECO/Duke
      were never affected - their feeds only ever report actively-open
      incidents, so no zero-customer rows exist to leak in. Fixed with a
      one-line `WHERE customers_out > 0` filter in each query - also
      nearly halves FPL's correlation compute time as a side effect
      (fewer rows to scan). 4 new tests proving a zero-customer snapshot
      is excluded and a real one still matches, for both FPL and JEA.
      Separately flagged and deferred: these correlation counts are still
      all-time-since-April-8-2026 with no rolling window, which will keep
      growing indefinitely and needs its own real design pass eventually
      (see the windowed-correlation entry directly below, done the same
      night) - this fix only addressed the over-counting on its own.
- [x] **Windowed correlation (7/30-day toggle) + distinct-alert
      counting**, resolving the unbounded-window problem flagged above.
      Two real, separate problems, fixed together: (1) the correlation
      tables were all-time since the poller first started (2026-04-08),
      no rolling window, so they'd only grow less meaningful forever;
      (2) even bounded, the alert-type tally was counting every matched
      *(outage-snapshot, alert) pair*, not anything a person would call
      "N alerts" - a real dashboard row showed "Air Quality Alert x190"
      for what was actually a handful of distinct alerts, re-counted
      once per 15-minute poll cycle each one happened to overlap.
      `find_correlations()`/`find_teco_correlations()`/
      `find_duke_correlations()`/`find_jea_correlations()` all gained a
      `days=` parameter (`None` preserves the old all-time behavior for
      any caller that doesn't ask for a window); `correlation_summary()`/
      `teco_correlation_summary()`/`duke_correlation_summary()` now
      count *distinct* alert_ids per event type and distinct outage/
      incident snapshots, not raw match pairs. Dashboard gets a `?window=
      7|30` toggle (default 30) in the header, each correlation section
      labeled with its actual window ("Weather / Outage Correlation
      (FPL) — last 30 days"), correlation cache keyed by window since
      7-day and 30-day results genuinely differ. Verified against real
      data: Nassau's Heat Advisory tally went from a meaningless 926 (or
      482 post over-count-fix) down to a real, readable 16. 8 new tests
      covering the distinct-counting logic and the window boundary
      itself (old data excluded, recent data included).
- [x] **Same-day follow-up: `confidence_breakdown` had the identical bug,
      missed by the fix above.** Caught while doing an unrelated color
      pass on the dashboard's buttons - the combined statewide KPI strip
      still showed "low x27118" even after `alert_types`/`outage_count`
      were fixed. Confidence is a pure function of the alert's own
      event_type + severity (`weather_match_confidence()`), not of which
      outage/incident it happened to match, so it needed the exact same
      per-alert deduplication, not its own separate running count.
      `correlation_summary()`/`teco_correlation_summary()`/
      `duke_correlation_summary()` in `correlate.py` now derive
      `alert_types` *and* `confidence_breakdown` from one shared
      deduplicated `matched_alerts` map per county, rather than
      incrementing two separate counters per raw match.
      `dashboard.py`'s `_combine_confidence_breakdowns()` (the combined
      KPI strip, a separate function, not something `correlate.py` could
      fix on its own) had the identical flaw and got the same fix,
      reusing `correlate.py`'s own `_alert_identity()` rather than
      re-deriving the same synthetic-key logic in a second place.
      Verified against real data: combined statewide confidence went
      from "low x27118" to "high x73, medium x222, low x75." 1 new test.
- [x] **Dashboard control buttons made real UI, not text links.** The
      county-history link and window toggle used to live as plain
      accent-colored text inside the small `.meta` subtitle line - easy
      to miss entirely. Pulled into their own `.controls-strip`: real
      pill-shaped buttons with a drop shadow and a subtle hover lift,
      and the window toggle as a proper segmented control (active choice
      filled in, inactive one a plain link). Also swapped the orange
      accent color used for buttons/navigation links to the same pink
      (`#ff1f8f`) already used in the Artifact design sandbox, after the
      original orange-on-dark-panel combination read as an unintended
      resemblance to an unrelated brand - the orange itself is kept for
      severity/confidence indicators (`pct-bad`, `badge-high`, the
      confidence bar's low segment), which are a different, deliberately
      unrelated color system.

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
      or JEA's live map backend the same way we did); the accumulated,
      cleaned, cross-checked historical comparison is the actual
      hard-won asset, and it's safer to show precisely because it
      can't just be re-scraped in an afternoon.
- Gated on all three of:
  1. [x] Historical storm backfill reaching 2018 (Phase 2) - done
  2. [x] Weather-match confidence scoring actually built, not just
     proposed (Phase 2) - done
  3. [ ] Enough live volume for restoration confidence to mean anything
     (Phase 3's data-volume bar)
- Not scoped in detail yet - revisit once the above are real, not before.
- [x] **First real concept for the actual public-facing page, built
      2026-07-12** (separate Artifact, not the ops-dashboard-redesign
      sandbox - a genuine pivot in what the design represents, so it got
      its own URL/favicon rather than overwriting that one). Combines
      the map/hero (kept, including the live per-county verdict - see
      the resolved decision above), the Heat This Month panel, and the
      Storm History section into one real page layout, built on real
      live data (51 counties with live coverage, real customer/incident
      counts, real all-time confidence patterns) rather than
      placeholders. Deliberately dropped the ops-style telemetry
      sidebar (by-utility breakdown, statewide confidence bars, top
      alert types, worst %, longest open outage) from this version -
      that's internal monitoring language that doesn't answer a real
      visitor's question, not something to carry over just because it
      existed in the ops sandbox. Ends with an honest footer stating
      plainly what the page does and doesn't show (derived patterns,
      not a replay of any utility's raw live feed) rather than leaving
      the omission unexplained. Still just a design concept - nothing
      here is a real route in the app yet, and the test/prod
      environment prerequisite noted above hasn't been started.
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
- [ ] **Real test/production environment split - not started, and needed
      before any public page ships, not after.** Right now there is no
      "production" to separate a test environment from: the app runs as
      a Flask *development* server (Flask's own warning: "do not use in
      a production deployment") on a personal laptop via launchd - fine
      for an internal tool, not something to point real public traffic
      at. Before "ship the whole shebang" makes sense, this needs: a
      real host (resolves the long-open "Apollo Sentinel hosting
      decision" - cloud VM vs. the old Acer laptop, still undecided), a
      real WSGI server (gunicorn or similar, not the dev server), and
      only then a genuine staging/test copy separate from whatever counts
      as production - in that order, since "test vs. prod" is meaningless
      without a real prod to split from first.
- [x] **Resolved: the live per-county verdict map (and hero KPIs) are OK
      to include in the public-facing page - a real decision, not an
      assumption.** Raised as an open question (2026-07-12): does a
      derived per-county verdict count as safe "derived/aggregated" data
      like heat/storm-history, or does it edge toward the live-feed
      exposure explicitly ruled out under "Explicitly not planned" below?
      User's call, same day: keep it - "if someone is shrewd enough
      they'll realize we are collecting data from a bunch of outage
      maps, but either way this info is necessary for the user." Accepts
      that a sufficiently attentive visitor could infer the site
      aggregates several utilities' outage maps; the verdict itself is
      still a derived label, not a raw feed pass-through, and the
      product judgment is that the value to a real user outweighs that
      inference risk. This does NOT reopen "Explicitly not planned"
      below (raw feeds themselves stay off-limits, permanently) - it
      specifically resolves the narrower derived-verdict question.
- [x] **Checked whether there's a real live-data geographic gap in
      Florida (2026-07-12) - there is, and it's exactly the Panhandle.**
      Cross-referenced the master Florida county list against every
      county that's ever appeared in any of the 4 live sources' raw
      tables, normalized for spelling drift - this took two passes to
      get right. First pass (punctuation-only normalization) falsely
      flagged St. Lucie as missing ("St Lucie" in FPL's own raw feed vs.
      "ST. LUCIE" canonical). Second pass still falsely flagged DeSoto
      (FPL's raw feed genuinely spells it "De Soto," two words, vs.
      "DESOTO" canonical - confirmed directly against the live feed, not
      assumed) until normalization also stripped internal spaces. Both
      are the same class of bug that hid Miami-Dade in the historical
      importer - a spelling/spacing mismatch masquerading as a real gap.
      Final, fully-verified result: exactly 10 of 67 counties have zero
      live coverage from any of our 4 sources, and all 10 are contiguous
      Florida Panhandle counties (Escambia, Santa Rosa, Okaloosa, Walton,
      Holmes, Washington, Calhoun, Jackson, Gadsden, Liberty) - no
      outlier elsewhere once the false positives were cleared. Also
      independently confirmed the main FPL feed itself (the one we
      already integrate) has exactly 35 counties and genuinely never
      includes any Panhandle county - ruling out "maybe it's our own
      parsing bug" before concluding a separate system is needed.
      Strongly suspected to be Gulf Power's former territory (merged
      into FPL corporately in 2021, likely still runs its own separate
      live outage-map system) - real lead found 2026-07-12: FPL
      maintains a distinct regional map at fplmaps.com/northwest.html,
      confirmed to reference its own separate JS/data bundle
      (`/content/dam/fplmaps/power-tracker/northwest/...`), not the main
      site's. The actual data endpoint sat behind real Incapsula/Imperva
      bot protection - same class of wall TECO/Duke needed a human
      working through the live site directly to get past. Found the
      same night, by the user, spotting the one real request among
      everything else the page loads (the real payload was tiny,
      ~7KB, easy to overlook next to the page's much larger assets):
      `fplmaps.com/northwest/feeds/CountyOutages.json`, needing a
      `Referer: https://www.fplmaps.com/northwest.html` header - same
      JSON shape as the main feed, confirmed by fetching it directly.
- [x] **FPL Northwest ("Panhandle") feed integrated - the fifth utility
      source, though not tracked as a separate one.** Since Gulf Power
      merged into FPL corporately in 2021, this is genuinely the same
      real utility as the one we already integrate, just a second
      technical data source FPL never consolidated into its main feed -
      so it's combined into the existing FPL pipeline
      (`get_combined_fpl_records()` in `fetch_fpl_outages.py`) rather
      than given its own tables/correlation function/dashboard section
      the way TECO/Duke/JEA were. Zero new schema needed - same
      `outages`/`outage_events` tables, same `find_correlations()`, same
      dashboard section, all already utility-name-keyed, not table-per-
      source. Covers Escambia, Santa Rosa, Okaloosa, Walton, Holmes,
      Washington, Jackson, and Bay - confirmed directly against the real
      response, closing 8 of the 10 gap counties above. Three real
      Panhandle counties (Calhoun, Gadsden, Liberty) still have zero live
      coverage - likely a smaller rural co-op's territory, not yet
      found; the county-coverage check now shows exactly these 3 and
      nothing else. The Panhandle feed is treated as a bonus on top of
      the main one - if it's ever unset or fails, `get_combined_fpl_records()`
      still returns the main feed's records rather than failing the
      whole outage cycle. 6 new tests
      (`tests/test_fetch_fpl_outages.py` - first direct unit tests for
      any FPL parsing logic; used `monkeypatch` to test the combining
      logic without a real network call, the first use of mocking in
      this test suite, since `get_combined_fpl_records()` calls the two
      fetch functions directly rather than accepting injected data the
      way JEA's zip-county cache did). Verified against real live data
      end to end before calling it done: ran one real cycle against the
      live database (not just tests), confirmed all 8 Panhandle counties
      appear in the raw snapshot table, restarted both the launchd
      poller and the dashboard to pick up the new code, re-ran the
      county-coverage check live and watched it drop from 10 missing
      counties to 3.
- [x] **City of Tallahassee integrated as a fifth live utility** -
      found via a plain Esri ArcGIS REST endpoint (esriemcs.com), no bot
      protection. Incident-level shape like TECO/Duke (own
      `tallahassee_incidents`/`tallahassee_incident_events` tables), plus
      a real 5-zone sub-county "region" field unique to this source. Real
      bug caught before shipping (not by a crash): the captured query URL
      pointed at ArcGIS layer 1 (a boundary polygon), not layer 0 (the
      actual "Outages" point layer) - looked identical to a valid empty
      response until a real request came back as the wrong shape;
      caught by re-querying the service's own layer list directly. A
      second mapping bug also caught: the zone-name layer numbers its
      rows by internal OBJECTID, not by the digit in each zone's own
      name - same class of silent-join bug as the Miami-Dade/St Lucie/
      DeSoto county-name mismatches.
- [x] **Talquin Electric Cooperative integrated as a sixth live
      utility** - found via a third distinct vendor platform (Siena
      Technologies, cache.sienatech.com). County-rollup shape like
      FPL/JEA (own `talquin_outages`/`talquin_outage_events` tables).
      Confirmed real coverage: Gadsden, Leon, Liberty, Wakulla - closed
      2 of the 3 remaining Panhandle gaps (Gadsden, Liberty).
- [x] **Florida Public Utilities Corporation (FPUC) integrated as a
      seventh live utility, with real per-incident/per-county detail
      added on a follow-up pass.** Found via a fourth distinct vendor
      platform (DataVoice's "Apprise" system, outageentry.com). Built
      first as a single combined-territory tracker (one authoritative
      total across FPUC's whole non-adjacent Florida territory -
      historically Calhoun, Jackson, Liberty, Nassau, Wakulla per PSC
      storm reports), since no per-county breakdown could be found at
      the time despite a real search.

      **Resolved on a follow-up check**: a live outage finally populated
      the response's `markers` array for the first time - it had only
      ever been observed empty before, indistinguishable from "no per-
      county data exists at all." Each marker carries a real lat/lon,
      reverse-geocoded to a real county (confirmed: Liberty) using the
      same lookup Duke's fetch module already uses. Added a real
      incident-level layer (`fpuc_incidents`/`fpuc_incident_events`,
      `find_fpuc_incident_correlations()`) alongside the combined
      tracker, not instead of it - the app's own config says some
      outages are deliberately withheld from markers for privacy, so
      this view is real and useful but can undercount relative to the
      combined total. Verified by replaying the exact real captured
      marker data through the real pipeline (the live outage resolved
      before the code was finished), then properly closed out that real
      incident afterward rather than deleting it.

      **What's still open**: this real per-incident view can undercount
      (privacy-withheld outages), and the "Substation" summary endpoint
      seen in the app's config was still never found. Calhoun's real
      coverage still ultimately rests on the combined total, not a
      guaranteed standalone incident-level reading - worth re-checking
      during a real multi-outage event to see how much of the true
      total the incident-level view actually captures.
- [x] **Peace River Electric Cooperative (PRECO) integrated as an eighth
      live utility** - same Siena Technologies platform as Talquin.
      County-rollup shape like Talquin (own
      `preco_outages`/`preco_outage_events` tables) - closes real
      coverage for Hardee County (previously only a tiny 11-customer FPL
      sliver) plus Brevard, DeSoto, Highlands, Hillsborough, Indian
      River, Manatee, Osceola, Polk, Sarasota (9,885 Hardee customers
      served vs. FPL's 11 - confirms PRECO, not FPL, is the real primary
      utility there).
- [x] **Florida Keys Electric Cooperative (FKEC) integrated as a ninth
      live utility** - a fifth distinct vendor platform (NISC's
      "cloud.coop" product, plain static JSON files on S3/CloudFront, no
      trackingCode/auth needed at all). Closes the real Monroe County
      coverage gap (previously only a tiny 101-customer FPL sliver) with
      FKEC's real 34,475 customers served. County-rollup shape (own
      `fkec_outages`/`fkec_outage_events` tables), always exactly one
      row (Monroe) - confirmed FKEC's entire six-ZIP-code territory is
      genuinely single-county by converting a real coordinate out of
      the map's own ZIP-boundary geometry file (Web Mercator
      projection) to lat/lon and reverse-geocoding it through the same
      FCC Census API `lookup_county()` already used for TECO/Duke/JEA -
      not just assumed from general Keys geography. Real data quirk found
      and handled: summing the six ZIPs' own `numberServed` figures
      doesn't quite match the response's own authoritative `totalServed`
      field (a ~0.6% gap) - `customers_served` uses `totalServed`
      directly rather than a locally re-derived sum.
- [x] **Tri-County Electric Cooperative (TCEC) integrated as a tenth
      live utility** - a sixth distinct vendor platform (a custom
      Microsoft IIS-hosted build, `outage.tcec.com`, no trackingCode or
      auth needed). Confirmed real territory via the user's own visual
      read of the live map (Jefferson, Madison, Taylor, plus small parts
      of Dixie, Lafayette, and Leon) after a candidate boundary
      shapefile (`Counties.zip`, a real Esri `.shp`/`.dbf` archive) turned
      out to be the map's background county-line reference layer, not
      real service territory - it listed 10 names including two real
      Georgia counties (Brooks, Thomas), a dead end for real coverage.
      Built as a combined-territory tracker (own
      `tcec_outages`/`tcec_outage_events`
      tables, always exactly one row) using the real, honestly-labeled
      county list rather than a fake placeholder - same pattern FPUC's
      original tracker used before its incident-level layer existed.
      Real per-region detail lives in a confirmed sibling endpoint,
      `outagePolygons.json`, but it's only ever been seen empty (zero
      active outages during discovery) - its real field shape, and the
      per-county correlation layer that would follow from it, is a
      known, deliberate gap until a genuine outage populates it, same
      "wait for a real event" situation FPUC's `markers` array was in
      before one finally did.
- [x] **Escambia River Electric Cooperative (EREC) integrated as an
      eleventh live utility** - identical vendor platform to TCEC (same
      `outageSummary.json`/`outagePolygons.json` shape, same Microsoft
      IIS server), just hosted off a raw IP:port over plain HTTP rather
      than a domain/TLS. Real territory confirmed directly by the user:
      Escambia and Santa Rosa counties (a clean two-county case, unlike
      TCEC's partial-coverage counties). Built the same way as TCEC -
      combined-territory tracker (own `erec_outages`/`erec_outage_events`
      tables, always exactly one row), same known correlation
      limitation, same "wait for a real event" gap on `outagePolygons.json`
      (confirmed to exist, seen empty every time so far). Found and built
      entirely under an explicit "take it easy on the requests"
      constraint following the Siena/Talquin/PRECO incident - only
      fetched URLs the user had already directly confirmed,
      no candidate-filename guessing this time.
- [x] **Live `/county` lookup page added** - an operator picks one of
      Florida's 67 real counties and sees everything currently relevant
      to it in one place: real per-county outages from every source
      that actually reports per-county (including FPUC's real
      incident-level markers, not just its combined total), weather
      alerts active right now that name the county (heat-type ones
      visually flagged), and - shown in a clearly separate group, never
      blended in - combined-territory sources (FPUC's original combined
      view, TCEC, EREC, CHELCO) whose multi-county label happens to
      mention it.
      Deliberately live/current-status only; `/history` remains the
      place for real multi-year storm data per county. Matching logic
      reuses `correlate.py`'s existing `_county_in_alert()` substring
      check for both real single-county rows and combined-territory
      labels - verified first that no two of Florida's 67 real county
      names are substrings of each other, so one matching function is
      safe for both cases.
- [x] **Choctawhatchee Electric Cooperative (CHELCO) integrated as a
      twelfth live utility** - identical vendor platform to TCEC/EREC
      (same `outageSummary.json`/`outagePolygons.json` shape, same
      Microsoft IIS server), hosted off a raw IP:port over plain HTTPS.
      Real territory confirmed directly by the user: Santa Rosa,
      Okaloosa, Walton, and Holmes counties - closes the Holmes County
      gap noted in prior sessions.
      Built the same way as TCEC/EREC - combined-territory tracker
      (own `chelco_outages`/`chelco_outage_events` tables, always
      exactly one row), same known correlation limitation, same "wait
      for a real event" gap on `outagePolygons.json` (confirmed to
      exist, seen empty every time so far). Found and built entirely
      under the same "take it easy on the requests" constraint - only
      fetched URLs the user had already directly confirmed.

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
