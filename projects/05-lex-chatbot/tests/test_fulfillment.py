"""Dialog logic tests — pure functions, no AWS calls."""

import os
import sys
from datetime import date, timedelta

import pytest

os.environ.setdefault("BOOKINGS_TABLE", "test-bookings")
os.environ.setdefault("OPEN_TIME", "09:00")
os.environ.setdefault("CLOSE_TIME", "18:00")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "fulfillment"))
import app  # noqa: E402


def _next_open_day() -> str:
    """Next day the clinic is open — the bot refuses Sundays, so never pick one."""
    day = date.today() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


def _event(slots: dict, intent: str = "BookAppointment", source: str = "DialogCodeHook") -> dict:
    return {
        "invocationSource": source,
        "sessionId": "test-session",
        "sessionState": {
            "sessionAttributes": {},
            "intent": {
                "name": intent,
                "state": "InProgress",
                "slots": {
                    key: {"value": {"interpretedValue": value}} if value is not None else None
                    for key, value in slots.items()
                },
            },
        },
    }


def test_delegates_when_everything_is_valid():
    response = app.lambda_handler(
        _event({"AppointmentType": "dental", "Date": _next_open_day(), "Time": "10:00"}), None
    )
    assert response["sessionState"]["dialogAction"]["type"] == "Delegate"


def test_rejects_a_past_date():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    response = app.lambda_handler(_event({"AppointmentType": "dental", "Date": yesterday}), None)

    action = response["sessionState"]["dialogAction"]
    assert action["type"] == "ElicitSlot"
    assert action["slotToElicit"] == "Date"
    assert response["sessionState"]["intent"]["slots"]["Date"] is None


def test_rejects_a_time_outside_opening_hours():
    response = app.lambda_handler(
        _event({"AppointmentType": "eye", "Date": _next_open_day(), "Time": "22:30"}), None
    )
    assert response["sessionState"]["dialogAction"]["slotToElicit"] == "Time"


def test_rejects_a_sunday():
    sunday = date.today() + timedelta(days=1)
    while sunday.weekday() != 6:
        sunday += timedelta(days=1)

    response = app.lambda_handler(
        _event({"AppointmentType": "dental", "Date": sunday.isoformat()}), None
    )
    action = response["sessionState"]["dialogAction"]

    assert action["type"] == "ElicitSlot"
    assert action["slotToElicit"] == "Date"


def test_rejects_an_unknown_appointment_type():
    response = app.lambda_handler(_event({"AppointmentType": "haircut"}), None)
    assert response["sessionState"]["dialogAction"]["slotToElicit"] == "AppointmentType"


@pytest.mark.parametrize("value,expected", [("09:00", True), ("18:00", True), ("08:59", False), ("18:01", False)])
def test_opening_hours_boundaries(value, expected):
    assert app._within_opening_hours(app._parse_time(value)) is expected


def test_booking_reference_shape():
    ref = app._booking_ref()
    assert ref.startswith("BK") and len(ref) == 8 and ref[2:].isalnum()
