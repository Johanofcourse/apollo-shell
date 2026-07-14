# Apollo Shell Roadmap

## Phase 1: Foundation (Done)
- [x] FPL data collection (live, 15-minute polling)
- [x] SQLite storage
- [x] Web dashboard (localhost only — internal tool, not public-facing)
- [x] NWS weather integration (fetching + storing active alerts)
- [x] Basic weather/outage correlation — see
      [`docs/product-review-weather-correlation.md`](./product-review-weather-correlation.md)
      for what's built vs. still proposed

## Phase 2: Multi-Source Intelligence (In Progress)
- [x] Second live Florida utility integrated end-to-end — richer than
      FPL's feed, with a real cause, live status, and an actual
      restoration estimate per incident
- [x] Historical storm data backfilled — 17 storms so far (2018-2025),
      every utility per storm, not just the ones we track live — kept
      private, not on GitHub
- [x] Historical severity data layered in from an independent public
      source, cross-checked against our own outage numbers, covering
      both tropical systems and winter/severe-thunderstorm events
- [x] Data integrity pass — idempotent writes across every table, so
      re-running any collector can't silently duplicate data
- [x] Historical storm backfill reached all the way back to 2018 —
      the earliest report available from the storm-report source this
      project uses. 17 storms, every major Florida weather event with
      a report on file. New storms keep getting added as they happen.
- [x] **Weather-match confidence scoring.** A high/medium/low label for
      whether an outage is genuinely weather-related at all, driven by
      whether the matched alert type could plausibly cause one - not a
      numeric percentage. Deliberately distinct from "restoration
      confidence" (how long a fix will take, a separate later idea).
      Shipped with a real display layer on the dashboard.
- [x] Third live Florida utility integrated end-to-end (Duke Energy),
      same richer incident-level pattern as the second
- [x] Fourth live Florida utility integrated end-to-end (JEA). A
      genuinely different data source than the first three - reports
      by ZIP code instead of county, and is the first source to carry
      its own labeled confidence on its restoration estimate, not just
      a raw estimate. Own dedicated tables, kept separate from FPL's so
      the two never get silently blended together.
- [ ] More Florida utilities beyond the first four, one at a time,
      same proven pattern
- [x] **County storm-history query tool**, internal only. Pick a
      county, see every one of the 17 storms with real data for it,
      side by side with an independent severity reading for the same
      storm. Along the way: found that two of the live utilities'
      historical numbers were sitting under a shared, generic table
      the whole time, not their own - a real structural finding, not a
      bug. Internal tool only for now - see Phase 4 for what actually
      opening this up would require.
- [x] **County history now lists every storm explicitly, including the
      ones with nothing for a given county**, instead of silently
      omitting them - which used to blur "no data for this storm" into
      "confirmed unaffected." Verified against real numbers for a
      couple of test counties. Deliberately logic-only, not a redesign.
- [x] **Pipeline error alerting**, tied to the dashboard. Every
      collection cycle already isolated its own failures so one
      source going down couldn't take the others with it, but the only
      record was a line in a growing log file nobody was watching. Now
      every failure lands in a real health-status table, surfaced as a
      strip at the top of the dashboard (healthy / warning / critical).
- [x] **Real bug hunt following an earlier data-gap fix, plus a first
      real test suite.** Asking "what else might be quietly wrong"
      turned up a couple more small, real issues, both fixed. Also
      added: a reusable data-integrity check script formalizing every
      ad hoc sanity check this project had done by hand, and a real
      automated test suite (the project had zero before this).
- [x] **"Heat this month" dashboard strip.** Heat advisories were
      already flowing in like any other weather alert, just never
      surfaced as their own thing - added a simple monthly count plus
      a live "active now" badge. First step toward the real goal: a
      public-facing heat-advisory view (see Phase 4).
- [x] **Heat zone drill-down + plain-English timestamps.** A page
      listing the actual zones under an active heat advisory, with a
      plain explanation of what a "zone" is; every raw machine
      timestamp on the dashboard now renders as normal prose instead.
- [x] **Dashboard load time fixed with a short-lived cache.** Page
      loads were recomputing every correlation from scratch on every
      visit, measured at over half a minute combined once the data
      grew large enough. A short-lived cache cut that down to a
      fraction of a second, the simpler and lower-risk fix over a
      deeper rewrite.
- [x] **Decoded two utilities' incident-ID formats, made one of them
      human-readable.** One turned out to encode its own date, so the
      dashboard now shows just the meaningful part of the number
      instead of the whole raw ID. The other doesn't decode to
      anything meaningful, confirmed by checking its real growth rate
      - left exactly as sent rather than faking a translation.
