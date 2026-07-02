import os
import sys
from datetime import datetime

from flask import Flask, render_template

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apollo_shell'))

from database import OutageDatabase
from correlate import find_correlations, correlation_summary


app = Flask(__name__)


def _duration_since(start_iso, end_iso=None):
    """
    Human-readable duration between two ISO timestamps (or start_iso and
    now, if end_iso is omitted).
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


def _format_alert_types(alert_types):
    """
    Turn {"Flood Advisory": 32, "Tornado Warning": 2} into
    "Flood Advisory ×32, Tornado Warning ×2"
    """
    return ", ".join(f"{name} ×{count}" for name, count in alert_types.items())


@app.route("/")
def index():
    db = OutageDatabase()
    db_path = db.db_path

    snapshot = db.get_latest_snapshot()
    open_events = db.get_open_events()
    closed_events = db.get_recent_closed_events(limit=10)
    weather_alerts = db.get_recent_weather_alerts(limit=10)

    db.close()

    for event in open_events:
        event["duration"] = _duration_since(event["start_time"])
    for event in closed_events:
        event["duration"] = _duration_since(event["start_time"], event["end_time"])

    matches = find_correlations(db_path)
    correlation = correlation_summary(matches)
    for stats in correlation.values():
        stats["alert_types_display"] = _format_alert_types(stats["alert_types"])

    return render_template(
        "dashboard.html",
        snapshot=snapshot,
        open_events=open_events,
        closed_events=closed_events,
        weather_alerts=weather_alerts,
        correlation=correlation,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
