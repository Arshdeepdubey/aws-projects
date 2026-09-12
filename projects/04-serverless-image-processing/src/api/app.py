"""HTTP API for the image pipeline.

POST /uploads          -> { uploadUrl, imageKey, expiresIn }  presigned PUT into incoming/
GET  /images/{imageId} -> metadata plus presigned GET URLs for each derivative
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

UPLOADS_BUCKET = os.environ["UPLOADS_BUCKET"]
DERIVED_BUCKET = os.environ["DERIVED_BUCKET"]
IMAGES_TABLE = os.environ["IMAGES_TABLE"]
PRESIGN_EXPIRY = int(os.environ.get("PRESIGN_EXPIRY_SECONDS", 900))
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 25 * 1024 * 1024))

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/tiff": "tiff",
}
IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    route = event.get("routeKey") or f"{event.get('httpMethod')} {event.get('path')}"
    LOG.info("route=%s", route)

    try:
        if route.startswith("POST /uploads"):
            return _create_upload(event)
        if route.startswith("GET /images/"):
            return _get_image(event)
        return _response(404, {"error": "not found"})
    except BadRequest as exc:
        return _response(400, {"error": str(exc)})
    except Exception:  # noqa: BLE001
        LOG.exception("unhandled error")
        return _response(500, {"error": "internal error"})


class BadRequest(Exception):
    pass


def _create_upload(event: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(event.get("body") or "{}")
    content_type = body.get("contentType", "image/jpeg")
    filename = body.get("filename", "")

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise BadRequest(f"contentType must be one of {sorted(ALLOWED_CONTENT_TYPES)}")

    extension = ALLOWED_CONTENT_TYPES[content_type]
    stem = _safe_stem(filename) or uuid.uuid4().hex[:12]
    key = f"incoming/{uuid.uuid4().hex[:8]}-{stem}.{extension}"

    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": UPLOADS_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGN_EXPIRY,
    )

    return _response(
        201,
        {
            "uploadUrl": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "imageKey": key,
            "maxBytes": MAX_IMAGE_BYTES,
            "expiresIn": PRESIGN_EXPIRY,
        },
    )


def _get_image(event: dict[str, Any]) -> dict[str, Any]:
    image_id = (event.get("pathParameters") or {}).get("imageId", "")
    if not IMAGE_ID_RE.match(image_id):
        raise BadRequest("invalid imageId")

    item = dynamodb.Table(IMAGES_TABLE).get_item(Key={"imageId": image_id}).get("Item")
    if not item:
        return _response(404, {"error": "image not found"})

    for variant in (item.get("derivatives") or {}).values():
        try:
            variant["url"] = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": DERIVED_BUCKET, "Key": variant["key"]},
                ExpiresIn=PRESIGN_EXPIRY,
            )
        except ClientError:
            LOG.warning("could not presign %s", variant.get("key"))

    return _response(200, item)


def _safe_stem(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in stem)[:40].strip("-")


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"not JSON serialisable: {type(value)}")
