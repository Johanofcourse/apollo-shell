# Product Review Doc: Weather/Outage Correlation

## Status
Partially built. Most of the underlying mechanism already exists
(`apollo_shell/correlate.py`); the new proposed work is a confidence
scheme and display layer. This doc reviews the feature as a whole,
not a from-scratch build.

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

## Proposed new work
- **Correlation confidence.** Not yet defined or built. Proposed
  starting point: derive an initial confidence level directly from
  NWS's own alert severity field (already captured, not something we'd
  be inventing) — e.g. a match against a "Severe" alert is higher
  confidence than a match against a "Minor" one. This needs real design
  work before it's buildable, not just a checkbox.
- **Display layer.** Show the matched weather type and confidence
  level alongside an outage, wherever outages are shown to a user.

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
