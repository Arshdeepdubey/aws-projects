#!/usr/bin/env python3
"""Create the OpenSearch Serverless vector index.

    python create_index.py --endpoint https://xxxx.us-east-1.aoss.amazonaws.com --index multimodal-rag

The ingestion Lambda creates the index on first use too; this exists so you can
create it up front, inspect the mapping, or recreate it after a schema change.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shared.aoss import OpenSearchServerless  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--index", default="multimodal-rag")
    parser.add_argument("--region")
    parser.add_argument("--recreate", action="store_true", help="Delete the index first if it exists.")
    args = parser.parse_args()

    client = OpenSearchServerless(args.endpoint, region=args.region)

    if client.index_exists(args.index):
        if not args.recreate:
            print(f"index {args.index} already exists ({client.count(args.index)} documents)")
            return
        print(f"deleting index {args.index}")
        client.request("DELETE", f"/{args.index}")

    response = client.create_index(args.index)
    print(json.dumps(response, indent=2))
    print(f"created index {args.index}")


if __name__ == "__main__":
    main()
