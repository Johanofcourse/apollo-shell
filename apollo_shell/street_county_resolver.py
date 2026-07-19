import time

import requests

# TCEC/EREC/CHELCO/GCEC's real, known service territories - the same
# county lists already used for each utility's COMBINED_TERRITORY_LABEL
# in their own fetch_*_outages.py modules. Resolving a bare street name
# is only reliable when constrained to a small, already-known candidate
# list (confirmed real 2026-07-18: an unconstrained nationwide lookup for
# "Sawmill Rd" - a real CHELCO street - matched Duval/Leon/Hillsborough
# instead, nowhere near CHELCO's real territory; the same name constrained
# to CHELCO's own counties correctly found the real Walton County match).
KNOWN_TERRITORIES = {
    "Tri-County Electric Cooperative, Inc.": ["Jefferson", "Madison", "Taylor", "Dixie", "Lafayette", "Leon"],
    "Escambia River Electric Cooperative, Inc.": ["Escambia", "Santa Rosa"],
    "Choctawhatchee Electric Cooperative, Inc.": ["Santa Rosa", "Okaloosa", "Walton", "Holmes"],
    "Gulf Coast Electric Cooperative, Inc.": ["Bay", "Calhoun", "Gulf", "Jackson", "Walton", "Washington"],
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy caps free public use at 1 request/second and
# requires a real, identifying User-Agent - this is a real external
# service run by volunteers, not our own infrastructure, so both are
# followed exactly rather than pushed against.
_REQUEST_INTERVAL_SECONDS = 1.1
_USER_AGENT = "apollo-shell-outage-tracker/1.0 (street-to-county resolution for known utility territories)"


class _LookupFailed(Exception):
    """A real request failure (network/timeout/HTTP error) - genuinely
    inconclusive, distinct from Nominatim successfully responding with
    zero results. Conflating the two was a real bug: caching a network
    hiccup as a confident "this street isn't in this county" is the
    same class of mistake as the county-overwrite bug found earlier this
    session (a transient failure treated as a confident, permanent
    answer) - here it would have permanently mis-marked a street as
    unresolvable instead of letting a later cycle retry it."""


def _query_nominatim(street_name, county):
    query = f"{street_name}, {county} County, Florida, USA"
    params = {"q": query, "format": "json", "addressdetails": 1, "limit": 1, "countrycodes": "us"}
    headers = {"User-Agent": _USER_AGENT}
    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Street lookup failed for '{street_name}' in {county}: {e}")
        raise _LookupFailed(str(e))
    return bool(results)


def resolve_street(utility, street_name):
    """
    Resolve one real street name to one of utility's known counties, by
    checking each candidate county explicitly (not an unconstrained
    search - see KNOWN_TERRITORIES's own comment for why). Rate-limited
    to Nominatim's real usage policy, so this is slow by design (up to
    len(candidate counties) real HTTP round trips, ~1 second apart) -
    callers should cache the result (see OutageDatabase.
    save_street_county()) and never call this for a street more than once
    - UNLESS this raises _LookupFailed (see below), which callers should
    treat as "try again next cycle," not a real answer to cache.

    Returns the matched county name, or None if the street matched zero
    of the utility's known counties (no match at all) or more than one
    (genuinely ambiguous - a street name that real-world exists in two of
    this utility's own counties) - either way, not confident enough to
    report, consistent with this project's honesty-over-polish standard
    elsewhere (see e.g. lwbu_etr_accuracy()/fpl_ordinary_restoration_stats()
    handling of thin/ambiguous real data).

    Raises _LookupFailed if any candidate county's request itself failed
    (network/timeout/HTTP error) - genuinely inconclusive, not the same
    as a confirmed zero-result response. A caller that caught this and
    cached None anyway would permanently mark a street unresolvable
    based on a technical hiccup instead of a real answer.
    """
    candidates = KNOWN_TERRITORIES.get(utility, [])
    matches = []
    for county in candidates:
        if _query_nominatim(street_name, county):
            matches.append(county)
        time.sleep(_REQUEST_INTERVAL_SECONDS)

    if len(matches) == 1:
        return matches[0]
    return None


def resolve_streets(utility, street_names, db, max_new_lookups=None):
    """
    Resolve every street in street_names for utility, using db's cache
    first (OutageDatabase.get_cached_street_counties()) and only calling
    resolve_street() - real, rate-limited network calls - for names never
    seen before. Every result (including unresolved ones) gets cached via
    OutageDatabase.save_street_county() so a given street is only ever
    really geocoded once, regardless of how many times it shows up in
    later outages.

    max_new_lookups caps how many previously-unseen streets get a real
    network lookup in this one call - a real active outage can carry
    dozens of streets (79 real ones seen for CHELCO alone 2026-07-18),
    each needing up to len(that utility's known counties) real,
    rate-limited HTTP round trips (~1 second apart) - resolving all of
    them synchronously inside a live 15-minute poll cycle would block
    every other utility's polling behind it for minutes. None means no
    cap (fine for a deliberate one-off backfill run, not the live
    poller) - uncapped calls still only pay this cost once per street
    ever, same as a capped one, just not spread across multiple cycles.
    Streets beyond the cap are simply left unresolved for this call -
    they're still real streets in street_names, they just don't get a
    network lookup yet, and will on a future cycle if still active.

    Returns a dict of {street_name: county_or_None} for every CACHED or
    newly-resolved name - a name beyond max_new_lookups with no prior
    cache entry is simply absent from the result, not present with a
    None value (which would incorrectly claim "checked, no match"). A
    street whose lookup hit a real request failure (see resolve_street()'s
    _LookupFailed) is also absent, not cached as None - a technical
    hiccup isn't a confident "no match," and caching it as one would
    permanently mark a real, resolvable street unresolvable. It still
    counts against max_new_lookups for this cycle (real rate-limited
    time was spent on it) and will be retried on a future cycle.
    """
    cached = db.get_cached_street_counties(utility, street_names)
    result = {}
    new_lookups = 0
    for street_name in street_names:
        if street_name in cached:
            result[street_name] = cached[street_name]
            continue
        if max_new_lookups is not None and new_lookups >= max_new_lookups:
            continue
        new_lookups += 1
        try:
            county = resolve_street(utility, street_name)
        except _LookupFailed:
            continue
        db.save_street_county(utility, street_name, county)
        result[street_name] = county
    return result


def active_counties(utility, street_names, db, max_new_lookups=None):
    """
    Real, currently-known-active counties for utility right now, derived
    from street_names (a live streetsAffected list) - the honest ceiling
    of what this data can say (no per-street customer count exists, so
    this is "these counties have reported activity right now", not a
    number). Streets that resolve to None (no confident county match) or
    that weren't resolved this call at all (see max_new_lookups on
    resolve_streets()) are silently excluded, not guessed into a county.

    Returns a sorted list of distinct county names, or [] if
    street_names is empty/None or nothing resolved (yet).
    """
    if not street_names:
        return []
    resolved = resolve_streets(utility, street_names, db, max_new_lookups=max_new_lookups)
    return sorted({county for county in resolved.values() if county})
