"""Classify images as they land in S3.

Triggered on s3:ObjectCreated under incoming/. Invokes the endpoint, writes the
prediction to DynamoDB, and copies anything below the confidence threshold into a
review/ prefix — a human-in-the-loop queue costs nothing and is the difference
between a demo and something you can act on.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
runtime = boto3.client("sagemaker-runtime")
dynamodb = boto3.resource("dynamodb")

ENDPOINT_NAME = os.environ["ENDPOINT_NAME"]
PREDICTIONS_TABLE = os.environ["PREDICTIONS_TABLE"]
IMAGES_BUCKET = os.environ["IMAGES_BUCKET"]
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", 0.75))
REVIEW_PREFIX = os.environ.get("REVIEW_PREFIX", "review/")
CLASS_INDEX_KEY = os.environ.get("CLASS_INDEX_KEY", "manifests/class_index.json")
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 10 * 1024 * 1024))

_classes: list[str] | None = None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    results = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        size = int(record["s3"]["object"].get("size", 0))

        if size > MAX_IMAGE_BYTES:
            LOG.warning("skipping %s: %d bytes exceeds the limit", key, size)
            continue

        try:
            results.append(_classify(bucket, key))
        except Exception:  # noqa: BLE001
            LOG.exception("failed to classify s3://%s/%s", bucket, key)
            raise  # let Lambda retry; S3 events are async with built-in retries

    return {"classified": len(results), "results": results}


def _classify(bucket: str, key: str) -> dict[str, Any]:
    payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    response = runtime.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="application/x-image",
        Body=payload,
    )
    probabilities = json.loads(response["Body"].read())
    if isinstance(probabilities, dict):
        probabilities = probabilities.get("predictions", [probabilities])[0]

    classes = _class_names(len(probabilities))
    ranked = sorted(zip(classes, probabilities), key=lambda pair: pair[1], reverse=True)
    label, confidence = ranked[0][0], float(ranked[0][1])
    needs_review = confidence < CONFIDENCE_THRESHOLD

    item = {
        "imageKey": key,
        "bucket": bucket,
        "label": label,
        "confidence": Decimal(str(round(confidence, 6))),
        "classifiedAt": datetime.now(timezone.utc).isoformat(),
        "needsReview": needs_review,
        "endpoint": ENDPOINT_NAME,
        "topK": [
            {"label": name, "confidence": Decimal(str(round(float(score), 6)))}
            for name, score in ranked[:3]
        ],
    }
    dynamodb.Table(PREDICTIONS_TABLE).put_item(Item=item)

    if needs_review:
        review_key = f"{REVIEW_PREFIX}{key.split('/')[-1]}"
        s3.copy_object(
            Bucket=IMAGES_BUCKET,
            Key=review_key,
            CopySource={"Bucket": bucket, "Key": key},
            MetadataDirective="REPLACE",
            Metadata={"predicted-label": label, "confidence": f"{confidence:.4f}"},
        )
        LOG.info("low confidence (%.3f) — copied to %s", confidence, review_key)

    LOG.info("%s -> %s (%.3f)", key, label, confidence)
    return {"key": key, "label": label, "confidence": round(confidence, 4), "needsReview": needs_review}


def _class_names(count: int) -> list[str]:
    """Load the class index written by build_manifests.py; cached across invocations."""
    global _classes

    if _classes is None:
        try:
            body = s3.get_object(Bucket=IMAGES_BUCKET, Key=CLASS_INDEX_KEY)["Body"].read()
            _classes = json.loads(body)["classes"]
        except Exception:  # noqa: BLE001
            LOG.warning("no class index at %s; falling back to numeric labels", CLASS_INDEX_KEY)
            _classes = [str(i) for i in range(count)]

    if len(_classes) < count:
        return _classes + [str(i) for i in range(len(_classes), count)]
    return _classes
