"""Unit tests for the image processor — no AWS account required (moto fakes S3/DynamoDB)."""

import importlib
import io
import json
import os

import boto3
import pytest
from moto import mock_aws
from PIL import Image

REGION = "ap-south-1"
UPLOADS = "test-uploads"
DERIVED = "test-derived"
TABLE = "test-images"


@pytest.fixture
def processor(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DERIVED_BUCKET", DERIVED)
    monkeypatch.setenv("IMAGES_TABLE", TABLE)
    monkeypatch.setenv("MAX_IMAGE_BYTES", str(5 * 1024 * 1024))

    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        for bucket in (UPLOADS, DERIVED):
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )

        boto3.client("dynamodb", region_name=REGION).create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "imageId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "imageId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "processor"))
        module = importlib.import_module("app")
        importlib.reload(module)
        yield module


def _event(key: str, size: int) -> dict:
    body = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": UPLOADS},
                    "object": {"key": key, "size": size},
                }
            }
        ]
    }
    return {"Records": [{"messageId": "m1", "body": json.dumps(body)}]}


def _upload_png(width: int, height: int, key: str) -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 200)).save(buffer, format="PNG")
    payload = buffer.getvalue()
    boto3.client("s3", region_name=REGION).put_object(Bucket=UPLOADS, Key=key, Body=payload)
    return len(payload)


def test_creates_all_derivatives(processor):
    size = _upload_png(1600, 900, "incoming/photo.png")

    result = processor.lambda_handler(_event("incoming/photo.png", size), None)

    assert result == {"batchItemFailures": []}

    keys = {
        obj["Key"]
        for obj in boto3.client("s3", region_name=REGION)
        .list_objects_v2(Bucket=DERIVED)
        .get("Contents", [])
    }
    # 3 sizes x 2 formats
    assert len(keys) == 6
    assert any(k.endswith("thumb.webp") for k in keys)

    items = boto3.resource("dynamodb", region_name=REGION).Table(TABLE).scan()["Items"]
    assert len(items) == 1
    assert items[0]["status"] == "PROCESSED"
    assert int(items[0]["width"]) == 1600


def test_never_upscales_small_images(processor):
    size = _upload_png(80, 60, "incoming/tiny.png")
    processor.lambda_handler(_event("incoming/tiny.png", size), None)

    item = boto3.resource("dynamodb", region_name=REGION).Table(TABLE).scan()["Items"][0]
    medium = item["derivatives"]["medium_webp"]
    assert int(medium["width"]) == 80


def test_rejects_oversized_upload_without_retry(processor):
    result = processor.lambda_handler(_event("incoming/huge.png", 99 * 1024 * 1024), None)

    # Permanent rejection: acknowledged, not re-queued.
    assert result == {"batchItemFailures": []}
    item = boto3.resource("dynamodb", region_name=REGION).Table(TABLE).scan()["Items"][0]
    assert item["status"] == "REJECTED"


def test_ignores_s3_test_event(processor):
    event = {"Records": [{"messageId": "m1", "body": json.dumps({"Event": "s3:TestEvent"})}]}
    assert processor.lambda_handler(event, None) == {"batchItemFailures": []}


def test_image_id_is_stable_and_safe(processor):
    first = processor._image_id("incoming/My Photo (1).jpg")
    second = processor._image_id("incoming/My Photo (1).jpg")
    assert first == second
    assert all(c.isalnum() or c in "-_" for c in first)