- [x] **Per-incident detail lookup**, reached by clicking any row in
      the open/resolved tables - not something meant to be typed from
      memory. Shows the full history for that specific outage,
      wherever the source provides a real identity for one.
- [x] **Fixed a real over-counting bug in weather correlation.**
      Checking a suspicious-looking dashboard number led to finding
      that correlation counts included routine polling snapshots where
      nothing was actually wrong, not just real active outages. Fixed
      with a simple filter; verified the real counts before and after
      - one utility's count dropped by roughly 60%, another's by
      roughly 85%.
- [x] **Added a 7/30-day window toggle, and fixed a related counting
      bug** where one alert firing repeatedly across many polling
      cycles was being counted many times over instead of once. A
      real dashboard number that used to read in the hundreds now
      reads as a small, honest, human-sized count.
- [x] **Caught the identical counting bug in the dashboard's combined
      summary strip**, missed by the fix above since it lived in a
      separate piece of code. Same fix, same verification against real
      numbers.
- [x] **Dashboard control buttons made real UI, not plain text links**,
      plus a color scheme fix after an earlier accent color read as an
      unintended resemblance to an unrelated brand.

## Phase 2.5: Dashboard Redesign (In progress — design exploration)
- [x] Visual direction settled on, explored entirely in an isolated
      design sandbox (never connected to live data, never touching the
      real app) before any porting work: a flat, typographic, dark-mode
      look with a small set of coded status colors.
- [x] A real Florida county map, colored by outcome - went through
      several rough passes before landing on real, publicly available
      geographic boundary data rather than anything hand-drawn.
      Correctly shows multi-part counties (island chains, barrier
      islands) because the underlying data is real.
- [ ] **Design philosophy, explicit and binding on this work:** built
      for an average, non-technical person, not tech-savvy assumptions
      (hover-only reveals, icon-only controls, jargon). A real tension
      already identified - the current sandbox build leans on hover
      interactions that have no touchscreen equivalent, and that needs
      to be resolved before any mobile pass, not retrofitted after.
- [ ] Planned, not yet built: clicking a county on the map should jump
      to that same county's row in the log below - currently two
      unlinked views of the same data.
- [ ] Port the settled design from the sandbox into the real app - not
      started. The sandbox is exploration only; nothing here is live.
- [ ] Eventual mobile browser/app version - explicitly named as the
      harder problem this desktop redesign is meant to prepare for.
      Not scoped yet, deliberately - revisit once the desktop version
      is actually settled and ported.

