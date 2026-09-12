"""Lex V2 code hook: dialog validation plus fulfillment.

Lex calls this function twice per turn when both hooks are enabled — once with
invocationSource == "DialogCodeHook" while slots are still being collected, and
once with "FulfillmentCodeHook" when every required slot is filled.

The contract is the response shape: `sessionState.dialogAction.type` tells Lex
what to do next (ElicitSlot, Delegate, Close), and `messages` is what the user hears.
"""

from __future__ import annotations

import logging
import os
import random
import string
from datetime import date, datetime, time
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
BOOKINGS_TABLE = os.environ["BOOKINGS_TABLE"]
OPEN_TIME = os.environ.get("OPEN_TIME", "09:00")
CLOSE_TIME = os.environ.get("CLOSE_TIME", "18:00")

APPOINTMENT_LABELS = {
    "dental": "dental check-up",
    "physician": "appointment with a physician",
    "eye": "eye test",
}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    LOG.info("invocationSource=%s intent=%s", event.get("invocationSource"), _intent_name(event))

    intent = _intent_name(event)
    source = event.get("invocationSource")

    if intent == "BookAppointment":
        if source == "DialogCodeHook":
            return _validate_booking(event)
        return _fulfil_booking(event)

    if intent == "CheckBooking":
        return _check_booking(event)

    if intent == "CancelBooking":
        return _cancel_booking(event)

    return _close(event, "Fulfilled", "I'm not sure how to help with that yet.")


# --------------------------------------------------------------------- dialog
def _validate_booking(event: dict[str, Any]) -> dict[str, Any]:
    slots = _slots(event)

    appointment_type = _slot_value(slots, "AppointmentType")
    if appointment_type and appointment_type.lower() not in APPOINTMENT_LABELS:
        return _elicit(
            event,
            "AppointmentType",
            "I can book dental, physician or eye appointments. Which would you like?",
            clear="AppointmentType",
        )

    raw_date = _slot_value(slots, "Date")
    if raw_date:
        parsed = _parse_date(raw_date)
        if parsed is None:
            return _elicit(event, "Date", "I didn't catch that date. What day would you like?", clear="Date")
        if parsed < date.today():
            return _elicit(
                event,
                "Date",
                "That date has already passed. What upcoming day works for you?",
                clear="Date",
            )
        if parsed.weekday() == 6:
            return _elicit(event, "Date", "We're closed on Sundays. Which other day works?", clear="Date")

    raw_time = _slot_value(slots, "Time")
    if raw_time:
        parsed_time = _parse_time(raw_time)
        if parsed_time is None:
            return _elicit(event, "Time", "What time would you like? For example, 3 pm.", clear="Time")
        if not _within_opening_hours(parsed_time):
            return _elicit(
                event,
                "Time",
                f"We're open between {OPEN_TIME} and {CLOSE_TIME}. What time in that window suits you?",
                clear="Time",
            )

    return _delegate(event)


# ----------------------------------------------------------------- fulfilment
def _fulfil_booking(event: dict[str, Any]) -> dict[str, Any]:
    slots = _slots(event)
    booking_ref = _booking_ref()

    item = {
        "bookingRef": booking_ref,
        "appointmentType": (_slot_value(slots, "AppointmentType") or "").lower(),
        "appointmentDate": _slot_value(slots, "Date") or "",
        "appointmentTime": _slot_value(slots, "Time") or "",
        "customerName": _slot_value(slots, "Name") or "",
        "status": "CONFIRMED",
        "createdAt": datetime.utcnow().isoformat(),
        "sessionId": event.get("sessionId", ""),
    }

    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(bookingRef)")
    except ClientError:
        LOG.exception("could not store booking")
        return _close(
            event,
            "Failed",
            "Something went wrong saving that booking. Please try again in a moment.",
        )

    label = APPOINTMENT_LABELS.get(item["appointmentType"], "appointment")
    message = (
        f"Done, {item['customerName']} — your {label} is booked for "
        f"{item['appointmentDate']} at {item['appointmentTime']}. "
        f"Your reference is {booking_ref}."
    )
    return _close(event, "Fulfilled", message)


def _check_booking(event: dict[str, Any]) -> dict[str, Any]:
    ref = (_slot_value(_slots(event), "BookingRef") or "").upper()
    item = _table().get_item(Key={"bookingRef": ref}).get("Item")

    if not item:
        return _close(event, "Fulfilled", f"I couldn't find a booking with reference {ref}.")

    return _close(
        event,
        "Fulfilled",
        f"Booking {ref} is {item.get('status', 'unknown').lower()}: "
        f"{item.get('appointmentType', 'appointment')} on {item.get('appointmentDate')} "
        f"at {item.get('appointmentTime')}.",
    )


def _cancel_booking(event: dict[str, Any]) -> dict[str, Any]:
    ref = (_slot_value(_slots(event), "BookingRef") or "").upper()

    try:
        _table().update_item(
            Key={"bookingRef": ref},
            UpdateExpression="SET #s = :cancelled, cancelledAt = :now",
            ConditionExpression="attribute_exists(bookingRef) AND #s <> :cancelled",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":cancelled": "CANCELLED",
                ":now": datetime.utcnow().isoformat(),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _close(
                event,
                "Fulfilled",
                f"I couldn't cancel {ref} — either it doesn't exist or it's already cancelled.",
            )
        raise

    return _close(event, "Fulfilled", f"Booking {ref} is cancelled. Anything else?")


# ------------------------------------------------------------------- helpers
def _parse_date(value: str) -> date | None:
    """Lex AMAZON.Date resolves to ISO dates, but partial values like 2026-W12 exist."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_time(value: str) -> time | None:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _within_opening_hours(value: time) -> bool:
    open_at = datetime.strptime(OPEN_TIME, "%H:%M").time()
    close_at = datetime.strptime(CLOSE_TIME, "%H:%M").time()
    return open_at <= value <= close_at


def _booking_ref() -> str:
    return "BK" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _table():
    return dynamodb.Table(BOOKINGS_TABLE)


def _intent_name(event: dict[str, Any]) -> str:
    return event.get("sessionState", {}).get("intent", {}).get("name", "")


def _slots(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("sessionState", {}).get("intent", {}).get("slots") or {}


def _slot_value(slots: dict[str, Any], name: str) -> str | None:
    slot = slots.get(name)
    if not slot:
        return None
    value = slot.get("value") or {}
    return value.get("interpretedValue") or value.get("originalValue")


# ------------------------------------------------------- Lex response shapes
def _delegate(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": {
            "dialogAction": {"type": "Delegate"},
            "intent": event["sessionState"]["intent"],
            "sessionAttributes": event["sessionState"].get("sessionAttributes", {}),
        }
    }


def _elicit(event: dict[str, Any], slot: str, message: str, clear: str | None = None) -> dict[str, Any]:
    intent = dict(event["sessionState"]["intent"])
    slots = dict(intent.get("slots") or {})
    if clear:
        slots[clear] = None
    intent["slots"] = slots

    return {
        "sessionState": {
            "dialogAction": {"type": "ElicitSlot", "slotToElicit": slot},
            "intent": intent,
            "sessionAttributes": event["sessionState"].get("sessionAttributes", {}),
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }


def _close(event: dict[str, Any], state: str, message: str) -> dict[str, Any]:
    intent = dict(event["sessionState"]["intent"])
    intent["state"] = state

    return {
        "sessionState": {
            "dialogAction": {"type": "Close"},
            "intent": intent,
            "sessionAttributes": event["sessionState"].get("sessionAttributes", {}),
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }
