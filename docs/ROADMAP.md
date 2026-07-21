# Apollo Shell Roadmap

## Phase 1: Foundation (Done, ~2026-04)
Real date range pulled from git history: initial build started 2026-04-01,
live poller running since 2026-04 (the date this project's own copy still
cites for "live tracking began"). Per-item dates from this far back aren't
reliably reconstructable without deeper archaeology than tonight's pass -
this is a real range, not a guess, but not bullet-level precision either.
- [x] FPL data collection (live, 15-minute polling)
- [x] SQLite storage
- [x] Web dashboard (localhost only — internal tool, not public-facing)
- [x] NWS weather integration (fetching + storing active alerts)
- [x] Basic weather/outage correlation — see
      [`docs/product-review-weather-correlation.md`](./product-review-weather-correlation.md)
      for what's built vs. still proposed

## Phase 2: Multi-Source Intelligence (In Progress, 2026-07-02 through 2026-07-17)
Real date range from git history - the bulk of this phase was one
continuous, heavy sprint: 16 commits on 07-02 alone, staying dense
through 07-17. Same caveat as Phase 1 - real range, not bullet-level
precision for the earliest items in this list.
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
- [x] **Real email alerting** for the two sources known not to
      self-heal - fires the moment either has a single failure (more
      sensitive than the standard threshold), one follow-up email on
      recovery, not a repeat every cycle for the whole outage.
- [ ] **Extend real email alerting to every other real source** -
      currently scoped to just the two known-fragile ones. The other
      real sources occasionally hit an ordinary transient blip that
      resolves on its own next cycle, so this needs a sustained-failure
      threshold (still failing next cycle too, not just once) rather
      than the single-failure trigger the two fragile sources use -
      otherwise routine self-healing blips would generate noise. Not
      started - deliberately deferred to just before Phase 6's public
      launch (see there), not sooner.
- [x] **Closed a real blind spot in failure detection itself - shipped
      2026-07-20, for TECO, Duke, Tallahassee, and weather.** These four
      each caught their own network failures internally and returned an
      empty result - indistinguishable from the source legitimately
      having nothing to report, unlike the county-rollup sources
      (FPL/Talquin/PRECO) that always report something for every
      serviced county. A real fetch failure for any of these four could
      vanish completely - not unalerted, never logged to
      pipeline_errors at all. Fix needed no new mechanism: removed the
      internal catch so the real exception reaches the poller's own
      per-utility error logging, which already worked correctly for
      every other source. FPUC's incident view, flagged as a possible
      fifth case, checked out already covered - its existing
      fetch-once-update-both-trackers design (built for an unrelated
      reason) already raises on a real empty response.
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
- [x] **Continuous Integration - shipped 2026-07-19.** GitHub Actions
      now runs the full 562-test suite automatically on every push to
      `main`, no new tests written - just guaranteeing the existing
      suite can't quietly get skipped. Pinned to Python 3.9 specifically
      to match the VM's real production interpreter rather than the
      older local dev version, the same kind of version gap that let
      LWBU's fractional-seconds bug (above) slip past local testing
      before it hit the VM. Deliberately narrow: covers one of the eight
      categories in a full audit sweep (the automated test suite
      itself) - data integrity, live smoke tests, and infra hygiene all
      still need real running access a public repo's CI shouldn't be
      handed, so those stay a manual ask.

## Phase 2.5: Dashboard Redesign (In progress — design exploration, ~2026-07-05 onward)
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

## Phase 3: Predictive (Restoration signal shipped for every utility with real usable data - FPL, TECO, Duke, JEA, and LWBU; the at-risk-counties signal shipped too; only FPUC's still-too-thin sample remains)
- [x] **Weather-based "at-risk counties" signal - shipped 2026-07-21,
      on the public page right below Current Weather Alerts.** Cross-
      references currently active NWS alerts against each county's own
      precomputed historical weather-match confidence tally
      (db.get_historical_confidence_tally() - instant, not a live
      recomputation of historical_confidence_tally() itself) to flag
      "this county has no reported outage yet, but its real history says
      outages here have often followed this kind of alert." A heuristic
      built on this project's own historical correlation strength, not a
      real meteorological or grid-load forecast - same honesty standard
      as every other confidence label in this project, stated plainly in
      the section's own explainer.

      A county is only flagged if it's currently genuinely clear (an
      already-broken county doesn't need an at-risk label), a real active
      alert covers it, and its historical tally has at least
      MIN_EVENTS_FOR_CONFIDENT_RANGE real events with "high" or "medium"
      as the plurality tier - a thin or low-dominant history stays
      unflagged rather than guessed into a false signal. Verified against
      the real live moment: 13 real counties flagged during the same
      active tropical system already surfacing elsewhere on the page,
      Madison leading at "high" confidence off 7 real historical events.
      No new external data source needed, as scoped - not broken down by
      alert type yet (the persisted tally is one county-wide blend across
      every alert type), a real, small, honestly-scoped first version.
      Paginated the same way as Outage History/Current Weather Alerts,
      since a widespread event could plausibly flag more than a page's
      worth of counties.
