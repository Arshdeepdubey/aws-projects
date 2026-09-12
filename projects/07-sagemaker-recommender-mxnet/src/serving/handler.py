"""Recommendations API.

GET /users/{userId}/recommendations?limit=10

Reads the precomputed top-N from DynamoDB. If the user has no row (a cold user, or
one who signed up after the last batch run) and a real-time endpoint is configured,
falls back to scoring the popular-item shortlist on the endpoint.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any

import boto3

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
runtime = boto3.client("sagemaker-runtime")

RECS_TABLE = os.environ["RECS_TABLE"]
ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "")
DEFAULT_LIMIT = int(os.environ.get("DEFAULT_LIMIT", 10))
MAX_LIMIT = 100


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}

    user_id = str(params.get("userId", "")).strip()
    if not user_id:
        return _response(400, {"error": "userId is required"})

    try:
        limit = min(int(query.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except ValueError:
        return _response(400, {"error": "limit must be an integer"})

    item = dynamodb.Table(RECS_TABLE).get_item(Key={"userId": user_id}).get("Item")

    if item and item.get("items"):
        return _response(
            200,
            {
                "userId": user_id,
                "source": "precomputed",
                "generatedAt": item.get("generatedAt", ""),
                "recommendations": _clean(item["items"])[:limit],
            },
        )

    if ENDPOINT_NAME:
        LOG.info("cold user %s — falling back to the endpoint", user_id)
        return _response(200, _score_live(user_id, limit))

    return _response(
        404,
        {
            "userId": user_id,
            "error": "no recommendations available",
            "hint": "run the batch job, or enable the real-time endpoint for cold users",
        },
    )


def _score_live(user_id: str, limit: int) -> dict[str, Any]:
    """Score a shortlist of popular items for a user the batch job has not covered."""
    shortlist = _popular_items(limit * 10)

    response = runtime.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="application/json",
        Body=json.dumps({"pairs": [[int(user_id), item] for item in shortlist]}).encode("utf-8"),
    )
    scores = json.loads(response["Body"].read())["scores"]

    ranked = sorted(zip(shortlist, scores), key=lambda pair: pair[1], reverse=True)[:limit]

    return {
        "userId": user_id,
        "source": "endpoint",
        "recommendations": [{"itemId": str(item), "score": round(float(score), 5)} for item, score in ranked],
    }


def _popular_items(count: int) -> list[int]:
    """Placeholder shortlist. Replace with a popularity table or a cached list in S3."""
    item = dynamodb.Table(RECS_TABLE).get_item(Key={"userId": "__popular__"}).get("Item")
    if item and item.get("items"):
        return [int(entry["itemId"]) for entry in item["items"][:count]]
    return list(range(count))


def _clean(items: Any) -> list[dict[str, Any]]:
    cleaned = []
    for entry in items:
        cleaned.append(
            {
                "itemId": str(entry.get("itemId", "")),
                "score": float(entry["score"]) if isinstance(entry.get("score"), Decimal) else entry.get("score"),
            }
        )
    return cleaned


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "max-age=60"},
        "body": json.dumps(body, default=str),
    }
