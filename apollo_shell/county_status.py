"""
Shared, presentation-agnostic per-county status logic - used by both
dashboard.py (the internal ops tool) and public_site.py (the public-
facing page) so the two apps read the same live data the same way,
without either one importing from the other. Pure data-assembly and
formatting functions only; no Flask/template dependency here.
"""
from datetime import datetime

from correlate import (
    _county_in_alert, correlation_summary,
    teco_correlation_summary, duke_correlation_summary,
    find_correlations, find_teco_correlations, find_duke_correlations,
    find_jea_correlations, find_tallahassee_correlations,
    find_talquin_correlations, find_fpuc_incident_correlations,
    find_preco_correlations, find_fkec_correlations, find_tcec_correlations,
    find_erec_correlations, find_chelco_correlations, find_gcec_correlations,
    find_lwbu_correlations, find_ouc_correlations, find_lcec_correlations,
)
from historical_import import FLORIDA_COUNTIES

# The 67 real Florida county names, properly cased for display. See
# FLORIDA_COUNTIES (all-caps, a PSC-parser artifact) - .title() handles
# every real multi-word/hyphenated name correctly (e.g. "MIAMI-DADE" ->
# "Miami-Dade", "ST. JOHNS" -> "St. Johns") except "DESOTO", the one
# county with an internal capital letter .title() can't produce on its
# own ("Desoto", not "DeSoto") - the same casing bug already caught
# once in fetch_preco_outages.py, fixed here the same way.
COUNTY_PICKER_CHOICES = sorted(
    "DeSoto" if c == "DESOTO" else c.title() for c in FLORIDA_COUNTIES
)


def humanize_timestamp(ts):
    """
    Turn a raw ISO timestamp ("2026-07-02T01:19:57.483375" or, for
    weather alerts, "2026-07-04T02:01:00-04:00") into plain prose
    ("July 2, 2026, 1:19 AM") for display. The duration/"ago" values
    elsewhere (_duration_since) are unaffected - this is only for
    absolute-time display.
    """
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    return dt.strftime("%B %-d, %Y, %-I:%M %p")


