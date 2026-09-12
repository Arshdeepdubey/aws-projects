#!/usr/bin/env python3
"""Seed sample orders so the agent's tools return something real.

    python seed_data.py --orders-table agentic-assistant-dev-orders
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


def iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


ORDERS = [
    {
        "orderId": "ORD-10428",
        "customerId": "CUST-1042",
        "status": "DELIVERED",
        "category": "electronics",
        "total": Decimal("249.00"),
        "currency": "USD",
        "orderedAt": iso(21),
        "deliveredAt": iso(16),
        "carrier": "UPS",
        "trackingNumber": "1Z999AA10123456784",
        "items": [{"sku": "HDPH-220", "name": "Noise-cancelling headphones", "qty": 1,
                   "price": Decimal("249.00")}],
    },
    {
        "orderId": "ORD-10512",
        "customerId": "CUST-1042",
        "status": "IN_TRANSIT",
        "category": "apparel",
        "total": Decimal("89.50"),
        "currency": "USD",
        "orderedAt": iso(3),
        "estimatedDelivery": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
        "carrier": "FedEx",
        "trackingNumber": "794657123456",
        "items": [
            {"sku": "JKT-004", "name": "Rain jacket", "qty": 1, "price": Decimal("74.50")},
            {"sku": "SCK-112", "name": "Wool socks", "qty": 1, "price": Decimal("15.00")},
        ],
    },
    {
        # Deliberately outside the electronics return window — exercises the
        # outside_return_window path.
        "orderId": "ORD-10190",
        "customerId": "CUST-1042",
        "status": "DELIVERED",
        "category": "electronics",
        "total": Decimal("1199.00"),
        "currency": "USD",
        "orderedAt": iso(75),
        "deliveredAt": iso(70),
        "carrier": "UPS",
        "trackingNumber": "1Z999AA10987654321",
        "items": [{"sku": "LAP-15X", "name": "15-inch laptop", "qty": 1, "price": Decimal("1199.00")}],
    },
    {
        "orderId": "ORD-20001",
        "customerId": "CUST-2077",
        "status": "PROCESSING",
        "category": "home",
        "total": Decimal("64.99"),
        "currency": "USD",
        "orderedAt": iso(1),
        "estimatedDelivery": (datetime.now(timezone.utc) + timedelta(days=6)).date().isoformat(),
        "items": [{"sku": "LMP-330", "name": "Desk lamp", "qty": 1, "price": Decimal("64.99")}],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders-table", required=True)
    parser.add_argument("--region")
    args = parser.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.orders_table)

    for order in ORDERS:
        table.put_item(Item=order)
        print(f"seeded {order['orderId']}  {order['status']:<12} {order['customerId']}")

    print(f"\n{len(ORDERS)} orders written to {args.orders_table}")
    print("try: \"Where is order ORD-10512?\" or \"Can I return ORD-10190?\"")


if __name__ == "__main__":
    main()