## Phase 3: Predictive (Blocked on data, not code)
- [ ] **Restoration-time estimation with its own confidence** ("roughly
      a 50% chance this outage is fixed within a day," e.g.) - a
      different thing from Phase 2's weather-match confidence: that one
      asks "was this outage really caused by weather," this one asks
      "how long will it take." A real answer needs both eventually, but
      they're separate pieces built at separate times.
- [ ] Connect live data to the historical storm dataset — e.g. "current
      live weather resembles what we saw in this county during a past
      storm, which took about this long to restore." Explicitly
      deferred, not just unstarted - needs both the right analysis
      approach and a lot more live volume than exists today.

This phase is **not a normal engineering task with a schedulable
timeline.** It requires enough accumulated real outage-duration data
before an estimate would mean anything — no amount of additional code
shortens that. Rough sense of scale: "enough for a first rough look" is
more like weeks of live data, "enough to trust" is more like a month or
two, and that's still only one season's worth of conditions. The 17
historical storms cover a different, rarer category of outage entirely
and don't substitute for everyday-outage volume.

## Phase 4: Public-Facing Query Layer (Future consideration, not started)
- [ ] Let people query the **derived/aggregated** data (historical
      severity vs. outage duration, weather-match confidence,
      eventually restoration confidence) - never the raw live feeds
      themselves. The accumulated, cleaned, cross-checked comparison is
      the actual hard-won asset here, not the raw feeds.
- Gated on all three of:
  1. [x] Historical storm backfill reaching 2018 (Phase 2) - done
  2. [x] Weather-match confidence scoring actually built, not just
     proposed (Phase 2) - done
  3. [ ] Enough live volume for restoration confidence to mean anything
     (Phase 3's data-volume bar)
- Not scoped in detail yet - revisit once the above are real, not before.
- [x] **First real concept for the actual public-facing page**, built
      as a separate design exploration. Combines the map, the Heat This
      Month panel, and the Storm History section into one real page
      layout on real live data rather than placeholders. Deliberately
      drops the internal-monitoring-style detail from the ops version -
      that's language for us, not a real visitor. Ends with an honest
      footer stating plainly what the page does and doesn't show. Still
      just a design concept - nothing here is a live route yet.
- [ ] **Public-facing heat advisory view.** A different risk profile
      than the rest of this gate - heat advisories are already-public
      National Weather Service alerts, not anything we've had to dig up
      ourselves. Still not started; the internal dashboard strip
      (Phase 2, above) is the only piece that exists today.
- [ ] **Natural-language query over the historical/derived data** - a
      cheap, narrow, task-scoped assistant answering plain-English
      questions against the accumulated historical/derived tables, not
      the live raw feeds. Purely conceptual right now - no design work
      done yet on scope, grounding, or abuse controls for a
      public-facing endpoint. Revisit once Phase 4's gates are met.
- [ ] **Real test/production environment split - not started, and
      needed before any public page ships, not after.** Right now there
      is no "production" to separate a test environment from - this
      still runs as a personal, internal tool. Before "ship the whole
      thing" makes sense, this needs a real host, a real production-
      grade server, and only then a genuine staging/test copy separate
      from whatever counts as production.
- [x] **Resolved: the live per-county outcome map (and hero numbers)
      are OK to include in the public-facing page - a real decision,
      not an assumption.** Raised as an open question: does a derived
      per-county outcome count as safe "derived/aggregated" data, or
      does it edge toward the raw-feed exposure ruled out below? The
      call: keep it - the value to a real user outweighs the small risk
      that an attentive visitor infers the site aggregates several
      utilities' data. The outcome itself is still a derived label, not
      a raw feed pass-through. This does not reopen "Explicitly not
      planned" below - raw feeds themselves stay off-limits,
      permanently.
- [x] **Confirmed a real live-data geographic gap in Florida, and
      closed most of it utility by utility.** Cross-referencing the
      full Florida county list against everywhere our live sources
      report data found exactly 10 of 67 counties with zero live
      coverage, every one of them in the Panhandle - no outliers
      elsewhere once a couple of spelling/formatting false alarms were
      cleared (the same class of mismatch that once hid a different
      county in the historical data entirely). Turned out the
      Panhandle runs on a second, separate technical feed from the same
      first utility we already track, closing 8 of the 10 at once once
      folded in. The remaining Panhandle counties were closed one at a
      time as additional Panhandle-area utilities got integrated below,
      down to full coverage.

## Utility coverage
Grew from one utility (FPL) to twelve live sources across this
project's life, each one closing a real, verified county-coverage gap
rather than being added for its own sake. In rough order: a second and
third utility added genuinely richer per-incident detail (real cause,
live status, restoration estimates) that FPL's feed never had. A
fourth added ZIP-level reporting and its own labeled confidence on
restoration estimates. A fifth closed a real Panhandle capital-area
gap, catching and fixing two subtle data-mapping bugs before shipping.
A sixth and eighth closed real rural Panhandle and Central Florida
gaps. A seventh (Florida Public Utilities) started as a single
combined total across its territory and later gained real per-incident
detail once a live outage actually populated it - it still carries a
known, disclosed limitation where some outages are withheld from the
incident-level view for privacy, so the combined total remains the
more complete number for that one. A ninth closed the real Monroe
County (Keys) gap. A tenth and eleventh, on the same underlying data
platform as each other, closed most of the remaining rural Panhandle
gap as combined-territory trackers (one authoritative total across a
multi-county territory, honestly labeled as such rather than guessed
at a fake single-county number). A twelfth, on that same platform,
closed the last standing Panhandle gap.

Every combined-territory source (where one number covers multiple real
counties rather than a true per-county breakdown) is disclosed as such
everywhere it shows up - on the dashboard, in the county lookup tool,
and in its own weather-correlation logic, which is designed to return
empty rather than fake a match it can't actually verify.

- [x] **Live `/county` lookup page added** - pick any of Florida's 67
      real counties and see everything currently relevant to it in one
      place: real per-county outages from every source that actually
      reports per-county, active weather alerts naming that county, and
      - shown in a clearly separate group, never blended in - the
      combined-territory sources whose multi-county label happens to
      cover it. Deliberately live/current-status only; the storm-history
      tool remains the place for real multi-year data per county.

## Phase 5: Scale (Open question — not yet committed)
- [ ] More utility integrations beyond Florida

  Bigger than it sounds: a different state means a different utility
  regulator, a different weather-alert forecast region, and the
  Florida-specific validation logic already built would need to
  generalize. Worth treating as its own design decision when it comes
  up, not an incremental add.

## Explicitly not planned
- **Any public pass-through of the raw live utility feeds** - never
  planned, not up for reconsideration. These are not official public
  APIs, and re-publishing them at any real scale is a different (and
  worse) risk than using them for our own private analysis. See Phase
  4 above for what *is* now under real consideration - the derived,
  cleaned, aggregated layer, not this.
