"""Record every CloudWatch alarm state change into DynamoDB.

EventBridge delivers one event per transition. Storing them gives the daily report
a real history — transition counts, time spent in ALARM, which alarms flap — none
of which you can reconstruct from describe_alarms at report time.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
HISTORY_TABLE = os.environ["HISTORY_TABLE"]
TTL_DAYS = int(os.environ.get("HISTORY_TTL_DAYS", 90))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail = event.get("detail", {})
    alarm_name = detail.get("alarmName") or event.get("resources", [""])[0]

    if not alarm_name:
        LOG.warning("event without an alarm name: %s", json.dumps(event)[:500])
        return {"recorded": False}

    state = detail.get("state", {})
    previous = detail.get("previousState", {})
    changed_at = state.get("timestamp") or event.get("time") or _now_iso()

    item = {
        "alarmName": alarm_name,
        "changedAt": changed_at,
        "reportDay": changed_at[:10],
        "state": state.get("value", "UNKNOWN"),
        "previousState": previous.get("value", "UNKNOWN"),
        "reason": _truncate(state.get("reason", ""), 1000),
        "region": event.get("region", ""),
        "accountId": event.get("account", ""),
        "alarmArn": (event.get("resources") or [""])[0],
        "expiresAt": int((datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)).timestamp()),
    }

    dynamodb.Table(HISTORY_TABLE).put_item(Item=item)
    LOG.info("recorded %s: %s -> %s", alarm_name, item["previousState"], item["state"])

    return {"recorded": True, "alarmName": alarm_name, "state": item["state"]}


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
