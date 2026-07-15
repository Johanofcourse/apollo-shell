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
    find_preco_correlations, find_fkec_correlations, find_lwbu_correlations,
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
        + _normalize_open_events(db.get_tallahassee_open_events(), "current_customer_count", "peak_customer_count")
        + _normalize_open_events(db.get_talquin_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_preco_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_fkec_open_events(), "current_customers_out", "peak_customers_out")
        + _normalize_open_events(db.get_lwbu_open_events(), "current_customers_out", "peak_customers_out")
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
        + _normalize_closed_events(db.get_tallahassee_recent_closed_events(limit=limit), "peak_customer_count")
        + _normalize_closed_events(db.get_talquin_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_preco_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_fkec_recent_closed_events(limit=limit), "peak_customers_out")
        + _normalize_closed_events(db.get_lwbu_recent_closed_events(limit=limit), "peak_customers_out")
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
    (find_fpuc_incident_correlations, correlation_summary),
    (find_preco_correlations, correlation_summary),
    (find_fkec_correlations, correlation_summary),
    (find_lwbu_correlations, correlation_summary),
]


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

    Returns a dict keyed by county name (matching COUNTY_PICKER_CHOICES
    casing where possible) to {"high": n, "medium": n, "low": n} -
    counties with no correlation history at all simply don't appear as
    a key, left to the caller to treat as "no data yet" rather than
    zero.
    """
    tally = {}
    for find_fn, summary_fn in _REAL_CORRELATION_SOURCES:
        matches = find_fn(db_path, days=None)
        if not matches:
            continue
        summary = summary_fn(matches)
        for county, stats in summary.items():
            bucket = tally.setdefault(county, {"high": 0, "medium": 0, "low": 0})
            for tier, count in stats.get("confidence_breakdown", {}).items():
                if tier in bucket:
                    bucket[tier] += count
    return tally
