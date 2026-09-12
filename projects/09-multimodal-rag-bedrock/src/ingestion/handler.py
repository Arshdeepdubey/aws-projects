"""Ingestion Lambda: documents in S3 -> chunks + embeddings in OpenSearch Serverless.

Handles three event shapes on the same function:

1. SQS messages carrying S3 ObjectCreated events  — a new document arrived
2. SNS messages from Textract                     — an async PDF job finished
3. Direct invocation {"bucket": ..., "key": ...}  — manual re-ingestion

PDFs go through Textract asynchronously because sync Textract only handles single
pages; images and text files are processed inline.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import bedrock  # noqa: E402
from shared.aoss import OpenSearchServerless  # noqa: E402
from shared.chunking import chunk_text  # noqa: E402

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")
textract = boto3.client("textract")
dynamodb = boto3.resource("dynamodb")

INDEX_NAME = os.environ["INDEX_NAME"]
COLLECTION_ENDPOINT = os.environ["COLLECTION_ENDPOINT"]
DERIVED_BUCKET = os.environ["DERIVED_BUCKET"]
DOCUMENTS_TABLE = os.environ["DOCUMENTS_TABLE"]
TEXTRACT_TOPIC_ARN = os.environ.get("TEXTRACT_TOPIC_ARN", "")
TEXTRACT_ROLE_ARN = os.environ.get("TEXTRACT_ROLE_ARN", "")
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 1200))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 180))

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".json", ".csv")
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}

_search: OpenSearchServerless | None = None


def search_client() -> OpenSearchServerless:
    global _search
    if _search is None:
        _search = OpenSearchServerless(COLLECTION_ENDPOINT)
    return _search


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    processed = 0

    for record in _records(event):
        message_id = record.get("messageId", "direct")
        try:
            processed += _dispatch(record["payload"])
        except Exception:  # noqa: BLE001
            LOG.exception("ingestion failed for %s", message_id)
            failures.append({"itemIdentifier": message_id})

    result: dict[str, Any] = {"processed": processed}
    if failures:
        result["batchItemFailures"] = failures
    return result


def _records(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten SQS / SNS / direct invocation into a common shape."""
    records = []

    for record in event.get("Records", []):
        source = record.get("eventSource") or record.get("EventSource")

        if source == "aws:sqs":
            body = json.loads(record["body"])
            if body.get("Event") == "s3:TestEvent":
                continue
            records.append({"messageId": record["messageId"], "payload": body})

        elif source == "aws:sns":
            body = json.loads(record["Sns"]["Message"])
            records.append({"messageId": record["Sns"]["MessageId"], "payload": {"textract": body}})

    if not records and ("bucket" in event or "Records" not in event):
        records.append({"messageId": "direct", "payload": event})

    return records


def _dispatch(payload: dict[str, Any]) -> int:
    if "textract" in payload:
        return _handle_textract_completion(payload["textract"])

    if "Records" in payload:  # S3 notification wrapped in SQS
        total = 0
        for s3_record in payload["Records"]:
            bucket = s3_record["s3"]["bucket"]["name"]
            key = urllib.parse.unquote_plus(s3_record["s3"]["object"]["key"])
            total += _ingest(bucket, key)
        return total

    if "bucket" in payload and "key" in payload:
        return _ingest(payload["bucket"], payload["key"])

    LOG.warning("unrecognised payload: %s", json.dumps(payload)[:400])
    return 0


# ------------------------------------------------------------------ ingestion
def _ingest(bucket: str, key: str) -> int:
    document_id = key.split("/", 1)[-1]
    suffix = os.path.splitext(key)[1].lower()
    LOG.info("ingesting s3://%s/%s (%s)", bucket, key, suffix or "no extension")

    _mark(document_id, "PROCESSING", {"bucket": bucket, "key": key})

    # Re-ingesting an updated file must not leave the old chunks behind.
    try:
        search_client().delete_by_document(INDEX_NAME, document_id)
    except RuntimeError as exc:
        LOG.info("no existing chunks to remove (%s)", str(exc)[:120])

    if suffix == ".pdf":
        return _start_textract(bucket, key, document_id)
    if suffix in IMAGE_SUFFIXES:
        return _ingest_image(bucket, key, document_id, suffix)
    if suffix in TEXT_SUFFIXES or suffix == "":
        return _ingest_text_file(bucket, key, document_id)

    _mark(document_id, "SKIPPED", {"reason": f"unsupported type {suffix}"})
    LOG.warning("unsupported file type: %s", key)
    return 0


def _ingest_text_file(bucket: str, key: str, document_id: str) -> int:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", "replace")
    chunks = chunk_text(body, CHUNK_SIZE, CHUNK_OVERLAP)
    documents = [
        _text_document(document_id, index, chunk, bucket, key, page=None)
        for index, chunk in enumerate(chunks)
    ]
    return _index(document_id, documents)


