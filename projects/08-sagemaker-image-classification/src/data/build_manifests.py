"""Build SageMaker augmented manifest files from a folder-per-class layout in S3.

    python build_manifests.py --bucket my-bucket --prefix raw --out-prefix manifests

Reads  s3://bucket/raw/<class>/<image>
Writes s3://bucket/manifests/train.manifest, validation.manifest, class_index.json

Augmented manifests let the built-in image classification algorithm read plain JPEG/PNG
files straight from S3 — no RecordIO conversion step, and the split stays inspectable.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from collections import Counter, defaultdict

import boto3

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="raw", help="Prefix containing one folder per class.")
    parser.add_argument("--out-prefix", default="manifests")
    parser.add_argument("--validation-split", type=float, default=0.2)
    parser.add_argument("--min-per-class", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    s3 = boto3.client("s3")
    prefix = args.prefix.strip("/") + "/"

    by_class: dict[str, list[str]] = defaultdict(list)
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=args.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(IMAGE_SUFFIXES):
                continue
            parts = key[len(prefix):].split("/")
            if len(parts) < 2:
                LOG.debug("skipping %s — not inside a class folder", key)
                continue
            by_class[parts[0]].append(key)

    if not by_class:
        raise SystemExit(f"no images found under s3://{args.bucket}/{prefix}<class>/")

    thin = [c for c, keys in by_class.items() if len(keys) < args.min_per_class]
    for class_name in thin:
        LOG.warning("dropping class %r — only %d images", class_name, len(by_class[class_name]))
        by_class.pop(class_name)

    classes = sorted(by_class)
    class_index = {name: i for i, name in enumerate(classes)}
    LOG.info("classes: %s", json.dumps({c: len(by_class[c]) for c in classes}))

    random.seed(args.seed)
    train_lines: list[str] = []
    val_lines: list[str] = []

    # Stratified split: every class keeps its proportion in both sets.
    for class_name in classes:
        keys = sorted(by_class[class_name])
        random.shuffle(keys)
        split_at = max(1, int(len(keys) * (1 - args.validation_split)))

        for index, key in enumerate(keys):
            line = json.dumps(
                {
                    "source-ref": f"s3://{args.bucket}/{key}",
                    "class": class_index[class_name],
                    "class-metadata": {"class-name": class_name},
                }
            )
            (train_lines if index < split_at else val_lines).append(line)

    random.shuffle(train_lines)

    out_prefix = args.out_prefix.strip("/") + "/"
    _put(s3, args.bucket, f"{out_prefix}train.manifest", "\n".join(train_lines) + "\n")
    _put(s3, args.bucket, f"{out_prefix}validation.manifest", "\n".join(val_lines) + "\n")
    _put(
        s3,
        args.bucket,
        f"{out_prefix}class_index.json",
        json.dumps({"classes": classes, "index": class_index}, indent=2),
    )

    LOG.info("train=%d validation=%d classes=%d", len(train_lines), len(val_lines), len(classes))
    LOG.info("num_classes=%d num_training_samples=%d  <- pass these to the estimator",
             len(classes), len(train_lines))


def _put(s3, bucket: str, key: str, body: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"))
    LOG.info("wrote s3://%s/%s", bucket, key)


if __name__ == "__main__":
    main()