def _duration_since(start_iso, end_iso=None):
    """
    Human-readable duration between two ISO timestamps (or start_iso
    and now, if end_iso is omitted).
    """
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso) if end_iso else datetime.now()
    total_minutes = int((end - start).total_seconds() // 60)

    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _percentage_tier(percentage_out):
    """
    Bucket a peak-percentage-out value into a severity tier for a
    colored badge/map fill. Real Florida outage percentages are rarely
    above 20-30% outside of a major hurricane, so a plain 0-100 linear
    scale would look nearly empty for almost every real row - a
    discrete tier reads much better than a proportional one at these
    scales.
    """
    if percentage_out is None:
        return "unknown"
    if percentage_out >= 30:
        return "critical"
    if percentage_out >= 10:
        return "high"
    if percentage_out >= 2:
        return "medium"
    return "low"


def _normalize_open_events(open_events, customers_field, peak_field):
    """
    Common shape for a currently-open event regardless of source
    (utility, county, current/peak customers, when it started, how
    long it's been going). Computes "duration" fresh (these are always
    open events, no end_time to bound it), rather than assuming some
    other caller already added it as a side effect.

    current_percentage_out/peak_percentage_out/customers_served are
    carried through when the source row has them (the real per-county/
    territory rollup sources - FPL, JEA, Talquin, PRECO, FKEC, LWBU, and
    all five combined-territory trackers) and come back None otherwise (the
    incident-level sources - TECO, Duke, Tallahassee, FPUC's incidents -
    which have no clean per-incident denominator/customer base at all).
    See county_verdict() for how the two cases get tiered together.
    """
    return [{
        "utility": e["utility"],
        "county": e["county"],
        "customers": e[customers_field],
        "peak_customers": e[peak_field],
        "current_percentage_out": e.get("current_percentage_out"),
        "peak_percentage_out": e.get("peak_percentage_out"),
        "customers_served": e.get("customers_served"),
        "start_time": e["start_time"],
        "duration": _duration_since(e["start_time"]),
    } for e in open_events]


def _real_per_county_open_events(db):
    """
    Every currently-open event from a source whose "county" field is a
    real, single Florida county - safe to match exactly. Includes
    FPUC's real per-incident markers (reverse-geocoded, can be a real
    county) alongside the always-real per-county rollup sources -
    deliberately NOT the combined-territory sources (FPUC's original
    combined view, TCEC, EREC, CHELCO, GCEC), whose "county" is a
    multi-name label rather than one real county - see
    _combined_territory_open_events() below.
    """
    return (
        _normalize_open_events(db.get_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_teco_open_events(), "current_customer_count", "peak_customer_count")
        + _normalize_open_events(db.get_duke_open_events(), "current_customer_count", "peak_customer_count")
        + _normalize_open_events(db.get_jea_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_tallahassee_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_talquin_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_preco_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_fkec_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_lwbu_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_ouc_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_lcec_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_fpuc_open_incidents(), "current_customer_count", "peak_customer_count")
    )


def _combined_territory_open_events(db):
    """
    Every currently-open event from a combined-territory source - real
    counties, just not splittable from a single response (see each
    fetch_X_outages.py module's own COMBINED_TERRITORY_LABEL). Kept as
    its own distinct group, never mixed in with the real per-county
    rows, so a reader can't mistake "the whole territory's number" for
    "just this county's number."
    """
    return (
        _normalize_open_events(db.get_fpuc_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_tcec_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_erec_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_chelco_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_gcec_open_events(), "current_customers_out", "peak_customers_out")
    )


def _rows_for_county(rows, search_county):
    """
    Filter normalized event rows down to ones whose county field
    matches search_county. Reuses correlate.py's _county_in_alert() (a
    plain normalized substring check) for both real single-county rows
    (an exact real name is trivially its own substring) and combined-
    territory labels (a real county name appearing inside a multi-name
    label) - verified that no two of Florida's 67 real county names are
    substrings of each other, so this one check is safe for both cases
    without a separate exact-match code path.
    """
    return [r for r in rows if r.get("county") and _county_in_alert(search_county, r["county"])]


def _normalize_closed_events(closed_events, peak_field):
    """
    Common shape for a resolved (closed) event regardless of source -
    same fields as _normalize_open_events() except there's no "current"
    reading (the event is over) and "duration" is bounded between
    start_time and end_time rather than start_time and now.
    """
    return [{
        "utility": e["utility"],
        "county": e["county"],
        "peak_customers": e[peak_field],
        "peak_percentage_out": e.get("peak_percentage_out"),
        "customers_served": e.get("customers_served"),
        "start_time": e["start_time"],
        "end_time": e["end_time"],
        "duration": _duration_since(e["start_time"], e["end_time"]),
    } for e in closed_events]


# Generous enough that a specific county's real history isn't crowded
# out by other counties' more frequent recent closures within the same
# global "most recent N" cut each get_X_recent_closed_*() call makes -
# in practice this project's real closure counts per source are nowhere
# near this over its whole life (started polling 2026-04), so this
# reads as "effectively all of it" rather than a real cap.
_CLOSED_EVENTS_LIMIT = 500


def _real_per_county_closed_events(db):
    """
    Every resolved event from a source whose "county" field is a real,
    single Florida county - the closed-event counterpart to
    _real_per_county_open_events() above, same source list, same
    real-vs-combined-territory split.
    """
    limit = _CLOSED_EVENTS_LIMIT
    return (
        _normalize_closed_events(db.get_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_teco_recent_closed_events(limit=limit), "peak_customer_count")
        + _normalize_closed_events(db.get_duke_recent_closed_events(limit=limit), "peak_customer_count")
        + _normalize_closed_events(db.get_jea_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_tallahassee_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_talquin_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_preco_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_fkec_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_lwbu_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_ouc_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_lcec_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_fpuc_recent_closed_incidents(limit=limit), "peak_customer_count")
    )


def _combined_territory_closed_events(db):
    """
    Every resolved event from a combined-territory source - the closed-
    event counterpart to _combined_territory_open_events() above, same
    source list. Kept as its own distinct group for the same reason:
    never mixed in with real per-county rows.
    """
    limit = _CLOSED_EVENTS_LIMIT
    return (
        _normalize_closed_events(db.get_fpuc_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_tcec_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_erec_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_chelco_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_gcec_recent_closed_events(limit=limit), "peak_customers_out")
    )


# Raw-count fallback tiers for incident-level sources (TECO, Duke,
# Tallahassee, FPUC's incidents) that have no clean per-incident
# denominator to compute a real percentage against - same "tiered by
# percentage where a customer base is known, falling back to a raw-
# count tier otherwise" approach already used for the live-severity map
# view. Thresholds are a coarse, honestly-labeled judgment call (not
# derived from any real statistical baseline), same spirit as every
# other severity tier in this project.
_RAW_COUNT_TIER_THRESHOLDS = [(2000, "critical"), (500, "high"), (50, "medium")]


def _row_tier(row):
    """
    Severity tier for a single normalized open-event row (see
    _normalize_open_events): percentage-based when the source carries a
    real percentage, a coarser raw-count tier otherwise. Returns "low"
    for anything below every threshold, never "unknown" - a currently-
    open event is, by definition, worth showing as at least low
    severity, not "no data."
    """
    percentage = row.get("peak_percentage_out")
    if percentage is not None:
        return _percentage_tier(percentage)

    count = row.get("peak_customers") or 0
    for threshold, tier in _RAW_COUNT_TIER_THRESHOLDS:
        if count >= threshold:
            return tier
    return "low"


def county_verdict(real_rows, combined_rows):
    """
    Collapse a county's real + combined-territory open events into one
    severity tier for map coloring / at-a-glance display - the worst
    (highest) tier among any currently-open event touching this county,
    or "clear" if there are none. Deliberately a single simple label,
    not a numeric score - matches this project's established preference
    for honest, coarse tiers over false-precision numbers.
    """
    all_rows = real_rows + combined_rows
    if not all_rows:
        return "clear"

    order = ["low", "medium", "high", "critical"]
    worst = "low"
    for row in all_rows:
        tier = _row_tier(row)
        if order.index(tier) > order.index(worst):
            worst = tier
    return worst


def all_county_verdicts(db, county_names=None):
    """
    verdict per county for every real Florida county in one pass - used
    by the public page's statewide map/hero, so it doesn't re-query the
    database once per county. Fetches the two open-event lists exactly
    once, then reuses _rows_for_county()/county_verdict() per county
    against those same in-memory lists.

    county_names defaults to COUNTY_PICKER_CHOICES (all 67); a smaller
    list can be passed for tests.
    """
    county_names = county_names if county_names is not None else COUNTY_PICKER_CHOICES
    real_rows = _real_per_county_open_events(db)
    combined_rows = _combined_territory_open_events(db)

    return {
        county: county_verdict(
            _rows_for_county(real_rows, county),
            _rows_for_county(combined_rows, county),
        )
        for county in county_names
    }


# Every real per-county correlation source - deliberately NOT the
# combined-territory ones (TCEC/EREC/CHELCO/GCEC/FPUC-combined), which
# always return empty by design (a multi-county label can never match
# a single-county alert), so including them here would just be
# needless work for a result that's always {}.
_REAL_CORRELATION_SOURCES = [
    (find_correlations, correlation_summary),
    (find_teco_correlations, teco_correlation_summary),
    (find_duke_correlations, duke_correlation_summary),
    (find_jea_correlations, correlation_summary),
    (find_tallahassee_correlations, correlation_summary),
    (find_talquin_correlations, correlation_summary),
    (find_fpuc_incident_correlations, duke_correlation_summary),
    (find_preco_correlations, correlation_summary),
    (find_fkec_correlations, correlation_summary),
    (find_tcec_correlations, correlation_summary),
    (find_erec_correlations, correlation_summary),
    (find_chelco_correlations, correlation_summary),
    (find_gcec_correlations, correlation_summary),
    (find_lwbu_correlations, correlation_summary),
    (find_ouc_correlations, correlation_summary),
    (find_lcec_correlations, correlation_summary),
]


# Real regression found 2026-07-18: a live source's own raw county
# spelling can differ from COUNTY_PICKER_CHOICES's canonical name by
# more than just casing/periods - FPL itself stores "De Soto"/"St
# Johns"/"St Lucie" (with a space) against this project's own canonical
# "DeSoto"/"St. Johns"/"St. Lucie". historical_confidence_tally()'s own
# docstring already claimed it matched COUNTY_PICKER_CHOICES casing "where
# possible", but nothing actually enforced that - a real weather-match
# for one of these three would have been silently invisible on the map
# (stored under "De Soto", looked up as "DESOTO", never found) the
# moment it happened. Confirmed via a real correlation test before
# fixing, not assumed.
_CANONICAL_COUNTY_BY_NORMALIZED = {
    c.lower().replace(".", "").replace(" ", ""): c for c in COUNTY_PICKER_CHOICES
}


def _canonicalize_county_name(raw_name):
    """
    Map a raw county string - whatever casing/spacing/punctuation a
    live source's own feed happens to use - to this project's one
    canonical spelling (COUNTY_PICKER_CHOICES), so a real match never
    ends up filed under a name the map's own county list will never
    recognize. Falls back to the raw name unchanged if it doesn't
    normalize-match any real county - keeps a genuine data problem
    visible instead of silently swallowing it.
    """
    key = raw_name.lower().replace(".", "").replace(" ", "")
    return _CANONICAL_COUNTY_BY_NORMALIZED.get(key, raw_name)


def historical_confidence_tally(db_path="outages.db"):
    """
    All-time weather-match confidence tally per county, combined across
    every real per-county source - "how often has this county's real
    outage history plausibly overlapped with real weather, and how
    confidently." Powers the public page's "Historical Pattern" map
    view, a genuinely different question from current live severity
    (all_county_verdicts() above): this one asks "does this county's
    track record look weather-driven," not "is anything open right
    now."

    Returns a dict keyed by county name (canonicalized to
    COUNTY_PICKER_CHOICES via _canonicalize_county_name - see its
    docstring for why this matters) to {"high": n, "medium": n,
    "low": n} - counties with no correlation history at all simply
    don't appear as a key, left to the caller to treat as "no data yet"
    rather than zero.
    """
    tally = {}
    for find_fn, summary_fn in _REAL_CORRELATION_SOURCES:
        matches = find_fn(db_path, days=None)
        if not matches:
            continue
        summary = summary_fn(matches)
        for county, stats in summary.items():
            canonical = _canonicalize_county_name(county)
            bucket = tally.setdefault(canonical, {"high": 0, "medium": 0, "low": 0})
            for tier, count in stats.get("confidence_breakdown", {}).items():
                if tier in bucket:
                    bucket[tier] += count
    return tally


def explain_missing_historical_data(county, db):
    """
    Real, computed explanation for why a county has no entry in the
    precomputed historical confidence tally (see
    OutageDatabase.get_historical_confidence_tally()) - for operator-
    facing display on dashboard.py's /county page. Deliberately computed
    fresh from live data on every call rather than hardcoded per-county
    text, so it stays accurate as a chronic source recovers or a new
    live source gets added, instead of quietly going stale.

    Returns None if the county already has a tally entry (nothing to
    explain). Otherwise a dict with one of three honest reasons, built
    from exactly what's actually known right now:
    - "combined_only": only a combined-territory source (utilities
      named) covers this county - its one shared multi-county number
      can't be honestly attributed to this county's own local weather.
    - "no_live_source": no live source at all currently reports this
      county (shouldn't happen at full 67-county coverage, but a real
      fallback rather than a silent crash if it ever did).
    - "not_yet_matched": a real per-county source does cover this
      county (utilities + real event count named) - it just hasn't
      logged an outage that overlapped an active NWS alert yet. Often
      resolves on its own with time, not a bug to chase.
    """
    tally = db.get_historical_confidence_tally()
    if any(c.upper() == county.upper() for c in tally):
        return None

    real_events = _rows_for_county(
        _real_per_county_open_events(db) + _real_per_county_closed_events(db), county
    )
    if real_events:
        return {
            "reason": "not_yet_matched",
            "utilities": sorted({e["utility"] for e in real_events}),
            "real_event_count": len(real_events),
        }

    combined_events = _rows_for_county(
        _combined_territory_open_events(db) + _combined_territory_closed_events(db), county
    )
    if combined_events:
        return {"reason": "combined_only", "utilities": sorted({e["utility"] for e in combined_events})}

    return {"reason": "no_live_source", "utilities": []}


FPL_UTILITY_NAME = "Florida Power and Light Company"

# A real single restoration job plausibly taking longer than this is
# rare enough to distrust - FPL's live feed only ever reports a county-
# wide customer-out total, and a busy county's aggregate often never
# fully returns to zero between separate real outages, so one
# continuous "event" here can actually be several real repair jobs
# blurred together (confirmed directly, 2026-07-18: checking this
# project's own 484 closed FPL live events statewide, 95% run under 41
# hours and the p99 is ~90 hours, then a sharp jump straight to 217 and
# 254 hours for two counties - a real, identifiable break in the data,
# not an arbitrary guess).
MAX_PLAUSIBLE_SINGLE_OUTAGE_HOURS = 96

# Below this many usable events, a "range" is really just a couple of
# data points wearing a range's clothing - same reasoning as
# storm_history.MIN_STORMS_FOR_CONFIDENT_RANGE, mirrored here since this
# counts live everyday events instead of storms.
MIN_EVENTS_FOR_CONFIDENT_RANGE = 3


def fpl_ordinary_restoration_stats(county, db):
    """
    Real restoration-duration precedent for one FPL county from this
    project's OWN live tracking (since 2026-04) - deliberately a
    separate, distinctly-labeled number from
    storm_history.fpl_restoration_precedent()'s major-storm archive, not
    merged into it. The two honestly answer different questions: "how
    long does an ordinary outage take here" vs. "how long does a major
    storm's damage take to fix here" - blending them would misrepresent
    both. This one has vastly more real data (hundreds of events per
    county vs. a handful of storms) but needs one honest filter: any
    single event longer than MAX_PLAUSIBLE_SINGLE_OUTAGE_HOURS is
    excluded as a likely blurred multi-outage reading rather than one
    real repair job (see that constant's own comment for the real
    numbers behind the cutoff).

    Returns None if there's no usable data for this county at all
    (either no real FPL events ever, or every one of them got excluded
    as an outlier). Otherwise a dict: n (usable event count), min_hours/
    median_hours/max_hours (real durations), limited=True once n is too
    small for a range to mean much, and excluded_count (how many
    outlier events were left out, for transparency).
    """
    conn = db.connect()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT start_time, end_time FROM outage_events
        WHERE UPPER(county) = UPPER(?) AND utility = ? AND end_time IS NOT NULL
    ''', (county, FPL_UTILITY_NAME))
    rows = cursor.fetchall()

    durations = []
    excluded_count = 0
    for start_time, end_time in rows:
        try:
            hours = (datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if hours <= 0:
            continue
        if hours > MAX_PLAUSIBLE_SINGLE_OUTAGE_HOURS:
            excluded_count += 1
            continue
        durations.append(hours)

    if not durations:
        return None

    durations.sort()
    n = len(durations)
    mid = n // 2
    median = durations[mid] if n % 2 == 1 else (durations[mid - 1] + durations[mid]) / 2

    return {
        "n": n,
        "min_hours": durations[0],
        "median_hours": median,
        "max_hours": durations[-1],
        "limited": n < MIN_EVENTS_FOR_CONFIDENT_RANGE,
        "excluded_count": excluded_count,
    }


TECO_UTILITY_NAME = "Tampa Electric Company"


def teco_etr_accuracy(county, db):
    """
    Real accuracy check of TECO's own stated restoration estimates
    against what actually happened, for one county - a genuinely
    different kind of Phase 3 signal than FPL's historical-precedent
    approach. TECO already reports a real per-incident ETR on 3,776 of
    3,776 real closed incidents checked 2026-07-18, so instead of
    inventing a range from scratch (FPL's problem - no per-incident data
    at all), this checks how trustworthy TECO's own existing number has
    actually been: "when TECO tells you when your power will be back,
    how close does that number usually land?"

    Confirmed 2026-07-18 this is TECO-specific, not a Duke/JEA feature
    too: Duke's raw feed has no restoration-estimate field at all, and
    JEA has no per-incident data at all (county-rollup only, like FPL).
    FPUC's real incident view and LWBU both technically have an ETR
    field, but only 3 and 8 real closed incidents respectively right
    now - too thin to mean anything yet, revisit once they accumulate
    more.

    For each resolved incident, compares its FIRST reported ETR (the
    earliest raw snapshot with one) against when it actually closed - no
    outlier filtering needed here the way fpl_ordinary_restoration_stats
    needs one, since TECO's incidents are already individually tracked
    with a real incident_id, not a blurred county-wide aggregate.

    Returns None if there's no usable data for this county. Otherwise a
    dict: n, median_error_hours (positive = resolved later than first
    promised, negative = resolved earlier), on_time_pct (share resolved
    at or before their first stated ETR), limited (n too small to mean
    much).
    """
    conn = db.connect()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT oe.end_time,
               (SELECT ti.estimated_restoration FROM teco_incidents ti
                WHERE ti.incident_id = oe.incident_id AND ti.estimated_restoration IS NOT NULL
                ORDER BY ti.fetched_at ASC LIMIT 1) AS first_etr
        FROM teco_incident_events oe
        WHERE UPPER(oe.county) = UPPER(?) AND oe.end_time IS NOT NULL
    ''', (county,))
    rows = cursor.fetchall()

    errors = []
    for end_time, first_etr in rows:
        if not first_etr:
            continue
        try:
            error_hours = (datetime.fromisoformat(end_time) - datetime.fromisoformat(first_etr)).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        errors.append(error_hours)

    if not errors:
        return None

    errors.sort()
    n = len(errors)
    mid = n // 2
    median = errors[mid] if n % 2 == 1 else (errors[mid - 1] + errors[mid]) / 2
    on_time = sum(1 for e in errors if e <= 0)

    return {
        "n": n,
        "median_error_hours": median,
        "on_time_pct": on_time / n * 100,
        "limited": n < MIN_EVENTS_FOR_CONFIDENT_RANGE,
    }