def _ingest_image(bucket: str, key: str, document_id: str, suffix: str) -> int:
    image_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    media_type = MEDIA_TYPES.get(suffix, "image/png")

    caption = bedrock.describe_image(image_bytes, media_type=media_type)
    LOG.info("caption for %s: %s", key, caption[:160])

    document = {
        "documentId": document_id,
        "chunkId": f"{document_id}#image",
        "modality": "image",
        "page": None,
        "s3Uri": f"s3://{bucket}/{key}",
        "sourceUri": f"s3://{bucket}/{key}",
        "title": os.path.basename(key),
        "text": caption,
        "caption": caption,
        "createdAt": _now(),
        "metadata": {"mediaType": media_type, "bytes": len(image_bytes)},
        # Both vectors: the caption makes it findable by text search, the image
        # embedding makes it findable by visual similarity and cross-modal queries.
        "textVector": bedrock.embed_text(caption),
        "multimodalVector": bedrock.embed_multimodal(text=caption[:400], image_bytes=image_bytes),
    }
    return _index(document_id, [document])


def _start_textract(bucket: str, key: str, document_id: str) -> int:
    if not TEXTRACT_TOPIC_ARN or not TEXTRACT_ROLE_ARN:
        raise RuntimeError("TEXTRACT_TOPIC_ARN and TEXTRACT_ROLE_ARN must be configured for PDFs")

    response = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        NotificationChannel={"SNSTopicArn": TEXTRACT_TOPIC_ARN, "RoleArn": TEXTRACT_ROLE_ARN},
        JobTag=document_id[:64],
        OutputConfig={"S3Bucket": DERIVED_BUCKET, "S3Prefix": f"textract/{document_id}/"},
    )

    _mark(
        document_id,
        "TEXTRACT_RUNNING",
        {"jobId": response["JobId"], "bucket": bucket, "key": key},
    )
    LOG.info("started Textract job %s for %s", response["JobId"], key)
    return 0  # indexing happens when the job completes


def _handle_textract_completion(message: dict[str, Any]) -> int:
    job_id = message["JobId"]
    status = message["Status"]
    document_id = message.get("JobTag") or job_id
    location = message.get("DocumentLocation", {})
    bucket = location.get("S3Bucket", "")
    key = location.get("S3ObjectName", "")

    if status != "SUCCEEDED":
        _mark(document_id, "FAILED", {"jobId": job_id, "status": status})
        LOG.error("Textract job %s finished with status %s", job_id, status)
        return 0

    pages = _collect_textract_pages(job_id)
    LOG.info("Textract returned %d pages for %s", len(pages), document_id)

    documents: list[dict[str, Any]] = []
    for page_number, page_text in sorted(pages.items()):
        for index, chunk in enumerate(chunk_text(page_text, CHUNK_SIZE, CHUNK_OVERLAP)):
            documents.append(
                _text_document(document_id, f"{page_number}-{index}", chunk, bucket, key, page=page_number)
            )

    return _index(document_id, documents)


def _collect_textract_pages(job_id: str) -> dict[int, str]:
    """Page number -> text, following pagination tokens."""
    pages: dict[int, list[str]] = {}
    next_token: str | None = None

    while True:
        kwargs = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            kwargs["NextToken"] = next_token

        response = textract.get_document_text_detection(**kwargs)

        for block in response.get("Blocks", []):
            if block["BlockType"] == "LINE":
                pages.setdefault(int(block.get("Page", 1)), []).append(block.get("Text", ""))

        next_token = response.get("NextToken")
        if not next_token:
            break

    return {page: "\n".join(lines) for page, lines in pages.items()}


def _text_document(
    document_id: str,
    chunk_index: Any,
    chunk: str,
    bucket: str,
    key: str,
    page: int | None,
) -> dict[str, Any]:
    return {
        "documentId": document_id,
        "chunkId": f"{document_id}#{chunk_index}",
        "modality": "text",
        "page": page,
        "s3Uri": f"s3://{bucket}/{key}" if bucket else "",
        "sourceUri": f"s3://{bucket}/{key}" if bucket else "",
        "title": os.path.basename(key) if key else document_id,
        "text": chunk,
        "caption": "",
        "createdAt": _now(),
        "metadata": {"chars": len(chunk)},
        "textVector": bedrock.embed_text(chunk),
        # Short prefix only: the multimodal model truncates text hard, and a
        # half-truncated chunk embedding is worse than none.
        "multimodalVector": bedrock.embed_multimodal(text=chunk[:400]),
    }


def _index(document_id: str, documents: list[dict[str, Any]]) -> int:
    if not documents:
        _mark(document_id, "EMPTY", {"chunks": 0})
        LOG.warning("nothing to index for %s", document_id)
        return 0

    client = search_client()
    if not client.index_exists(INDEX_NAME):
        LOG.info("creating index %s", INDEX_NAME)
        client.create_index(INDEX_NAME)

    count = client.bulk_index(INDEX_NAME, documents)
    _mark(document_id, "INDEXED", {"chunks": count})
    LOG.info("indexed %d chunks for %s", count, document_id)
    return count


def _mark(document_id: str, status: str, extra: dict[str, Any] | None = None) -> None:
    item = {"documentId": document_id, "status": status, "updatedAt": _now()}
    item.update(extra or {})
    dynamodb.Table(DOCUMENTS_TABLE).put_item(Item=item)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
