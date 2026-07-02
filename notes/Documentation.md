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
- A 15-minute poller (`main.py`) running live in the background via
  launchd — fetch, save, correlate, log, repeat, forever, quietly
- Outage lifecycle tracking (`outage_events` table): we finally know
  when an outage *starts* and *ends*, not just "customers out: 41"
  frozen in a single snapshot

## Live right now
- The poller is out there right now, polling. `outages.db` is
  growing on its own. Somewhat satisfying to just... let it run.
- `weather_alerts` is still thin — Florida hasn't thrown a real storm
  at us since we flipped this on. The correlation logic is sitting
  there, arms crossed, waiting for something to correlate.

## The honest gaps
- Can we guess when the lights come back on? Not yet, and not for
  code reasons — FPL's public feed gives us three fields (county,
  customers out, customers served) and nothing else. No crew ETA, no
  incident IDs, nothing to hang a prediction on.
- One or two closed `outage_events` isn't a pattern, it's an anecdote.
  Need weeks of real outages, ideally overlapping real weather, before
  "educated guess" becomes more than a phrase.
- The original plan — AI input parser, command history, an actual
  interactive shell — got quietly benched. Turns out "map power
  outages against weather" was the more interesting rabbit hole.

## Open question worth chasing
- fplmaps.com is an interactive map. Interactive maps usually have a
  richer feed behind them than the county-summary JSON we're using.
  Is there an incident-level endpoint hiding in their network traffic
  that actually has restoration estimates? Unconfirmed — worth five
  minutes in devtools sometime.