- [x] **Restoration-time signal for FPL - shipped 2026-07-17, as two
      deliberately separate, distinctly-labeled numbers, never merged
      into one.** FPL can never get a live incident-level model (its
      feed only ever reports a county-wide total, and events blur
      together - see Phase 2's county-history findings), so both
      versions here are honest substitutes, not predictions:
      - **"Major Storms"** - `storm_history.fpl_restoration_precedent()`,
        real min/median/max duration from the 17-storm PSC archive
        (`historical_consolidated.db`, 498 FPL events across 40 counties).
        "Major storm" means a real event serious enough that the Florida
        PSC required a formal restoration report - mostly named
        hurricanes/tropical storms, plus a few severe non-tropical
        outbreaks (checked directly: 3 of the 17 aren't officially named
        storms at all).
      - **"Everyday Outages"** - `county_status.
        fpl_ordinary_restoration_stats()`, the same idea from this
        project's own live tracking instead (484 real closed FPL events
        statewide) - a genuinely different, richer question ("how long
        does an ordinary outage take here") the storm archive alone
        can't answer. Needed one real filter: events longer than 96
        hours are excluded as likely several real outages blurred into
        one never-resets-to-zero county aggregate, not one real repair
        job - the cutoff came from the live data's own natural break
        (p99 ~90h, then a sharp jump straight to 217h/254h), not a
        guess.

      Both gated the same way: only shown on a county's live page while
      a real FPL outage is currently open there, not as a standalone
      historical curiosity.
- [x] **"Major Storms" bucketed by real wind severity - shipped
      2026-07-18.** The flat range above was blending genuinely
      different storms into one number (Nassau County alone: a 9-hour
      event and a 66-hour event averaged into a meaningless ~34h) -
      `storm_history.fpl_restoration_precedent_by_wind_severity()` splits
      the same real durations at the NHC's own Category 1 hurricane
      threshold (74mph, picked from real data - no single dramatic gap
      exists in the real 29-155mph distribution, so an invented
      percentile split would have been arbitrary in a way this
      authoritative number isn't). Verified worth building against the
      aggregate first: hurricane-force storms have a 96h statewide
      median vs 30h for sub-hurricane storms. Shown as a refinement next
      to the existing combined number, not a replacement - only about
      45% of FPL's historical records have a usable wind match, so a
      low-exposure county can still land entirely in one tier (confirmed
      real for Nassau: every one of its own storms on file tops out
      under 65mph, even during Helene).
- [x] **TECO restoration-estimate accuracy - shipped 2026-07-17, a
      genuinely different kind of signal from FPL's pair.** TECO already
      reports a real per-incident restoration estimate (unlike FPL,
      which has none at all), so instead of inventing a precedent range,
      `county_status.teco_etr_accuracy()` checks how trustworthy that
      existing number has actually been - each resolved incident's first
      stated ETR vs. when it actually closed. Real, clean data (every
      closed incident statewide has a usable ETR, no blurring problem to
      filter since each is already individually tracked by a real
      incident_id). Numbers checked and corrected 2026-07-18 after a real
      audit sweep found the originally-cited figures had drifted as more
      incidents closed (83% on-time had become 66%) - current statewide
      read: incidents resolve a median of ~2.5 hours *before* their own
      first estimate, on-time-or-early roughly two-thirds of the time.
      Same live-outage gating as FPL's pair.
- [x] **Duke restoration-time precedent - shipped 2026-07-18, the
      simplest of the three original Phase 3 shapes.** Duke's raw feed
      has no restoration-estimate field at all, so TECO's accuracy-check
      approach is permanently off the table for it - but Duke already
      reports real, individually-tracked incidents (like TECO, not a
      blurred county-wide aggregate like FPL), so it gets a plain
      duration precedent instead, `county_status.
      duke_restoration_precedent()`. No outlier-exclusion filter needed
      at all (checked directly: 7,195 real closed incidents statewide,
      only 1 over 48 hours, none over 96 - genuinely clean, unlike
      FPL's aggregate-blur problem), and no "Major Storms" sibling
      either, since Duke has no storm-archive counterpart the way FPL
      does. Real finding: Duke's incidents statewide resolve in a
      median of about 1.3 hours. Same live-outage gating as the other
      two.
- [x] **JEA restoration precedent - shipped 2026-07-18, the last
      utility with zero Phase 3 signal.** Same structural limit as FPL
      (no per-incident data at all, county-rollup only), so it gets
      FPL's historical-precedent shape via `storm_history.
      jea_restoration_precedent()` - 46 real records across Duval/Clay/
      St. Johns in the same 17-storm PSC archive. No live "Everyday
      Outages" companion though - JEA's real live volume is too thin (2
      closed events statewide when checked) to be worth a range built
      from 2 points, the same trap this project has avoided everywhere
      else. Ships historical-only, honestly scoped to what the data
      actually supports.
- [x] **LWBU restoration-estimate accuracy - shipped 2026-07-18.** Same
      shape as TECO's via `county_status.lwbu_etr_accuracy()` - judged
      too thin to build when TECO's version shipped (8 closed incidents),
      revisited once it reached 12. Two real bugs caught building this,
      not just plumbing: LWBU's raw ETR always carries a real UTC offset
      unlike TECO's naive format, which would have raised a real
      exception on every row and made the function silently always
      return "no data"; and LWBU's API doesn't zero-pad fractional
      seconds, which Python's timestamp parser rejects outright on the
      VM's Python version. Both fixed by testing against real messy VM
      data, not just clean local fixtures.
- [ ] FPUC's real per-incident view still only has 3 real closed
      incidents (checked again 2026-07-19) - still too thin for
      `teco_etr_accuracy()`'s approach to mean anything yet. Revisit once
      it accumulates more real incident history - no code change needed,
      the function would just need reusing.

## Phase 4: Public-Facing Query Layer (Live and actively growing since 2026-07-14 - "Future consideration, not started" below is stale as a phase-level label; individual items are still accurately marked)
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
- [x] **First real concept for the actual public-facing page (~2026-07-08
      through 2026-07-13)**, built as a separate design exploration. Combines the map, the Heat This
      Month panel, and the Storm History section into one real page
      layout on real live data rather than placeholders. Deliberately
      drops the internal-monitoring-style detail from the ops version -
      that's language for us, not a real visitor. Ends with an honest
      footer stating plainly what the page does and doesn't show. Still
      just a design concept - nothing here is a live route yet.
- [x] **The concept above is now real, live code - shipped 2026-07-14,
      a genuinely separate app from the internal dashboard, not a
      redesign of it.** Built as
      its own Flask app (own port, own template folder), sharing only
      the read-only data layer with the internal tool - it imports
      nothing from the internal dashboard's own code, and the internal
      dashboard imports nothing from it, so the two can change
      independently. Ported closely from the real design-sandbox
      artifact rather than a from-memory approximation: a real
      isometric Florida county map (true angled projection, real
      per-county extrusion tied to severity, real public boundary data
      regenerated fresh via a small one-time build script since the
      original never made it into this repo) toggles between an
      all-time historical weather-match view and current live severity,
      clickable through to a county's real current outages, active
      weather alerts, and full storm history. A real narrative summary
      (worst county/utility by count and by percentage, computed from
      one live data pass, not invented) sits above the fold. Three
      small shared modules were extracted out of the internal
      dashboard's own code along the way (county-status/storm-history/
      timestamp-formatting helpers, plus a new all-time confidence
      tally) so both apps read live and historical data the same way
      without duplicating that logic or coupling to each other - a real
      refactor, and the full existing test suite still passes unchanged
      after it. A real performance bug surfaced during this build (a
      44-second cold page load from re-running expensive all-time
      correlation queries on every view) - fixed the same way an
      identical problem was already fixed once on the internal
      dashboard, a short-lived cache. Currently runs locally only, not
      exposed to the internet - that's still its own later decision,
      gated on the same test/prod environment split below.
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
- [x] **Confirmed a real live-data geographic gap in Florida (2026-07-12),
      and closed most of it utility by utility over the following two
      days.** Cross-referencing the
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
- [ ] **Crowd-sourced status pins - a real idea, not yet designed in
      detail.** Raised after noticing that even well-received outage/
      weather apps tend to feel community-less. A full comment/forum
      section was considered and deliberately set aside - moderation
      needs are highest at exactly the moment (a real storm) when
      there's the least bandwidth to provide it, and a paywalled
      version of that (rate-limited free posting, unlimited on a paid
      tier) reads badly for a tool people rely on during emergencies.
      Landed instead on a narrower, Waze-style idea: simple pins (e.g.
      "downed line here"), not freeform text, so there's much less to
      moderate. Two real design constraints already agreed on before
      any of this is built: (1) rate-limiting per device/account to
      resist spam and grief-pinning; (2) a pin shouldn't show as
      trustworthy on a single report alone - needs roughly 3-4 similar
      nearby reports before it's treated as confirmed, closer to how
      real crowd-sourced traffic apps handle unverified reports. Also
      worth doing once real: cross-referencing a confirmed pin against
      this project's own live utility-outage data for the same area,
      since that correlation is close to free given what's already
      collected. Squarely a Phase 4+ idea - needs real public traffic
      and the test/prod split above first, not something to start now.

## Utility coverage
Real dates, pulled from git history rather than memory: FPL (foundational,
Phase 1). TECO 2026-07-02. Duke 2026-07-03. JEA 2026-07-09. Tallahassee,
Talquin, FPUC (combined), and Peace River Electric all 2026-07-13, the
same day as Florida Keys Electric. TCEC, EREC, CHELCO, GCEC, and Lake
Worth Beach Utilities all 2026-07-14 (GCEC's real per-county coverage
milestone below is also this date). Orlando Utilities Commission
2026-07-16, same day as Lee County Electric Cooperative, the 16th. Clay
Electric Cooperative, the 17th and (so far) last, followed 2026-07-19.

Grew from one utility (FPL) to seventeen live sources across this
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
County (Keys) gap. A tenth through thirteenth, all on the same
underlying data platform, closed the entire remaining rural Panhandle
gap one county group at a time as combined-territory trackers (one
authoritative total across a multi-county territory, honestly labeled
as such rather than guessed at a fake single-county number) - the last
of the four found by cross-referencing this project's own historical
storm data for a consistently-appearing real utility rather than
guessing, and completing full real per-county live coverage across all
67 Florida counties for the first time. A fourteenth (Lake Worth Beach
Utilities) added a real, always-present city-wide percentage for a
single county already covered indirectly through FPL, plus a separate,
richer per-incident feed (street, cause, crew status) - kept as two
independent views of the same real outages, with only the city-wide
total feeding the statewide numbers, the same "pick exactly one real
per-county source" principle FPUC's own dual-shape feed established.
A fifteenth (Orlando Utilities Commission) added a second, real-
percentage reading for a county already covered by an incident-level
source with no clean customer-base denominator - found the same honest
way as GCEC, by ranking every utility that's ever shown up in this
project's own historical storm data and confirming a real live feed
existed before building anything. Runs on the same shared vendor
platform JEA's feed does. A sixteenth (Lee County Electric Cooperative)
was the same ranking's #2 candidate, added once OUC was done - a real
per-county source (five counties) on a fully public, unauthenticated
data platform, no tracking code or WAF gate required. A seventeenth
(Clay Electric Cooperative) topped the same historical-storm ranking
by a clear margin once run again - a real per-county source across 15
counties, on the identical platform LCEC already runs on, the richest
shape confirmed yet. Its real per-incident array shipped too, the same
night - live restoration estimate, crew/planned status, a statewide
accuracy check - but honestly without a county attached to any of it,
after a real coordinate transform investigation (a documented
reference system, an empirical calibration against a real live
incident, a look at the platform's own rendering logic) confirmed
unsolvable for now rather than guessed around.

Every combined-territory source (where one number covers multiple real
counties rather than a true per-county breakdown) is disclosed as such
everywhere it shows up - on the dashboard, in the county lookup tool,
and in its own weather-correlation logic, which is designed to return
empty rather than fake a match it can't actually verify.

- [x] **Live `/county` lookup page added (2026-07-14)** - pick any of Florida's 67
      real counties and see everything currently relevant to it in one
      place: real per-county outages from every source that actually
      reports per-county, active weather alerts naming that county, and
      - shown in a clearly separate group, never blended in - the
      combined-territory sources whose multi-county label happens to
      cover it. Deliberately live/current-status only; the storm-history
      tool remains the place for real multi-year data per county.
- [x] **Real mobile pass on the public page - shipped 2026-07-18.** The
      Florida map had a hardcoded 400px width wider than every real
      iPhone viewport once the page's own padding was accounted for -
      switched to a real responsive width/aspect-ratio, verified against
      a true 390px device-emulated viewport via headless Chrome/CDP, not
      just a resized desktop window (which turned out to give a false
      pass - a real, separate finding this same night). Also: the
      header intro and five section intros (Heat This Month, Current
      Weather Alerts, County Status, Outage History, Storm History) now
      collapse behind a "What is this section?" toggle instead of
      showing their full explanatory paragraph by default, meaningfully
      cutting real scroll length. Two real overflow bugs also found and
      fixed this pass: a combined-territory label with no spaces
      (`Bay/Calhoun/Gulf/Jackson/Walton/Washington`) had no valid point
      to line-break and ran off the screen with a real horizontal
      scrollbar; a card's decorative corner-cut clip-path only clipped
      the two diagonal corners, not content overflowing the straight
      edge.
- [x] **Real county-search combobox, replacing a plain `<input list>` +
      `<datalist>` - shipped 2026-07-18.** Datalist's native dropdown
      can't be sized or styled (no forcing "show 5 rows") and iOS
      Safari's support for it is spotty at best - likely near-unusable
      for the exact audience (older users, less precise taps) this was
      meant to help. Hand-built replacement: real substring matching (so
      "Dade" finds "Miami-Dade", not just names starting with "Dade"), a
      fixed-height scrollable list sized for ~5 real touch-target rows,
      both tap and keyboard selection. Added in two places - the hero
      map card (instant navigation, same as clicking the map) and Storm
      History (navigates + searches) - sharing one factory function
      rather than two copy-pasted instances.
- [x] **Outage History paginated instead of a hard display ceiling -
      shipped 2026-07-19.** The old cap silently dropped anything past
      15 rows with just a "showing N of TOTAL" note and no way to
      actually see the rest. Real server-side pagination instead (7 per
      page, Prev/Next links, page number clamped to a valid range so a
      stale bookmark can't 500 or silently show nothing) - verified
      against real production-scale data, a 412-outage county
      (59 real pages) and a 13-outage county (2 pages), confirming page
      2 actually shows different, older events, not a repeat of page 1.
- [x] **Current Weather Alerts paginated the same way - shipped
      2026-07-20.** A real active storm (Hillsborough taking the worst
      of it) pushed the statewide active-alert count well past a
      comfortable single scroll - same fix, same page size, same real
      pagination helper Outage History already uses, reused rather than
      reinvented. Verified against the real live moment: 28 real active
      alerts, a real Tropical Cyclone Local Statement among them, and a
      real narrow-mobile-viewport screenshot, not just the desktop view.
- [x] **Weather alert cards made collapsible - shipped 2026-07-20,
      same night as pagination.** Even paginated at 7 per page, a fully
      expanded card per alert (type, window, full area chip list) still
      read as a wall of text during a busy storm. Real native
      `<details>`/`<summary>` collapse now, same mechanism the "What is
      this section?" explainers already use - a compact one-line summary
      (event type, severity, a short area preview like "Hillsborough &
      2 more") by default, full detail one click away. Extreme-severity
      alerts default open rather than collapsed, on purpose - the most
      urgent ones shouldn't need an extra click to read in full.
- [x] **Storm History narrowed to a rolling last-4-years window, and
      paginated - shipped 2026-07-21.** The full real archive (17
      storms, 2018-2025) listed in full for every county was getting
      long enough to scroll past just to see anything recent. A rolling
      window off the real current year (not a fixed range, so "last 4
      years" stays true as time passes) plus the same real pagination
      already used elsewhere on the page. One real edge case handled on
      purpose: a genuinely known county whose filtered window comes back
      empty gets an honest "no storms in the last 4 years" message, not
      the "check your spelling" one reserved for actually-unrecognized
      county names - verified directly, not assumed, that the two stay
      distinguishable. Older storms aren't gone, just not the default.
- [x] **Public page section order reshuffled - shipped 2026-07-21.**
      Heat This Month, the lowest-urgency item on the page, was sitting
      above Current Weather Alerts, the most time-critical one. Moved it
      below Current Weather Alerts and At-Risk Counties instead, so the
      statewide-monitoring half of the page (before the county
      drill-down half) now reads in real order of urgency. No new page -
      not enough content yet to justify the extra click that would cost
      a mobile visitor just reaching their own county.
- [x] **Real per-county activity signal for TCEC/EREC/CHELCO/GCEC -
      shipped 2026-07-18.** These four report one combined total with no
      per-county split - but a real active outage that same night
      revealed their `streetsAffected` field isn't always empty like
      earlier notes assumed; it's real street names (no per-street
      customer count or coordinates), so the honest ceiling is "which
      counties currently have reported activity," not a number, shown as
      a refinement next to the existing combined total. Two real
      problems solved, not just plumbing: a bare street name isn't
      reliably geocodable on its own (an unconstrained nationwide lookup
      for a real CHELCO street matched counties nowhere near its actual
      territory - constraining the search to just that utility's own
      known counties resolved it correctly instead), and Nominatim's
      free usage policy caps requests at 1/second, so results are cached
      forever per street and new lookups are capped per poll cycle
      rather than blocking every other utility's polling behind a
      dozens-of-streets backlog. A real bug in this same feature was
      caught and fixed the next day (2026-07-19) during a full audit
      sweep: a network failure and a genuine zero-result response were
      both being cached as the same confident "no match," permanently -
      the same root-cause pattern as the county-overwrite bugs above,
      just a different mechanism. Fixed so a real request failure
      retries on a later cycle instead of being baked in wrong forever.
- [x] **FPUC's combined-vs-incident undercount concern checked against
      real data - 2026-07-18, re-check banked for ~2026-08-01.** Long-
      standing open question, never previously checked against real
      overlapping data. Found 9 real non-zero combined-total readings
      across the whole live-tracking history, mapping to 4 distinct real
      incidents - every single one matched exactly (same customer count,
      same timestamp), no undercount observed in any real instance
      checked. Sample is thin (4 incidents since April), so this isn't
      proof the concern never happens - banked as a standing re-check
      once more incidents accumulate, not treated as permanently closed.

## Phase 5: Scale (Open question — not yet committed)
- [ ] More utility integrations beyond Florida

  Bigger than it sounds: a different state means a different utility
  regulator, a different weather-alert forecast region, and the
  Florida-specific validation logic already built would need to
  generalize. Worth treating as its own design decision when it comes
  up, not an incremental add.

## Phase 6: Public Launch (Not started, but no longer blocked - the Oracle Cloud migration settled 2026-07-17, hardened with systemd/SELinux/backups on both sides)
Making the Apollo Sentinel public page a real, clickable website - not
just reachable over an SSH tunnel. Same VM, no second server needed;
this project's traffic never justifies one.

- [ ] A real domain name (the one actual recurring cost in this plan)
- [ ] DNS pointed at the VM's IP
- [ ] A real production web server in front of Flask (nginx + gunicorn -
      Flask's own dev server explicitly isn't meant for real traffic)
- [ ] HTTPS via Let's Encrypt (free)
- [ ] Firewall opened for real web traffic (80/443) - only SSH is open
      today
- [ ] A real choice on analytics: self-hosted/privacy-respecting (e.g.
      Plausible, Umami) vs. a hosted third-party tool - decide
      deliberately, don't default
- [ ] Extend real email alerting to every real source, not just
      Talquin/PRECO (see Phase 2) - deliberately deferred here rather
      than done now, decided 2026-07-20: a dashboard strip only someone
      running the poller happens to check is an acceptable gap for a
      personal/internal tool, but stops being acceptable the moment
      real strangers depend on this data being right. Do this before
      Phase 6 actually ships, not after.
- [ ] Real automated deployment (CI exists - tests run on every push;
      CD doesn't - getting a merged commit onto the VM is still a fully
      manual pull + restart). Prompted by a real, honest surprise
      2026-07-20: merging a PR and expecting the live site to reflect
      it immediately, when it didn't until someone (or Claude) manually
      synced the VM. Deliberately deferred, not fixed on the spot -
      worth having before real strangers are the ones hitting a stale
      deploy, less urgent while it's still just the two of us who'd
      notice. Real tradeoff to weigh when this comes up again: automatic
      deploy means it can never be forgotten, but also means nobody's
      watching that specific deploy happen before real visitors see it.

The internal dashboard stays SSH-tunnel-only regardless - never
publicly exposed, a firm decision, not just the current state.

## Explicitly not planned
- **Any public pass-through of the raw live utility feeds** - never
  planned, not up for reconsideration. These are not official public
  APIs, and re-publishing them at any real scale is a different (and
  worse) risk than using them for our own private analysis. See Phase
  4 above for what *is* now under real consideration - the derived,
  cleaned, aggregated layer, not this.
