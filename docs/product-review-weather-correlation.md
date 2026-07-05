# Product Review Doc: Weather/Outage Correlation

## Status
Mostly built. The matching mechanism, a real weather-match confidence
label, and a display layer for both all exist now
(`apollo_shell/correlate.py`, `dashboard.py`); a second, different
confidence concept (restoration confidence) is the remaining proposed
work. This doc reviews the feature as a whole, not a from-scratch
build.

## Problem
Outage data and weather data are collected separately, with no
connection surfaced anywhere. There's no way to tell, for a given
outage, whether it happened alongside severe weather in the same area
or was more likely an isolated, unrelated issue (equipment failure,
localized damage, etc).

## Goal
Surface, for each outage, whether it coincided with an active weather
alert in the same place and time — and how strong that overlap is.

**This is a correlation signal, not a causal diagnosis.** A matched
outage does not prove weather caused it — only that both were true at
the same time and place. The feature should never claim more than
that.

## User story
> As a homeowner, I want to see whether a power outage coincided with
> severe weather in my area, so I can tell whether it was part of a
> broader weather event or more likely an isolated local issue.

Note: there is currently no end-user-facing product — the only
consumer of this data today is a localhost-only personal dashboard.
This user story describes an intended future direction, not current
usage.

## Already built
- NWS weather alerts fetched and stored, including event type and
  NWS's own severity classification (Severe/Moderate/Minor/etc.)
- Outages matched to alerts by county + effective/expires time-window
  overlap
- Weather event type available per match (e.g. "Severe Thunderstorm
  Warning," "Flood Advisory")
- **Weather-match confidence** (`weather_match_confidence()` in
  `correlate.py`) - a high/medium/low label on every match, computed
  on the fly rather than stored. Driven primarily by whether the
  matched alert's event type could plausibly cause a power outage at
  all, not by NWS's own severity field alone: a "Severe" Rip Current
  Statement (a real ocean-safety alert with zero physical connection to
  outages) never outranks a "Moderate" Tornado Warning. Severity is a
  secondary modifier, only applied within an event type that's already
  plausible. Caught a real problem in the process - one county had 268
  correlation "matches" that were entirely Rip Current Statements,
  indistinguishable in the old summary output from a genuine severe-
  weather match.
- **Display layer.** The matched weather type and confidence level now
  show up alongside outages in `dashboard.py`/`templates/
  dashboard.html` - confidence bars and color-coded severity badges on
  FPL's, TECO's, and Duke's tables, plus a KPI summary strip at the top
  of the page for an at-a-glance read before scrolling into detail.

## Proposed new work
- **Restoration confidence.** Not yet built, and not just unstarted -
  explicitly blocked on data volume, not a design or code problem. This
  is a different confidence concept from weather-match confidence
  above: that one asks "was this outage really weather-related," this
  one would ask "how long is this outage likely to take to fix" (e.g.
  "roughly a 50% chance this is fixed within a day"). Needs real
  restoration-time history to be believable - see the Phase 3 timeline
  discussion in `docs/ROADMAP.md`.
- **A more intuitive presentation of both confidence labels**, once
  restoration confidence exists too - see the dashboard redesign work
  in `docs/ROADMAP.md` (Phase 2.5). The current bars/badges are a
  developer-legible first pass; the explicit design goal going forward
  is legibility for a non-technical person with no assumed familiarity
  with dashboards or web-app conventions, not just "technically
  displayed somewhere."

## Explicitly out of scope for this review
- Non-weather causes (equipment failure, vehicle accidents, etc.) —
  a different utility's live feed already gives us a stated cause for
  some outages, but folding that in is a separate piece of work, not
  part of "weather correlation."
- Any causal claim ("this outage happened *because of* weather") —
  the matching logic can't support that and shouldn't imply it.

## Success metrics
- **Matching accuracy**: correlation matches, spot-checked against a
  manual sample, are correct (right county, right time window). Target:
  no known false positives in a reviewed sample.
- **Match rate is a metric to observe, not a target to hit.** Whatever
  percentage of outages actually correlate with weather is real
  information about the data — it should be reported honestly, not
  optimized toward a predetermined number. (An earlier draft of this
  doc set an 80% target; dropped, since real data collected so far
  suggests most routine outages are *not* weather-related, and treating
  that as a goal to hit would incentivize inflating matches rather than
  reporting reality.)
