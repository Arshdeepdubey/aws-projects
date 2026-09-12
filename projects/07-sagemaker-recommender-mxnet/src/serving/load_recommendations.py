"""Write batch-transform output into the DynamoDB recommendations table.

    python load_recommendations.py --bucket my-bucket --table recommender-dev-recs

Expects one JSON object per line in s3://<bucket>/batch-output/:
    {"userId": "42", "items": [{"itemId": "356", "score": 0.93}, ...]}
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="batch-output/")
    parser.add_argument("--table", required=True)
    parser.add_argument("--ttl-days", type=int, default=14)
    parser.add_argument("--top-n", type=int, default=50)
    args = parser.parse_args()

    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(args.table)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=args.ttl_days)).timestamp())

    written = 0
    paginator = s3.get_paginator("list_objects_v2")

    with table.batch_writer(overwrite_by_pkeys=["userId"]) as batch:
        for page in paginator.paginate(Bucket=args.bucket, Prefix=args.prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith("/"):
                    continue

                body = s3.get_object(Bucket=args.bucket, Key=obj["Key"])["Body"].read().decode("utf-8")

                for line in body.splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)

                    batch.put_item(
                        Item={
                            "userId": str(record["userId"]),
                            "generatedAt": generated_at,
                            "expiresAt": expires_at,
                            "items": [
                                {
                                    "itemId": str(entry["itemId"]),
                                    # DynamoDB has no float type.
                                    "score": Decimal(str(round(float(entry["score"]), 6))),
                                }
                                for entry in record["items"][: args.top_n]
                            ],
                        }
                    )
                    written += 1

                    if written % 10_000 == 0:
                        LOG.info("written %d users", written)

    LOG.info("done: %d user rows written to %s", written, args.table)


if __name__ == "__main__":
    main()
