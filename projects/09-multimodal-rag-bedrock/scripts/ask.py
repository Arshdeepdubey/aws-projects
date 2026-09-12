#!/usr/bin/env python3
"""Ask the RAG API a question from the command line.

    python ask.py --api https://abc.execute-api.us-east-1.amazonaws.com --question "..."
"""

import argparse
import json
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--document-id", help="Restrict retrieval to one document.")
    args = parser.parse_args()

    payload = {"question": args.question, "topK": args.top_k}
    if args.document_id:
        payload["filters"] = {"documentId": args.document_id}

    request = urllib.request.Request(
        f"{args.api.rstrip('/')}/ask",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read())

    print(result.get("answer", ""))
    print("\nSources:")
    for citation in result.get("citations", []):
        page = f" p{citation['page']}" if citation.get("page") else ""
        print(f"  [{citation['position']}] {citation['documentId']}{page} "
              f"({citation['modality']}, score {citation['score']:.4f})")

    usage = result.get("usage", {})
    if usage:
        print(f"\ntokens: {usage.get('inputTokens', 0)} in / {usage.get('outputTokens', 0)} out")


if __name__ == "__main__":
    main()
