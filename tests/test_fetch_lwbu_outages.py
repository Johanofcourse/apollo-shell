"""
Tests for fetch_lwbu_outages.py - the summary-rollup parsing
(summary_to_records) and the incident-level parsing (parse_incidents),
added 2026-07-14 alongside the Lake Worth Beach Utilities integration.
Both real per-county correlation math AND real per-incident detail
(street/cause/crew/work status) come from the same real feed here, kept
as two independent, non-overlapping data shapes - see
fetch_lwbu_outages.py's own module docstring/comments for why.
"""

import fetch_lwbu_outages as lwbu


def _summary(customers_out_now=0, customers_served=28232):
    return {
        "customersAffected": customers_out_now,
        "customersRestored": 0,
        "customersOutNow": customers_out_now,
        "customersServed": customers_served,
        "updateTime": "2026-07-14T21:02:02.9667128-04:00",
        "hourlyCustomersOut": [],
        "streetsAffected": [],
    }


def _incident(rec_id="2026-07-14-0099", customers_out_now=2, lat=26.6, lng=-80.1,
              cause="Material or equipment fault/failure", crew_assigned=False,
              work_status="Crew in Route", streets=None, is_planned=False, verified=True,
              start_time="2026-07-14T18:40:50-04:00", etr=None):
    return {
        "outageRecID": rec_id,
        "outageName": "TF-25-A 4631 PENNY LN",
        "outagePoint": {"lat": lat, "lng": lng},
        "outageStartTime": start_time,
        "estimatedTimeOfRestoral": etr,
        "outageEndTime": None,
        "verified": verified,
        "cause": cause,
        "code": "300",
        "crewAssigned": crew_assigned,
        "customersOutInitially": customers_out_now,
        "customersOutNow": customers_out_now,
        "customersRestored": 0,
        "streetsAffected": streets if streets is not None else ["PENNY LN"],
        "outageModifiedTime": "2026-07-14T19:12:22.737-04:00",
        "outageWorkStatus": work_status,
        "isPlanned": is_planned,
    }


class TestSummaryToRecords:
    def test_parses_real_captured_shape(self):
        data = _summary(customers_out_now=2, customers_served=28232)
        records = lwbu.summary_to_records(data)
        assert records == [{"county": "Palm Beach", "customers_out": 2, "customers_served": 28232}]

    def test_zero_customers_out_still_returns_a_record(self):
        # A quiet day is still a real record, not "no data" - this feed
        # always reports its city-wide total, unlike Duke's declared-
        # event-only county figures.
        data = _summary(customers_out_now=0, customers_served=28232)
        records = lwbu.summary_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_customers_out_now_defaults_to_zero(self):
        data = {"customersServed": 28232}
        records = lwbu.summary_to_records(data)
        assert records[0]["customers_out"] == 0

    def test_missing_customers_served_defaults_to_zero(self):
        data = {"customersOutNow": 5}
        records = lwbu.summary_to_records(data)
        assert records[0]["customers_served"] == 0

    def test_none_data_returns_empty(self):
        assert lwbu.summary_to_records(None) == []

    def test_empty_dict_returns_empty(self):
        assert lwbu.summary_to_records({}) == []


class TestGetLwbuRecords:
    def test_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(lwbu, "fetch_lwbu_summary", lambda: None)
        assert lwbu.get_lwbu_records() == []

    def test_fetch_success_returns_parsed_records(self, monkeypatch):
        monkeypatch.setattr(lwbu, "fetch_lwbu_summary", lambda: _summary(customers_out_now=3, customers_served=28232))
        records = lwbu.get_lwbu_records()
        assert records == [{"county": "Palm Beach", "customers_out": 3, "customers_served": 28232}]


class TestParseIncidents:
    def test_parses_real_captured_shape(self):
        records = lwbu.parse_incidents([_incident()])
        assert len(records) == 1
        r = records[0]
        assert r["utility"] == "Lake Worth Beach Utilities"
        assert r["incident_id"] == "2026-07-14-0099"
        assert r["customer_count"] == 2
        assert r["lat"] == 26.6
        assert r["lon"] == -80.1
        assert r["county"] == "Palm Beach"
        assert r["cause"] == "Material or equipment fault/failure"
        assert r["streets_affected"] == "PENNY LN"
        assert r["crew_assigned"] is False
        assert r["work_status"] == "Crew in Route"
        assert r["is_planned"] is False
        assert r["verified"] is True

    def test_cause_is_categorized_same_as_teco_duke_tallahassee(self):
        records = lwbu.parse_incidents([_incident(cause="Tree down")])
        assert records[0]["cause_category"] == "vegetation"

    def test_multiple_streets_joined_with_comma(self):
        records = lwbu.parse_incidents([_incident(streets=["PENNY LN", "MAIN ST"])])
        assert records[0]["streets_affected"] == "PENNY LN, MAIN ST"

    def test_no_streets_gives_empty_string_not_none(self):
        records = lwbu.parse_incidents([_incident(streets=[])])
        assert records[0]["streets_affected"] == ""

    def test_crew_assigned_true(self):
        records = lwbu.parse_incidents([_incident(crew_assigned=True)])
        assert records[0]["crew_assigned"] is True

    def test_missing_incident_id_becomes_none(self):
        raw = _incident()
        raw["outageRecID"] = None
        records = lwbu.parse_incidents([raw])
        assert records[0]["incident_id"] is None


class TestGetIncidentsSummary:
    def test_drops_incidents_with_no_id(self, monkeypatch):
        monkeypatch.setattr(
            lwbu, "fetch_lwbu_incidents",
            lambda: [_incident(rec_id="1"), _incident(rec_id=None), _incident(rec_id="2")],
        )
        records = lwbu.get_incidents_summary()
        assert [r["incident_id"] for r in records] == ["1", "2"]

    def test_empty_feed_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(lwbu, "fetch_lwbu_incidents", lambda: [])
        assert lwbu.get_incidents_summary() == []
