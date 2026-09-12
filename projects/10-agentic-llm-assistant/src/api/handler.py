"""Chat API in front of the Bedrock Agent.

POST /chat                {"message": "...", "sessionId": "...", "returnTrace": false}
GET  /sessions/{sessionId}  -> the stored transcript

InvokeAgent streams an event stream; the useful parts are `chunk` (answer text),
`trace` (the model's reasoning and tool calls) and `returnControl` (a write action
awaiting confirmation).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    config=Config(read_timeout=300, retries={"max_attempts": 2, "mode": "standard"}),
)
dynamodb = boto3.resource("dynamodb")

AGENT_ID = os.environ["AGENT_ID"]
AGENT_ALIAS_ID = os.environ["AGENT_ALIAS_ID"]
CONVERSATIONS_TABLE = os.environ["CONVERSATIONS_TABLE"]
TRANSCRIPT_TTL_DAYS = int(os.environ.get("TRANSCRIPT_TTL_DAYS", 30))
MAX_MESSAGE_CHARS = 4000


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    route = event.get("routeKey", "")

    try:
        if route.startswith("GET /sessions"):
            return _history(event)
        return _chat(event)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        LOG.exception("bedrock error %s", code)
        if code in ("ThrottlingException", "ServiceQuotaExceededException"):
            return _response(429, {"error": "busy, try again in a moment"})
        if code == "ResourceNotFoundException":
            return _response(503, {"error": "agent not ready — has it been prepared?"})
        return _response(502, {"error": "agent invocation failed"})
    except Exception:  # noqa: BLE001
        LOG.exception("chat failed")
        return _response(500, {"error": "chat failed"})


def _chat(event: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(event.get("body") or "{}") if "body" in event else event

    message = (body.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"message is too long (max {MAX_MESSAGE_CHARS} characters)")

    session_id = body.get("sessionId") or uuid.uuid4().hex
    return_trace = bool(body.get("returnTrace"))

    # Session attributes ride along with every turn — the place for the signed-in
    # customer id, so the model never has to ask for something you already know.
    session_state: dict[str, Any] = {}
    if body.get("customerId"):
        session_state["sessionAttributes"] = {"customerId": str(body["customerId"])}

    LOG.info("session=%s message=%s", session_id, message[:200])

    response = agent_runtime.invoke_agent(
        agentId=AGENT_ID,
        agentAliasId=AGENT_ALIAS_ID,
        sessionId=session_id,
        inputText=message,
        enableTrace=True,
        **({"sessionState": session_state} if session_state else {}),
    )

    answer, traces, pending_action, citations = _consume(response)

    _persist(session_id, "user", message)
    _persist(session_id, "assistant", answer, {"toolCalls": _tool_calls(traces)})

    payload: dict[str, Any] = {
        "sessionId": session_id,
        "answer": answer,
        "toolCalls": _tool_calls(traces),
    }
    if citations:
        payload["citations"] = citations
    if pending_action:
        payload["pendingAction"] = pending_action
        payload["requiresConfirmation"] = True
    if return_trace:
        payload["trace"] = traces

    return _response(200, payload)


def _consume(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None, list]:
    """Drain the event stream into text, traces, and any pending confirmation."""
    chunks: list[str] = []
    traces: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    for event in response.get("completion", []):
        if "chunk" in event:
            chunk = event["chunk"]
            chunks.append(chunk["bytes"].decode("utf-8"))

            for citation in chunk.get("attribution", {}).get("citations", []):
                for reference in citation.get("retrievedReferences", []):
                    citations.append(
                        {
                            "text": reference.get("content", {}).get("text", "")[:300],
                            "location": reference.get("location", {}),
                        }
                    )

        elif "trace" in event:
            trace = event["trace"].get("trace", {})
            traces.append(trace)
            LOG.info("TRACE %s", json.dumps(trace, default=str)[:1500])

        elif "returnControl" in event:
            # A confirmation-gated tool is waiting for the user to say yes.
            pending = event["returnControl"]
            LOG.info("PENDING ACTION %s", json.dumps(pending, default=str)[:800])

        elif "internalServerException" in event:
            raise RuntimeError(str(event["internalServerException"]))

    return "".join(chunks).strip(), traces, pending, citations


def _tool_calls(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the trace into a simple list of which tools ran — useful in the UI."""
    calls = []

    for trace in traces:
        orchestration = trace.get("orchestrationTrace", {})
        invocation = orchestration.get("invocationInput", {}).get("actionGroupInvocationInput")
        if invocation:
            calls.append(
                {
                    "tool": invocation.get("function") or invocation.get("apiPath", ""),
                    "actionGroup": invocation.get("actionGroupName", ""),
                    "parameters": {
                        p.get("name"): p.get("value") for p in invocation.get("parameters", []) or []
                    },
                }
            )

    return calls


def _history(event: dict[str, Any]) -> dict[str, Any]:
    session_id = (event.get("pathParameters") or {}).get("sessionId", "")
    if not session_id:
        raise ValueError("sessionId is required")

    response = dynamodb.Table(CONVERSATIONS_TABLE).query(
        KeyConditionExpression=Key("sessionId").eq(session_id),
        ScanIndexForward=True,
        Limit=200,
    )

    return _response(
        200,
        {
            "sessionId": session_id,
            "turns": [
                {
                    "role": item.get("role"),
                    "content": item.get("content"),
                    "at": item.get("turnAt"),
                    "toolCalls": item.get("toolCalls", []),
                }
                for item in response.get("Items", [])
            ],
        },
    )


def _persist(session_id: str, role: str, content: str, extra: dict[str, Any] | None = None) -> None:
    item = {
        "sessionId": session_id,
        "turnAt": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content[:8000],
        "expiresAt": int((datetime.now(timezone.utc) + timedelta(days=TRANSCRIPT_TTL_DAYS)).timestamp()),
    }
    item.update(extra or {})
    dynamodb.Table(CONVERSATIONS_TABLE).put_item(Item=item)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
