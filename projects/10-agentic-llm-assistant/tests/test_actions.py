"""Tool tests — the agent's behaviour depends on these returning exactly this shape."""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

REGION = "us-east-1"
ORDERS = "test-orders"
TICKETS = "test-tickets"


@pytest.fixture
def actions(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("ORDERS_TABLE", ORDERS)
    monkeypatch.setenv("TICKETS_TABLE", TICKETS)

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=ORDERS,
            KeySchema=[{"AttributeName": "orderId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "orderId", "AttributeType": "S"},
                {"AttributeName": "customerId", "AttributeType": "S"},
                {"AttributeName": "orderedAt", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "customer-orderedAt-index",
                    "KeySchema": [
                        {"AttributeName": "customerId", "KeyType": "HASH"},
                        {"AttributeName": "orderedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        ddb.create_table(
            TableName=TICKETS,
            KeySchema=[{"AttributeName": "ticketId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "ticketId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "actions"))
        module = importlib.import_module("handler")
        importlib.reload(module)

        table = boto3.resource("dynamodb", region_name=REGION).Table(ORDERS)
        table.put_item(
            Item={
                "orderId": "ORD-10428",
                "customerId": "CUST-1042",
                "status": "DELIVERED",
                "category": "electronics",
                "total": Decimal("249.00"),
                "orderedAt": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
                "deliveredAt": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
                "cardNumber": "4111111111111111",  # must never be returned
            }
        )
        table.put_item(
            Item={
                "orderId": "ORD-10190",
                "customerId": "CUST-1042",
                "status": "DELIVERED",
                "category": "electronics",
                "total": Decimal("1199.00"),
                "orderedAt": (datetime.now(timezone.utc) - timedelta(days=75)).isoformat(),
                "deliveredAt": (datetime.now(timezone.utc) - timedelta(days=70)).isoformat(),
            }
        )

        yield module


def _event(function: str, **params) -> dict:
    return {
        "actionGroup": "customer-operations",
        "function": function,
        "parameters": [
            {"name": key, "type": _type_of(value), "value": str(value)}
            for key, value in params.items()
        ],
    }


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _body(response: dict) -> dict:
    return json.loads(response["response"]["functionResponse"]["responseBody"]["TEXT"]["body"])


def test_get_order_returns_the_record(actions):
    body = _body(actions.lambda_handler(_event("get_order", orderId="ORD-10428"), None))

    assert body["orderId"] == "ORD-10428"
    assert body["status"] == "DELIVERED"


def test_get_order_never_leaks_payment_details(actions):
    body = _body(actions.lambda_handler(_event("get_order", orderId="ORD-10428"), None))

    assert "cardNumber" not in body
    assert "4111" not in json.dumps(body)


def test_unknown_order_returns_an_actionable_error_not_an_exception(actions):
    body = _body(actions.lambda_handler(_event("get_order", orderId="ORD-99999"), None))

    assert body["error"] == "order_not_found"
    assert "hint" in body  # the agent needs to know what to do next


def test_malformed_order_id_is_rejected_before_the_lookup(actions):
    body = _body(actions.lambda_handler(_event("get_order", orderId="hello"), None))
    assert body["error"] == "invalid_order_id"


def test_list_orders_is_newest_first(actions):
    body = _body(actions.lambda_handler(_event("list_orders_for_customer", customerId="CUST-1042"), None))

    assert body["count"] == 2
    assert body["orders"][0]["orderId"] == "ORD-10428"  # more recent orderedAt


def test_shipping_is_deterministic_and_surcharges_remote_postcodes(actions):
    normal = _body(actions.lambda_handler(
        _event("estimate_shipping", destinationPostcode="10001", weightKg=2.0, speed="express"), None))
    remote = _body(actions.lambda_handler(
        _event("estimate_shipping", destinationPostcode="99501", weightKg=2.0, speed="express"), None))

    assert normal["cost"] == round(9.99 + 1.40 * 2, 2)
    assert remote["cost"] == round(normal["cost"] + 5.0, 2)
    assert remote["transitBusinessDays"] > normal["transitBusinessDays"]


def test_shipping_rejects_out_of_range_weight_with_a_route_forward(actions):
    body = _body(actions.lambda_handler(
        _event("estimate_shipping", destinationPostcode="10001", weightKg=120), None))

    assert body["error"] == "weight_out_of_range"
    assert "ticket" in body["hint"].lower()


def test_return_outside_the_window_is_refused_with_the_numbers(actions):
    body = _body(actions.lambda_handler(
        _event("start_return", orderId="ORD-10190", reason="changed my mind"), None))

    assert body["error"] == "outside_return_window"
    assert body["daysSinceDelivery"] > body["windowDays"]


def test_start_return_is_idempotent(actions):
    first = _body(actions.lambda_handler(
        _event("start_return", orderId="ORD-10428", reason="faulty", idempotencyKey="k1"), None))
    second = _body(actions.lambda_handler(
        _event("start_return", orderId="ORD-10428", reason="faulty", idempotencyKey="k1"), None))

    assert first["status"] == "started"
    assert second["status"] == "already_started"
    assert second["returnId"] == first["returnId"]


def test_create_ticket_is_idempotent_on_the_key(actions):
    first = _body(actions.lambda_handler(
        _event("create_ticket", customerId="CUST-1042", summary="Damaged item", idempotencyKey="t1"), None))
    second = _body(actions.lambda_handler(
        _event("create_ticket", customerId="CUST-1042", summary="Damaged item", idempotencyKey="t1"), None))

    assert first["created"] is True
    assert second["created"] is False
    assert first["ticketId"] == second["ticketId"]


def test_unknown_tool_lists_the_real_ones(actions):
    body = _body(actions.lambda_handler(_event("delete_everything"), None))

    assert body["error"] == "unknown_function"
    assert "get_order" in body["hint"]


def test_response_envelope_matches_what_bedrock_expects(actions):
    response = actions.lambda_handler(_event("get_return_policy", category="apparel"), None)

    assert response["messageVersion"] == "1.0"
    assert response["response"]["actionGroup"] == "customer-operations"
    assert response["response"]["function"] == "get_return_policy"
    assert "TEXT" in response["response"]["functionResponse"]["responseBody"]
