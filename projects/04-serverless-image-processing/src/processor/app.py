"""Image processing Lambda.

Triggered by SQS messages carrying S3 ObjectCreated events. For each object:
validate, normalise orientation, strip EXIF, render several sizes into the
derived bucket, and record metadata in DynamoDB.

Partial batch failures are reported back to SQS so one poisoned message does not
force the whole batch to be retried.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

DERIVED_BUCKET = os.environ["DERIVED_BUCKET"]
IMAGES_TABLE = os.environ["IMAGES_TABLE"]
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 25 * 1024 * 1024))

# Longest-edge targets. Images are never upscaled.
SIZES: dict[str, int] = {"thumb": 150, "small": 480, "medium": 1024}
OUTPUT_FORMATS = ("webp", "jpeg")
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}
# Guard against decompression bombs: 100 megapixels.
MAX_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ValidationError(Exception):
    """Permanent failure — retrying will not help, so do not re-queue."""


@dataclass(frozen=True)
class S3Object:
    bucket: str
    key: str
    size: int


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            for obj in _s3_objects_from_sqs_record(record):
                _process(obj)
        except ValidationError as exc:
            # Record the rejection and acknowledge the message; a retry would fail identically.
            LOG.warning("rejected message %s: %s", message_id, exc)
        except Exception:  # noqa: BLE001 — anything else is worth retrying
            LOG.exception("failed message %s", message_id)
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}


def _s3_objects_from_sqs_record(record: dict[str, Any]) -> Iterable[S3Object]:
    body = json.loads(record["body"])

    # S3 sends a test event when the notification is first configured.
    if body.get("Event") == "s3:TestEvent":
        LOG.info("ignoring S3 test event")
        return []

    objects = []
    for s3_record in body.get("Records", []):
        bucket = s3_record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])
        size = int(s3_record["s3"]["object"].get("size", 0))
        objects.append(S3Object(bucket=bucket, key=key, size=size))
    return objects


def _process(obj: S3Object) -> None:
    image_id = _image_id(obj.key)
    LOG.info("processing s3://%s/%s (%d bytes) as %s", obj.bucket, obj.key, obj.size, image_id)

    if obj.size > MAX_IMAGE_BYTES:
        _record_failure(image_id, obj, f"file too large: {obj.size} > {MAX_IMAGE_BYTES}")
        raise ValidationError(f"{obj.key} exceeds MAX_IMAGE_BYTES")

    raw = _download(obj)
    checksum = hashlib.sha256(raw).hexdigest()

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = (probe.format or "").upper()
            width, height = probe.size
    except UnidentifiedImageError as exc:
        _record_failure(image_id, obj, "not a recognised image")
        raise ValidationError(f"{obj.key} is not an image") from exc

    if fmt not in ALLOWED_FORMATS:
        _record_failure(image_id, obj, f"unsupported format {fmt}")
        raise ValidationError(f"{obj.key} has unsupported format {fmt}")

    if width * height > MAX_PIXELS:
        _record_failure(image_id, obj, f"too many pixels: {width}x{height}")
        raise ValidationError(f"{obj.key} exceeds the pixel limit")

    derivatives = _render_derivatives(raw, image_id)

    _table().put_item(
        Item={
            "imageId": image_id,
            "status": "PROCESSED",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceBucket": obj.bucket,
            "sourceKey": obj.key,
            "sourceBytes": obj.size,
            "sha256": checksum,
            "format": fmt,
            "width": width,
            "height": height,
            "derivatives": derivatives,
        }
    )
    LOG.info("stored %d derivatives for %s", len(derivatives), image_id)


def _render_derivatives(raw: bytes, image_id: str) -> dict[str, dict[str, Any]]:
    derivatives: dict[str, dict[str, Any]] = {}

    with Image.open(io.BytesIO(raw)) as img:
        # Apply EXIF orientation, then drop EXIF entirely (GPS tags are a privacy risk).
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        for label, longest_edge in SIZES.items():
            resized = _fit(img, longest_edge)

            for fmt in OUTPUT_FORMATS:
                payload, content_type = _encode(resized, fmt)
                key = f"{image_id}/{label}.{fmt}"

                s3.put_object(
                    Bucket=DERIVED_BUCKET,
                    Key=key,
                    Body=payload,
                    ContentType=content_type,
                    CacheControl="public, max-age=31536000, immutable",
                    Metadata={"image-id": image_id, "variant": label},
                )

                derivatives[f"{label}_{fmt}"] = {
                    "key": key,
                    "bytes": len(payload),
                    "width": resized.width,
                    "height": resized.height,
                }

    return derivatives


def _fit(img: Image.Image, longest_edge: int) -> Image.Image:
    if max(img.size) <= longest_edge:
        return img.copy()  # never upscale
    copy = img.copy()
    copy.thumbnail((longest_edge, longest_edge), Image.LANCZOS)
    return copy


def _encode(img: Image.Image, fmt: str) -> tuple[bytes, str]:
    buffer = io.BytesIO()

    if fmt == "jpeg":
        flat = img.convert("RGB") if img.mode == "RGBA" else img
        flat.save(buffer, format="JPEG", quality=85, optimize=True, progressive=True)
        return buffer.getvalue(), "image/jpeg"

    img.save(buffer, format="WEBP", quality=82, method=4)
    return buffer.getvalue(), "image/webp"


def _download(obj: S3Object) -> bytes:
    try:
        response = s3.get_object(Bucket=obj.bucket, Key=obj.key)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            raise ValidationError(f"object gone: s3://{obj.bucket}/{obj.key}") from exc
        raise
    return response["Body"].read()


def _record_failure(image_id: str, obj: S3Object, reason: str) -> None:
    _table().put_item(
        Item={
            "imageId": image_id,
            "status": "REJECTED",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceBucket": obj.bucket,
            "sourceKey": obj.key,
            "sourceBytes": obj.size,
            "reason": reason,
        }
    )


def _image_id(key: str) -> str:
    """Stable, filesystem-safe id derived from the object key."""
    stem = key.split("/")[-1].rsplit(".", 1)[0]
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem)[:40].strip("-")
    digest = hashlib.sha1(key.encode()).hexdigest()[:10]
    return f"{safe or 'image'}-{digest}"


def _table():
    return dynamodb.Table(IMAGES_TABLE)
