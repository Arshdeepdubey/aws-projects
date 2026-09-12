"""Aggregation and rendering tests — no AWS, no network."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("HISTORY_TABLE", "t")
os.environ.setdefault("REPORTS_BUCKET", "b")
os.environ.setdefault("SENDER_EMAIL", "alerts@example.com")
os.environ.setdefault("RECIPIENT_EMAILS", "ops@example.com")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "reporter"))
import render  # noqa: E402


NOW = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(hours=24)


def _alarm(name, state, has_actions=True, updated="2026-09-11T07:00:00+00:00"):
    return {
        "name": name,
        "state": state,
        "reason": f"{name} is {state}",
        "updatedAt": updated,
        "description": "",
        "namespace": "AWS/ApplicationELB",
        "metric": "TargetResponseTime",
        "actionsEnabled": True,
        "hasActions": has_actions,
    }


def _transition(name, state, previous, when):
    return {
        "alarmName": name,
        "state": state,
        "previousState": previous,
        "changedAt": when,
        "reason": "Threshold crossed",
    }


@pytest.fixture
def summary():
    import app  # imported lazily so the env vars above are set first

    alarms = [
        _alarm("checkout-latency", "ALARM"),
        _alarm("queue-depth", "OK"),
        _alarm("batch-job-heartbeat", "INSUFFICIENT_DATA"),
        _alarm("legacy-cpu", "OK", has_actions=False),
    ]
    transitions = [
        _transition("checkout-latency", "ALARM", "OK", "2026-09-11T07:50:00+00:00"),
        _transition("checkout-latency", "OK", "ALARM", "2026-09-11T07:20:00+00:00"),
        _transition("checkout-latency", "ALARM", "OK", "2026-09-11T06:55:00+00:00"),
        _transition("queue-depth", "OK", "ALARM", "2026-09-11T03:10:00+00:00"),
    ]
    return app.summarise(alarms, transitions, SINCE, NOW)


def test_counts_per_state(summary):
    assert summary["counts"] == {
        "total": 4,
        "ALARM": 1,
        "OK": 2,
        "INSUFFICIENT_DATA": 1,
    }


def test_noisiest_is_ranked_by_transition_count(summary):
    assert summary["noisiest"][0]["name"] == "checkout-latency"
    assert summary["noisiest"][0]["transitions"] == 3
    assert summary["noisiest"][0]["toAlarm"] == 2


def test_flags_alarms_without_actions_and_without_data(summary):
    assert [a["name"] for a in summary["unactioned"]] == ["legacy-cpu"]
    assert [a["name"] for a in summary["stale"]] == ["batch-job-heartbeat"]


def test_html_contains_the_firing_alarm_and_escapes_input(summary):
    summary["firing"][0]["reason"] = "<script>alert(1)</script>"
    output = render.render_html(summary)

    assert "checkout-latency" in output
    assert "<script>" not in output
    assert "&lt;script&gt;" in output


def test_csv_rows_cover_every_section(summary):
    rows = render.render_csv_rows(summary)
    sections = {row[0] for row in rows[1:]}

    assert sections == {"firing", "noisiest", "transition", "stale", "unactioned"}
    assert rows[0] == ["section", "alarm", "field1", "field2", "field3"]
