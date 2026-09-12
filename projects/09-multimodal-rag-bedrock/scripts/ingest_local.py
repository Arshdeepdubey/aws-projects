#!/usr/bin/env python3
"""Upload local files into the documents bucket so the pipeline picks them up.

    python ingest_local.py --bucket my-docs-bucket --path ./corpus
"""

import argparse
import mimetypes
from pathlib import Path

import boto3

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".md", ".markdown", ".csv", ".json"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--path", required=True, help="File or directory to upload.")
    parser.add_argument("--prefix", default="documents/")
    args = parser.parse_args()

    s3 = boto3.client("s3")
    root = Path(args.path)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())

    uploaded = 0
    for path in files:
        if path.suffix.lower() not in SUPPORTED:
            print(f"skip  {path} (unsupported type)")
            continue

        key = f"{args.prefix}{path.name}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        s3.upload_file(str(path), args.bucket, key, ExtraArgs={"ContentType": content_type})
        print(f"up    s3://{args.bucket}/{key}")
        uploaded += 1

    print(f"\nuploaded {uploaded} files; ingestion runs asynchronously")
    print("watch it: aws logs tail /aws/lambda/multimodal-rag-dev-ingestion --follow")


if __name__ == "__main__":
    main()
