"""
Cross-utility BI panels for the public Sentinel site's "Business
Intelligence" page - statewide rollups answering one real question each,
built from the same real tables every other page on this project reads.
Deliberately separate from county_status.py (which answers "what's true
for this one county") - everything here answers "how do the utilities
compare, across all of Florida."
"""
from datetime import datetime

from alerting import PIPELINE_SOURCE_DISPLAY_NAMES
from county_status import (
    MAX_PLAUSIBLE_SINGLE_OUTAGE_HOURS,
    MIN_EVENTS_FOR_CONFIDENT_RANGE,
    teco_etr_accuracy_statewide,
    fpl_etr_accuracy_statewide,
    lwbu_etr_accuracy_statewide,
    clay_etr_accuracy,
)


def _display_name(source):
    return PIPELINE_SOURCE_DISPLAY_NAMES.get(source, source.title())


def _percentile(sorted_values, pct):
    """Linear-interpolation percentile (nearest-rank would be too coarse
    for n in the single digits, which several of these sources have)."""
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def outage_frequency_by_utility(db):
    """
    "Who gets hit most?" - resolved outage count per utility, statewide,
    most first. Same real per-county closed tables and filter as the
    homepage's per-county chart, just rolled up by utility instead of
    county+month.
    """
    counts = db.get_closed_outage_counts_by_source()
    results = [
        {"source": source, "display_name": _display_name(source), "count": count}
        for source, count in counts.items()
    ]
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def restoration_duration_by_utility(db):
    """
    "Who takes longest to restore power?" - median/p90 outage duration
    per utility, statewide, longest-median first. Applies the same
    MAX_PLAUSIBLE_SINGLE_OUTAGE_HOURS outlier filter
    fpl_ordinary_restoration_stats() established (a county-wide rollup
    source's aggregate often never fully returns to zero between
    separate real outages, blurring several repairs into one implausibly
    long "event") - applied here across every source uniformly, not
    just FPL, since the same blurring risk applies to every county-wide
    rollup table, not only FPL's.
    """
    rows = db.get_closed_outage_durations_by_source()
    durations_by_source = {}
    excluded_by_source = {}
    for row in rows:
        try:
            hours = (
                datetime.fromisoformat(row["end_time"]) - datetime.fromisoformat(row["start_time"])
            ).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if hours <= 0:
            continue
        source = row["source"]
        if hours > MAX_PLAUSIBLE_SINGLE_OUTAGE_HOURS:
            excluded_by_source[source] = excluded_by_source.get(source, 0) + 1
            continue
        durations_by_source.setdefault(source, []).append(hours)

    results = []
    for source, durations in durations_by_source.items():
        durations.sort()
        n = len(durations)
        results.append({
            "source": source,
            "display_name": _display_name(source),
            "n": n,
            "median_hours": _percentile(durations, 50),
            "p90_hours": _percentile(durations, 90),
            "excluded_count": excluded_by_source.get(source, 0),
            "limited": n < MIN_EVENTS_FOR_CONFIDENT_RANGE,
        })
    results.sort(key=lambda r: r["median_hours"], reverse=True)
    return results


# source key -> the statewide ETR-accuracy function for that utility.
# Only the four sources that report a real per-incident restoration
# estimate at all (see each function's own docstring for why every
# other source is excluded, not just forgotten).
_ETR_ACCURACY_FUNCTIONS = {
    "teco": teco_etr_accuracy_statewide,
    "fpl": fpl_etr_accuracy_statewide,
    "lwbu": lwbu_etr_accuracy_statewide,
    "clay": clay_etr_accuracy,
}


def etr_accuracy_by_utility(db):
    """
    "Whose restoration estimate can you actually trust?" - on-time %
    and median error (positive = later than promised) per utility,
    statewide, most honest (highest on-time %) first. Duke has no
    restoration-estimate field at all; JEA/FPUC/the county-wide rollup
    sources have no per-incident tracking to check an estimate against -
    these four are the only ones with enough structure to answer this.
    """
    results = []
    for source, fn in _ETR_ACCURACY_FUNCTIONS.items():
        stats = fn(db)
        if stats is None:
            continue
        results.append({"source": source, "display_name": _display_name(source), **stats})
    results.sort(key=lambda r: r["on_time_pct"], reverse=True)
    return results


# Fixed display order + color slot for the real, meaningful cause
# categories - matches fetch_teco_outages.REASON_CATEGORIES, the one
# categorizer TECO/Duke/FPL/LWBU all share. "animal" and "vehicle" fold
# into "other_cause" (too small individually, statewide, to earn their
# own slot - see utility_cause_breakdown()'s docstring for the real
# counts behind that call). Order is fixed and never reassigned by
# rank, per this project's categorical-color convention.
CAUSE_DISPLAY_ORDER = ("equipment", "planned", "vegetation", "weather", "other_cause")
CAUSE_DISPLAY_NAMES = {
    "equipment": "Equipment",
    "planned": "Planned work",
    "vegetation": "Vegetation",
    "weather": "Weather",
    "other_cause": "Other / vehicle / animal",
}

# Categories that mean "no real cause was ever given" - kept out of the
# cause-mix chart entirely rather than lumped into "other_cause" above,
# since mixing "we don't know why" into a real cause bucket would
# misrepresent both. Tracked instead as its own transparency metric.
_NO_REAL_CAUSE = {"pending", "other", "unknown"}
_FOLD_INTO_OTHER_CAUSE = {"vehicle", "animal"}


def utility_cause_breakdown(db):
    """
    "What actually causes outages - and who tells you why?" Real finding
    behind this shape (checked against live data before building):
    TECO/FPL/LWBU's own free-text cause field usually DOES resolve to a
    real category (equipment/weather/vegetation/vehicle/animal/planned),
    but Duke's phrasing almost never matches the shared keyword buckets
    (classifies as "other" ~92% of the time) - not an absence of cause
    data, a mismatch between Duke's wording and this project's shared
    categorizer. Reporting a cause-mix pie for Duke alone would be
    almost entirely "other" and wouldn't be an honest comparison, so
    this leads with that transparency rate instead, then shows the real
    cause mix only among the incidents that DID resolve to one.

    Returns a dict: "transparency" (per-utility: n_total, n_real_cause,
    real_cause_pct, sorted most-transparent first) and "mix" (per-
    utility dict of CAUSE_DISPLAY_ORDER key -> count, real-cause
    incidents only).
    """
    rows = db.get_closed_incident_cause_counts()

    totals = {}
    real_cause_totals = {}
    mix = {}
    for row in rows:
        source, cause, n = row["source"], row["cause"], row["n"]
        totals[source] = totals.get(source, 0) + n
        if cause in _NO_REAL_CAUSE:
            continue
        real_cause_totals[source] = real_cause_totals.get(source, 0) + n
        bucket = "other_cause" if cause in _FOLD_INTO_OTHER_CAUSE else cause
        mix.setdefault(source, {}).setdefault(bucket, 0)
        mix[source][bucket] += n

    transparency = []
    for source, n_total in totals.items():
        n_real = real_cause_totals.get(source, 0)
        transparency.append({
            "source": source,
            "display_name": _display_name(source),
            "n_total": n_total,
            "n_real_cause": n_real,
            "real_cause_pct": n_real / n_total * 100 if n_total else 0,
        })
    transparency.sort(key=lambda r: r["real_cause_pct"], reverse=True)

    return {"transparency": transparency, "mix": mix}
