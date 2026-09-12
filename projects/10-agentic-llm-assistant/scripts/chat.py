#!/usr/bin/env python3
"""Terminal client for the agent.

    python chat.py --api https://abc.execute-api.us-east-1.amazonaws.com
    python chat.py --api ... --customer-id CUST-1042 --trace
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
import uuid


def ask(api: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{api.rstrip('/')}/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {"answer": f"[{exc.code}] {exc.read().decode('utf-8', 'replace')[:400]}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--session-id", default=uuid.uuid4().hex)
    parser.add_argument("--customer-id")
    parser.add_argument("--trace", action="store_true", help="Print the agent's reasoning trace.")
    args = parser.parse_args()

    print(f"session {args.session_id} — Ctrl-D to quit\n")

    while True:
        try:
            message = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not message:
            continue

        payload = {"message": message, "sessionId": args.session_id, "returnTrace": args.trace}
        if args.customer_id:
            payload["customerId"] = args.customer_id

        result = ask(args.api, payload)

        for call in result.get("toolCalls", []):
            print(f"  · {call['tool']}({json.dumps(call.get('parameters', {}))})")

        if result.get("requiresConfirmation"):
            print("  · waiting for your confirmation before acting")

        print(f"bot > {result.get('answer', '')}\n")

        if args.trace and result.get("trace"):
            print(json.dumps(result["trace"], indent=2, default=str)[:4000], "\n")


if __name__ == "__main__":
    main()
