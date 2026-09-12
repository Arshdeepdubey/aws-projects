"""Fraud scoring API handler.

Calls the SageMaker endpoint, applies the decision threshold from SSM (so retraining
can move the threshold without a code deploy), and returns a decision plus the raw
probability — callers usually want both.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

runtime = boto3.client("sagemaker-runtime")
ssm = boto3.client("ssm")

ENDPOINT_NAME = os.environ["ENDPOINT_NAME"]
THRESHOLD_PARAMETER = os.environ["THRESHOLD_PARAMETER"]
THRESHOLD_TTL_SECONDS = 300

_threshold_cache: dict[str, Any] = {"value": None, "fetched_at": 0.0}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}") if "body" in event else event
        features = body.get("features")

        if not isinstance(features, list) or not features:
            return _response(400, {"error": "body must contain a non-empty 'features' array"})

        if not all(isinstance(v, (int, float)) for v in features):
            return _response(400, {"error": "all features must be numeric"})

        payload = ",".join(str(float(v)) for v in features)

        response = runtime.invoke_endpoint(
            EndpointName=ENDPOINT_NAME,
            ContentType="text/csv",
            Accept="application/json",
            Body=payload.encode("utf-8"),
        )

        probability = _parse_probability(response["Body"].read())
        threshold = _threshold()

        return _response(
            200,
            {
                "fraudProbability": round(probability, 6),
                "isFraud": probability >= threshold,
                "threshold": threshold,
                "endpoint": ENDPOINT_NAME,
            },
        )

    except runtime.exceptions.ValidationError as exc:
        LOG.warning("endpoint rejected the payload: %s", exc)
        return _response(400, {"error": "endpoint rejected the payload; check the feature count"})
    except Exception:  # noqa: BLE001
        LOG.exception("scoring failed")
        return _response(500, {"error": "scoring failed"})


def _parse_probability(raw: bytes) -> float:
    text = raw.decode("utf-8").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return float(text.split(",")[0])

    if isinstance(parsed, (int, float)):
        return float(parsed)
    if isinstance(parsed, list):
        return float(parsed[0] if not isinstance(parsed[0], list) else parsed[0][0])
    if isinstance(parsed, dict):
        for key in ("score", "probability", "predictions"):
            if key in parsed:
                value = parsed[key]
                return float(value[0] if isinstance(value, list) else value)
    raise ValueError(f"unrecognised endpoint response: {text[:200]}")


def _threshold() -> float:
    now = time.time()
    if _threshold_cache["value"] is None or now - _threshold_cache["fetched_at"] > THRESHOLD_TTL_SECONDS:
        value = ssm.get_parameter(Name=THRESHOLD_PARAMETER)["Parameter"]["Value"]
        _threshold_cache["value"] = float(value)
        _threshold_cache["fetched_at"] = now
    return float(_threshold_cache["value"])


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
