import glob
import os
import re
from datetime import datetime

from pypdf import PdfReader

from database import OutageDatabase


TIMESTAMP_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)")

# "Florida Power and Light Company ALACHUA 1,280 0 0.00% Not Significantly Impacted"
ROW_FULL_RE = re.compile(
    r"^(?P<provider>.+?)\s+(?P<county>[A-Z][A-Z.\s]*[A-Z])\s+"
    r"(?P<customers>[\d,]+)\s+(?P<out>[\d,]+)\s+(?P<pct>[\d.]+)%\s+(?P<restore>.+)$"
)

# Some already-restored rows drop the "out" column entirely, e.g.
# "Florida Power and Light Company BREVARD 345,490 0.00% Restored"
ROW_NO_OUT_RE = re.compile(
    r"^(?P<provider>.+?)\s+(?P<county>[A-Z][A-Z.\s]*[A-Z])\s+"
    r"(?P<customers>[\d,]+)\s+(?P<pct>[\d.]+)%\s+(?P<restore>.+)$"
)

# Some zero-customer rows drop the percentage entirely, e.g.
# "Duke Energy HARDEE 0 0 Restored"
ROW_NO_PCT_RE = re.compile(
    r"^(?P<provider>.+?)\s+(?P<county>[A-Z][A-Z.\s]*[A-Z])\s+"
    r"(?P<customers>[\d,]+)\s+(?P<out>[\d,]+)\s+(?P<restore>[A-Za-z].+)$"
)


# All 67 real Florida counties, in the spelling/format the PSC reports
# actually use. Extracted "county" values are validated against this -
# garbled PDF text extraction can otherwise leak fragments (e.g. "C
# ALACHUA", "BEACH" from a broken "PALM BEACH") through as if they were
# real rows.
FLORIDA_COUNTIES = {
    "ALACHUA", "BAKER", "BAY", "BRADFORD", "BREVARD", "BROWARD", "CALHOUN",
    "CHARLOTTE", "CITRUS", "CLAY", "COLLIER", "COLUMBIA", "DESOTO", "DIXIE",
    "DUVAL", "ESCAMBIA", "FLAGLER", "FRANKLIN", "GADSDEN", "GILCHRIST",
    "GLADES", "GULF", "HAMILTON", "HARDEE", "HENDRY", "HERNANDO",
    "HIGHLANDS", "HILLSBOROUGH", "HOLMES", "INDIAN RIVER", "JACKSON",
    "JEFFERSON", "LAFAYETTE", "LAKE", "LEE", "LEON", "LEVY", "LIBERTY",
    "MADISON", "MANATEE", "MARION", "MARTIN", "MIAMI-DADE", "MONROE",
    "NASSAU", "OKALOOSA", "OKEECHOBEE", "ORANGE", "OSCEOLA", "PALM BEACH",
    "PASCO", "PINELLAS", "POLK", "PUTNAM", "ST. JOHNS", "ST. LUCIE",
    "SANTA ROSA", "SARASOTA", "SEMINOLE", "SUMTER", "SUWANNEE", "TAYLOR",
    "UNION", "VOLUSIA", "WAKULLA", "WALTON", "WASHINGTON",
}


def _normalize(text):
    return text.lower().replace(".", "").strip()


FLORIDA_COUNTIES_NORMALIZED = {_normalize(c) for c in FLORIDA_COUNTIES}


def _is_real_county(county):
    return _normalize(county) in FLORIDA_COUNTIES_NORMALIZED


def _parse_int(value):
    return int(value.replace(",", ""))


def parse_report_timestamp(text):
    """
    Extract the report's Date/Time header, e.g. "10/16/2024  8:44 PM", as a
    naive datetime.
    """
    match = TIMESTAMP_RE.search(text)
    if not match:
        return None

    date_str = match.group(1)
    time_str = match.group(2).replace(" ", "")
    return datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M%p")