DUKE_UTILITY_NAME = "Duke Energy"


def duke_restoration_precedent(county, db):
    """
    Real restoration-duration precedent for one Duke county from this
    project's own live tracking - the same underlying question as
    fpl_ordinary_restoration_stats(), but Duke doesn't need that
    function's outlier-exclusion filter, and isn't paired with a
    "Major Storms" sibling the way FPL's "Everyday Outages" is.

    Checked directly before building, 2026-07-18: unlike FPL (a county-
    wide aggregate that often never resets to zero, blurring several
    real outages into one reading), Duke already reports real,
    individually-tracked incidents with their own incident_id - the
    same shape as TECO. 7,195 real closed incidents statewide, median
    1.3 hours, only 1 single incident over 48 hours, none over 96 -
    genuinely clean data, no blurring problem to filter for. Duke has
    no restoration-estimate field at all (unlike TECO), so there's
    nothing to check accuracy against either - this is the only honest
    restoration signal available for it, a plain duration precedent
    like FPL's, just without FPL's contamination risk or its separate
    storm archive.

    Returns None if there's no usable data for this county. Otherwise a
    dict: n, min_hours/median_hours/max_hours, limited (n too small to
    mean much).
    """
    conn = db.connect()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT start_time, end_time FROM duke_incident_events
        WHERE UPPER(county) = UPPER(?) AND utility = ? AND end_time IS NOT NULL
    ''', (county, DUKE_UTILITY_NAME))
    rows = cursor.fetchall()

    durations = []
    for start_time, end_time in rows:
        try:
            hours = (datetime.fromisoformat(end_time) - datetime.fromisoformat(start_time)).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if hours > 0:
            durations.append(hours)

    if not durations:
        return None

    durations.sort()
    n = len(durations)
    mid = n // 2
    median = durations[mid] if n % 2 == 1 else (durations[mid - 1] + durations[mid]) / 2

    return {
        "n": n,
        "min_hours": durations[0],
        "median_hours": median,
        "max_hours": durations[-1],
        "limited": n < MIN_EVENTS_FOR_CONFIDENT_RANGE,
    }
