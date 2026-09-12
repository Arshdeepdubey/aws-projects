"""Scheduled CloudWatch alarm report.

Pulls current alarm state from CloudWatch and transition history from DynamoDB,
renders HTML + CSV, archives both in S3, and emails the HTML with the CSV attached.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from render import render_csv_rows, render_html

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

cloudwatch = boto3.client("cloudwatch")
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
ses = boto3.client("ses")

HISTORY_TABLE = os.environ["HISTORY_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
RECIPIENTS = [e.strip() for e in os.environ.get("RECIPIENT_EMAILS", "").split(",") if e.strip()]
DEFAULT_WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", 24))
ALARM_NAME_PREFIX = os.environ.get("ALARM_NAME_PREFIX", "")
ACCOUNT_LABEL = os.environ.get("ACCOUNT_LABEL", "")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    window_hours = int(event.get("windowHours", DEFAULT_WINDOW_HOURS))
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)

    alarms = _describe_alarms()
    transitions = _transitions_since(since)
    summary = summarise(alarms, transitions, since, now)

    html = render_html(summary)
    csv_bytes = _csv_bytes(render_csv_rows(summary))

    keys = _archive(now, html, csv_bytes)

    sent = False
    if RECIPIENTS:
        sent = _email(summary, html, csv_bytes, now)
    else:
        LOG.warning("no recipients configured; report archived only")

    LOG.info(
        "report built: %d alarms, %d in ALARM, %d transitions",
        summary["counts"]["total"],
        summary["counts"]["ALARM"],
        len(transitions),
    )

    return {
        "windowHours": window_hours,
        "alarms": summary["counts"],
        "transitions": len(transitions),
        "archived": keys,
        "emailed": sent,
    }


# ------------------------------------------------------------------ gathering
def _describe_alarms() -> list[dict[str, Any]]:
    alarms: list[dict[str, Any]] = []
    paginator = cloudwatch.get_paginator("describe_alarms")

    kwargs: dict[str, Any] = {"AlarmTypes": ["MetricAlarm", "CompositeAlarm"]}
    if ALARM_NAME_PREFIX:
        kwargs["AlarmNamePrefix"] = ALARM_NAME_PREFIX

    for page in paginator.paginate(**kwargs):
        for alarm in page.get("MetricAlarms", []) + page.get("CompositeAlarms", []):
            alarms.append(
                {
                    "name": alarm.get("AlarmName", ""),
                    "state": alarm.get("StateValue", "UNKNOWN"),
                    "reason": alarm.get("StateReason", ""),
                    "updatedAt": _iso(alarm.get("StateUpdatedTimestamp")),
                    "description": alarm.get("AlarmDescription", "") or "",
                    "namespace": alarm.get("Namespace", ""),
                    "metric": alarm.get("MetricName", ""),
                    "actionsEnabled": bool(alarm.get("ActionsEnabled", False)),
                    "hasActions": bool(alarm.get("AlarmActions")),
                }
            )
    return alarms


def _transitions_since(since: datetime) -> list[dict[str, Any]]:
    """Read recorded transitions from the per-day GSI, one query per day touched."""
    table = dynamodb.Table(HISTORY_TABLE)
    rows: list[dict[str, Any]] = []
    since_iso = since.isoformat()

    day = since.date()
    today = datetime.now(timezone.utc).date()
    while day <= today:
        response = table.query(
            IndexName="reportDay-changedAt-index",
            KeyConditionExpression=Key("reportDay").eq(day.isoformat())
            & Key("changedAt").gte(since_iso),
        )
        rows.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            response = table.query(
                IndexName="reportDay-changedAt-index",
                KeyConditionExpression=Key("reportDay").eq(day.isoformat())
                & Key("changedAt").gte(since_iso),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            rows.extend(response.get("Items", []))

        day += timedelta(days=1)

    if ALARM_NAME_PREFIX:
        rows = [r for r in rows if str(r.get("alarmName", "")).startswith(ALARM_NAME_PREFIX)]

    return sorted(rows, key=lambda r: str(r.get("changedAt", "")), reverse=True)


# ----------------------------------------------------------------- aggregation
def summarise(
    alarms: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    since: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Pure aggregation — unit-tested without any AWS calls."""
    counts = Counter(a["state"] for a in alarms)

    per_alarm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transitions:
        per_alarm[str(row.get("alarmName", ""))].append(row)

    noisiest = sorted(
        (
            {
                "name": name,
                "transitions": len(rows),
                "toAlarm": sum(1 for r in rows if r.get("state") == "ALARM"),
                "lastState": rows[0].get("state", "") if rows else "",
                "lastChangedAt": rows[0].get("changedAt", "") if rows else "",
            }
            for name, rows in per_alarm.items()
        ),
        key=lambda item: (-item["transitions"], item["name"]),
    )

    firing = sorted(
        (a for a in alarms if a["state"] == "ALARM"),
        key=lambda a: a.get("updatedAt", ""),
        reverse=True,
    )

    stale = [a for a in alarms if a["state"] == "INSUFFICIENT_DATA"]
    unactioned = [a for a in alarms if not a["hasActions"] or not a["actionsEnabled"]]

    return {
        "generatedAt": now.isoformat(timespec="seconds"),
        "windowStart": since.isoformat(timespec="seconds"),
        "windowEnd": now.isoformat(timespec="seconds"),
        "windowHours": round((now - since).total_seconds() / 3600),
        "accountLabel": ACCOUNT_LABEL,
        "counts": {
            "total": len(alarms),
            "ALARM": counts.get("ALARM", 0),
            "OK": counts.get("OK", 0),
            "INSUFFICIENT_DATA": counts.get("INSUFFICIENT_DATA", 0),
        },
        "firing": firing,
        "noisiest": noisiest[:20],
        "transitions": transitions[:200],
        "stale": stale,
        "unactioned": unactioned,
    }


# ------------------------------------------------------------------- delivery
def _csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _archive(now: datetime, html: str, csv_bytes: bytes) -> dict[str, str]:
    stamp = now.strftime("%Y/%m/%d/%H%M")
    html_key = f"reports/{stamp}-alarm-report.html"
    csv_key = f"reports/{stamp}-alarm-report.csv"

    s3.put_object(Bucket=REPORTS_BUCKET, Key=html_key, Body=html.encode("utf-8"), ContentType="text/html")
    s3.put_object(Bucket=REPORTS_BUCKET, Key=csv_key, Body=csv_bytes, ContentType="text/csv")

    return {"html": html_key, "csv": csv_key}


def _email(summary: dict[str, Any], html: str, csv_bytes: bytes, now: datetime) -> bool:
    firing = summary["counts"]["ALARM"]
    subject = (
        f"[{'ACTION' if firing else 'OK'}] CloudWatch alarms — {firing} firing — "
        f"{now.strftime('%Y-%m-%d')}"
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SENDER_EMAIL
    message["To"] = ", ".join(RECIPIENTS)
    message.set_content(
        f"{firing} alarms in ALARM out of {summary['counts']['total']}. "
        "Open the HTML version for the full report."
    )
    message.add_alternative(html, subtype="html")
    message.add_attachment(
        csv_bytes,
        maintype="text",
        subtype="csv",
        filename=f"alarm-report-{now.strftime('%Y%m%d')}.csv",
    )

    ses.send_raw_email(
        Source=SENDER_EMAIL,
        Destinations=RECIPIENTS,
        RawMessage={"Data": message.as_bytes()},
    )
    return True


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(value or "")