def parse_esf12_report(pdf_path):
    """
    Parse a Florida PSC ESF12 outage situation report PDF.

    Returns (report_timestamp, records) where records is a list of dicts
    shaped like {"utility", "county", "customers_out", "customers_served"}
    - covers every utility listed in the report (FPL, Duke Energy, TECO,
    JEA, co-ops, municipals, etc.), not just one.
    """
    with open(pdf_path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        # Some URLs 200 with an HTML "not found" page instead of a real PDF
        return None, []

    reader = PdfReader(pdf_path)
    full_text = "\n".join(page.extract_text() for page in reader.pages)

    report_timestamp = parse_report_timestamp(full_text)

    records = []
    for line in full_text.split("\n"):
        line = line.strip()
        county = customers_out = customers_served = None

        match = ROW_FULL_RE.match(line)
        if match:
            county = match.group("county").strip()
            customers_out = _parse_int(match.group("out"))
            customers_served = _parse_int(match.group("customers"))
        else:
            match = ROW_NO_OUT_RE.match(line)
            if match:
                county = match.group("county").strip()
                customers_out = 0
                customers_served = _parse_int(match.group("customers"))
            else:
                match = ROW_NO_PCT_RE.match(line)
                if match:
                    county = match.group("county").strip()
                    customers_out = _parse_int(match.group("out"))
                    customers_served = _parse_int(match.group("customers"))

        if match is None or not _is_real_county(county):
            continue

        utility = match.group("provider").strip()
        if not utility or not utility[0].isupper():
            # Broken table extraction in some PDFs truncates the provider
            # column mid-word (e.g. "mpa Electric Comp" from "Tampa
            # Electric Company") - a real utility name always starts
            # with a capital letter, a truncated fragment never does.
            continue

        records.append({
            "utility": utility,
            "county": county,
            "customers_out": customers_out,
            "customers_served": customers_served,
        })

    return report_timestamp, records


def import_report_series(pdf_paths, db_path):
    """
    Parse a series of ESF12 report PDFs (any order) and replay them in
    chronological order into db_path, building real outage_events history
    for every utility in the reports (not just FPL), exactly the way the
    live poller does cycle by cycle.

    Returns a summary dict: {"reports_parsed", "reports_skipped", "counties_seen", "utilities_seen"}
    """
    parsed = []
    skipped = []

    for path in pdf_paths:
        timestamp, records = parse_esf12_report(path)
        if timestamp is None or not records:
            skipped.append(path)
            continue
        parsed.append((timestamp, records, path))

    parsed.sort(key=lambda item: item[0])

    db = OutageDatabase(db_path)
    counties_seen = set()
    utilities_seen = set()

    for timestamp, records, path in parsed:
        iso_timestamp = timestamp.isoformat()

        by_utility = {}
        for record in records:
            by_utility.setdefault(record["utility"], []).append(record)

        for utility, utility_records in by_utility.items():
            db.log_multiple_outages(utility, utility_records, timestamp=iso_timestamp)
            db.sync_outage_events(utility, utility_records, timestamp=iso_timestamp)

        counties_seen.update(r["county"] for r in records)
        utilities_seen.update(r["utility"] for r in records)

    db.close()

    return {
        "reports_parsed": len(parsed),
        "reports_skipped": len(skipped),
        "skipped_paths": skipped,
        "counties_seen": sorted(counties_seen),
        "utilities_seen": sorted(utilities_seen),
    }


def main():
    """
    Manual entry point: import every PDF in a directory (pass via
    MILTON_DIR env var or edit the default below) into historical_milton.db
    """
    pdf_dir = os.environ.get("MILTON_DIR", "milton_series")
    pdf_paths = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))

    if not pdf_paths:
        print(f"No PDFs found in {pdf_dir}")
        return

    summary = import_report_series(pdf_paths, db_path="historical_milton.db")
    print(f"Parsed {summary['reports_parsed']} reports, skipped {summary['reports_skipped']}")
    if summary["skipped_paths"]:
        print("Skipped (no timestamp or no rows found):")
        for p in summary["skipped_paths"]:
            print(" ", p)
    print(f"Counties seen: {len(summary['counties_seen'])}")


if __name__ == "__main__":
    main()
