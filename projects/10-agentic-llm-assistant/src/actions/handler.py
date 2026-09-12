"""Action group Lambda — the agent's tools.

Bedrock Agents call this with an event naming the action group, the function, and
the parameters the model chose. The response must come back in the shape Bedrock
expects, or the agent sees a malformed tool result and usually loops.

Design rules applied here:
  * tools return structured data, never sentences for the user
  * every failure is a normal response with an `error` key and a `hint`, so the
    agent can recover in the same turn instead of retrying blindly
  * writes are idempotent on an explicit key
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
ORDERS_TABLE = os.environ["ORDERS_TABLE"]
TICKETS_TABLE = os.environ["TICKETS_TABLE"]

ORDER_ID_RE = re.compile(r"^ORD-[A-Za-z0-9]{3,12}$")
CUSTOMER_ID_RE = re.compile(r"^CUST-[A-Za-z0-9]{3,12}$")

RETURN_POLICIES = {
    "electronics": {"windowDays": 14, "conditions": "Unopened, or faulty within the window.",
                    "restockingFee": "0% unopened, 15% opened"},
    "apparel": {"windowDays": 30, "conditions": "Unworn with tags attached.", "restockingFee": "0%"},
    "home": {"windowDays": 30, "conditions": "Unused and in original packaging.", "restockingFee": "0%"},
    "grocery": {"windowDays": 0, "conditions": "Perishable items cannot be returned.",
                "restockingFee": "n/a"},
    "default": {"windowDays": 30, "conditions": "Unused and in original packaging.", "restockingFee": "0%"},
}

SHIPPING_RATES = {  # base cost, per-kg cost, transit days
    "standard": (4.99, 0.80, 5),
    "express": (9.99, 1.40, 2),
    "overnight": (19.99, 2.20, 1),
}
REMOTE_POSTCODE_PREFIXES = ("79", "80", "99")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    action_group = event.get("actionGroup", "")
    function_name = event.get("function") or event.get("apiPath", "")
    parameters = _parameters(event)

    LOG.info("tool=%s params=%s", function_name, json.dumps(parameters)[:500])

    handler = TOOLS.get(function_name)
    if handler is None:
        body = {"error": "unknown_function", "function": function_name,
                "hint": "Use one of: " + ", ".join(sorted(TOOLS))}
    else:
        try:
            body = handler(parameters)
        except ToolError as exc:
            body = exc.payload
        except ClientError:
            LOG.exception("AWS error in %s", function_name)
            body = {"error": "backend_unavailable",
                    "hint": "Tell the customer the system is briefly unavailable and offer a ticket."}
        except Exception:  # noqa: BLE001
            LOG.exception("unhandled error in %s", function_name)
            body = {"error": "internal_error", "hint": "Apologise and offer to open a ticket."}

    LOG.info("tool=%s result=%s", function_name, json.dumps(body, default=str)[:500])

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "function": function_name,
            "functionResponse": {
                "responseBody": {"TEXT": {"body": json.dumps(body, default=_json_default)}}
            },
        },
        "sessionAttributes": event.get("sessionAttributes", {}),
        "promptSessionAttributes": event.get("promptSessionAttributes", {}),
    }


class ToolError(Exception):
    def __init__(self, error: str, hint: str, **extra: Any):
        super().__init__(error)
        self.payload = {"error": error, "hint": hint, **extra}


def _parameters(event: dict[str, Any]) -> dict[str, Any]:
    """Bedrock sends [{name, type, value}]; normalise to a dict with real types."""
    result: dict[str, Any] = {}

    for parameter in event.get("parameters", []) or []:
        name = parameter.get("name")
        value = parameter.get("value")
        kind = (parameter.get("type") or "string").lower()

        if value is None:
            continue
        try:
            if kind == "integer":
                value = int(value)
            elif kind == "number":
                value = float(value)
            elif kind == "boolean":
                value = str(value).lower() in ("true", "1", "yes")
        except (TypeError, ValueError):
            raise ToolError(
                "invalid_parameter",
                f"{name} should be a {kind}. Ask the customer to restate it.",
                parameter=name,
            )
        result[name] = value

    # API-schema style action groups put arguments in requestBody instead.
    request_body = event.get("requestBody", {}).get("content", {}).get("application/json", {})
    for prop in request_body.get("properties", []) or []:
        result.setdefault(prop.get("name"), prop.get("value"))

    return result


# --------------------------------------------------------------------- tools
def get_order(params: dict[str, Any]) -> dict[str, Any]:
    order_id = str(params.get("orderId", "")).strip().upper()

    if not ORDER_ID_RE.match(order_id):
        raise ToolError(
            "invalid_order_id",
            "Order ids look like ORD-10428. Ask the customer to check their confirmation email.",
            received=order_id,
        )

    item = dynamodb.Table(ORDERS_TABLE).get_item(Key={"orderId": order_id}).get("Item")
    if not item:
        raise ToolError(
            "order_not_found",
            "No order with that id. Ask the customer to confirm it, or look up their recent "
            "orders with list_orders_for_customer.",
            orderId=order_id,
        )

    return _order_view(item)


def list_orders_for_customer(params: dict[str, Any]) -> dict[str, Any]:
    customer_id = str(params.get("customerId", "")).strip().upper()
    limit = min(int(params.get("limit", 5) or 5), 20)

    if not CUSTOMER_ID_RE.match(customer_id):
        raise ToolError(
            "invalid_customer_id",
            "Customer ids look like CUST-1042. Ask the customer for the id on their account page.",
            received=customer_id,
        )

    response = dynamodb.Table(ORDERS_TABLE).query(
        IndexName="customer-orderedAt-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("customerId").eq(customer_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    orders = [_order_view(item) for item in response.get("Items", [])]

    if not orders:
        raise ToolError(
            "no_orders",
            "This customer has no orders. Check the customer id, or offer to open a ticket.",
            customerId=customer_id,
        )

    return {"customerId": customer_id, "count": len(orders), "orders": orders}


def get_return_policy(params: dict[str, Any]) -> dict[str, Any]:
    category = str(params.get("category", "")).strip().lower()
    policy = RETURN_POLICIES.get(category, RETURN_POLICIES["default"])

    return {
        "category": category or "default",
        "windowDays": policy["windowDays"],
        "conditions": policy["conditions"],
        "restockingFee": policy["restockingFee"],
        "matchedExactly": category in RETURN_POLICIES,
    }


def estimate_shipping(params: dict[str, Any]) -> dict[str, Any]:
    postcode = str(params.get("destinationPostcode", "")).strip()
    speed = str(params.get("speed", "standard") or "standard").strip().lower()

    try:
        weight = float(params.get("weightKg", 0))
    except (TypeError, ValueError):
        raise ToolError("invalid_weight", "Ask the customer for the parcel weight in kilograms.")

    if weight <= 0 or weight > 50:
        raise ToolError(
            "weight_out_of_range",
            "We ship parcels between 0 and 50 kg. Heavier shipments need a freight quote — "
            "offer to open a ticket.",
            weightKg=weight,
        )

    if speed not in SHIPPING_RATES:
        raise ToolError(
            "invalid_speed",
            f"Speed must be one of {', '.join(SHIPPING_RATES)}.",
            received=speed,
        )

    base, per_kg, transit_days = SHIPPING_RATES[speed]
    remote = postcode.startswith(REMOTE_POSTCODE_PREFIXES)

    cost = base + per_kg * weight + (5.00 if remote else 0.0)
    days = transit_days + (2 if remote else 0)
    delivery = _add_business_days(datetime.now(timezone.utc), days)

    return {
        "destinationPostcode": postcode,
        "weightKg": round(weight, 2),
        "speed": speed,
        "cost": round(cost, 2),
        "currency": "USD",
        "remoteSurchargeApplied": remote,
        "transitBusinessDays": days,
        "estimatedDelivery": delivery.date().isoformat(),
    }


def start_return(params: dict[str, Any]) -> dict[str, Any]:
    order_id = str(params.get("orderId", "")).strip().upper()
    reason = str(params.get("reason", "")).strip()

    if not ORDER_ID_RE.match(order_id):
        raise ToolError("invalid_order_id", "Order ids look like ORD-10428.", received=order_id)
    if len(reason) < 3:
        raise ToolError("missing_reason", "Ask the customer why they are returning the item.")

    table = dynamodb.Table(ORDERS_TABLE)
    item = table.get_item(Key={"orderId": order_id}).get("Item")
    if not item:
        raise ToolError("order_not_found", "Confirm the order id with the customer.", orderId=order_id)

    if item.get("status") == "RETURN_STARTED":
        # Idempotent: a retried agent step must not open a second return.
        return {
            "orderId": order_id,
            "returnId": item.get("returnId", ""),
            "status": "already_started",
            "note": "A return was already started for this order.",
        }

    policy = RETURN_POLICIES.get(str(item.get("category", "")).lower(), RETURN_POLICIES["default"])
    delivered_at = item.get("deliveredAt")

    if delivered_at:
        delivered = datetime.fromisoformat(str(delivered_at).replace("Z", "+00:00"))
        days_since = (datetime.now(timezone.utc) - delivered).days
        if days_since > policy["windowDays"]:
            raise ToolError(
                "outside_return_window",
                "Explain that the window has closed and offer to open a ticket for an exception.",
                orderId=order_id,
                daysSinceDelivery=days_since,
                windowDays=policy["windowDays"],
            )

    return_id = "RET-" + _stable_suffix(params.get("idempotencyKey") or order_id + reason)

    table.update_item(
        Key={"orderId": order_id},
        UpdateExpression="SET #s = :status, returnId = :rid, returnReason = :reason, returnStartedAt = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":status": "RETURN_STARTED",
            ":rid": return_id,
            ":reason": reason[:500],
            ":now": _now(),
        },
    )

    return {
        "orderId": order_id,
        "returnId": return_id,
        "status": "started",
        "labelEmailed": True,
        "returnBy": (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat(),
    }


def create_ticket(params: dict[str, Any]) -> dict[str, Any]:
    customer_id = str(params.get("customerId", "")).strip().upper()
    summary = str(params.get("summary", "")).strip()
    priority = str(params.get("priority", "normal") or "normal").lower()

    if not summary:
        raise ToolError("missing_summary", "Summarise the issue in one line before escalating.")
    if priority not in ("low", "normal", "high"):
        priority = "normal"

    ticket_id = "TIC-" + _stable_suffix(
        params.get("idempotencyKey") or f"{customer_id}{summary}{datetime.now(timezone.utc):%Y%m%d%H}"
    )

    table = dynamodb.Table(TICKETS_TABLE)
    try:
        table.put_item(
            Item={
                "ticketId": ticket_id,
                "customerId": customer_id,
                "summary": summary[:200],
                "details": str(params.get("details", ""))[:4000],
                "priority": priority,
                "status": "OPEN",
                "createdAt": _now(),
                "source": "agent",
            },
            ConditionExpression="attribute_not_exists(ticketId)",
        )
        created = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        created = False  # same idempotency key — the ticket already exists

    return {
        "ticketId": ticket_id,
        "status": "OPEN",
        "priority": priority,
        "created": created,
        "expectedResponse": "1 business day" if priority == "high" else "2 business days",
    }


TOOLS = {
    "get_order": get_order,
    "list_orders_for_customer": list_orders_for_customer,
    "get_return_policy": get_return_policy,
    "estimate_shipping": estimate_shipping,
    "start_return": start_return,
    "create_ticket": create_ticket,
}


# ------------------------------------------------------------------- helpers
def _order_view(item: dict[str, Any]) -> dict[str, Any]:
    """Only fields the assistant may use. Never return payment details or internal notes."""
    return {
        "orderId": item.get("orderId"),
        "customerId": item.get("customerId"),
        "status": item.get("status"),
        "category": item.get("category"),
        "total": item.get("total"),
        "currency": item.get("currency", "USD"),
        "orderedAt": item.get("orderedAt"),
        "deliveredAt": item.get("deliveredAt"),
        "estimatedDelivery": item.get("estimatedDelivery"),
        "carrier": item.get("carrier"),
        "trackingNumber": item.get("trackingNumber"),
        "items": item.get("items", []),
        "returnId": item.get("returnId"),
    }


def _add_business_days(start: datetime, days: int) -> datetime:
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _stable_suffix(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
