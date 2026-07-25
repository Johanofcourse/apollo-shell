# Product Review Doc: Monetization

## Status
Direction settled, nothing built. Raised for the first time 2026-07-25,
well before public launch - this doc reviews a decided principle and
what it would take to build, not a from-scratch brainstorm. See
`docs/ROADMAP.md`'s Phase 7 for the tracked checklist version of the
same plan.

## Problem
This project has no monetization at all, and no plan for one. Left
undecided, the default failure mode is deciding under pressure, later,
possibly by paywalling whatever's easiest to gate rather than what's
actually right to gate - exactly the mistake almost made here on the
first pass (see below).

## Goal
Settle *what kind* of thing is allowed to be paywalled, before there's
any real pressure to monetize, so a future decision has a principle to
check itself against instead of being made from scratch under revenue
pressure.

## The first draft, and why it changed
The original idea: rate-limit core lookups to roughly 5/hour, $1.99/mo
to remove the cap. Reconsidered against a stance Johan had already
taken once before, for a different feature - rejecting a paid comment-
tier for the crowd-pins idea specifically because paywalling
communication during an emergency is a bad look, not a monetization
win (see `project_crowd_pins_idea` in memory, decided 2026-07-14). The
same logic applies directly here: the worst possible moment to ask
someone for $1.99 is mid-storm, checking whether their own block has
power. Once laid out, Johan agreed and revised the plan himself in the
same conversation.

## User story
> As someone who needs to check on old outage/storm history - for an
> insurance claim, a legal dispute, or just curiosity - I'd pay for
> deeper access to the historical archive than the free front page
> shows, without ever having to pay just to check whether my own power
> is out right now.

Note: there is currently no paying user of any kind - this describes
an intended future direction, not current usage. Same caveat the
weather-correlation review draws for its own user story.

## The settled principle
**Paywall archive depth. Never paywall live status.**

Concretely: Storm History and Outage History both already paginate
(`_paginate()`, `public_site.py`) with the first page free. The agreed
plan is everything past page 1 sits behind a subscription; current/live
outage status, active weather alerts, At-Risk Counties, and page 1 of
both histories all stay free, unconditionally, forever. The paywall
only ever gates "go deeper into the past" - never "see what's
happening right now."

Storm History in particular already has real bones for this: an
independently-sourced 2018-2025 archive with genuine standalone value
(the user story above - an insurance claim doesn't care about live
data, it cares about a specific past storm). It currently defaults to a
rolling last-4-years window with **no way to reach anything older at
all**, free or paid - the exact shape a premium tier would fill, not a
new restriction being invented on top of something previously open.

## Already built
- Nothing monetization-specific. The only thing this review can point
  to as "already built" is the *shape* the paywall would slot into -
  real pagination already exists on both histories, for unrelated
  reasons (scrolling got long before a subscription was ever on the
  table).

## Proposed new work
- **User accounts** - don't exist in any form right now. Every visitor
  today is anonymous; a paywall needs a concept of "this specific
  person's subscription" before it can gate anything.
- **Real payment processing** - Stripe is the likely default choice,
  not yet evaluated against alternatives. Needs real subscription
  handling: creation, renewal, failed payments, cancellation, refunds.
- **A real business/legal layer** - a registered business entity
  behind the payment processor account, a terms of service, a refund
  policy, and sales-tax handling that depends on where a subscriber
  actually lives - not a coding problem, a real compliance one.
- **The actual gate itself** - once accounts + payments exist, gating
  page 2+ of `_paginate()`'s output behind a subscription check is the
  comparatively small remaining step.

## Explicitly out of scope for this review
- **Any paywall on live/current data** - not a phased-in restriction,
  a hard line. County Status, active weather alerts, At-Risk Counties,
  and page 1 of both histories are never part of any future paywall
  discussion, not just "not part of this one."
- **Ads** - not raised, not evaluated here. A genuinely separate
  monetization path with its own tradeoffs (would need to be weighed
  against the site's plain, uncluttered visual identity), not folded
  into this review by default.
- **Rate-limiting core lookups** - the original idea, explicitly
  rejected in favor of the archive-depth principle above. Recorded here
  so it isn't accidentally re-proposed as if it were new.

## Success metrics
Not yet meaningful to define. Same real gating factor as the plan
itself: this needs actual public usage data (Phase 6) before any
target (conversion rate, willingness to pay, which counties'
histories get requested past page 1 most) means anything more than a
guess.
