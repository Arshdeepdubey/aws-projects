"""Bedrock helpers: embeddings (text and multimodal) and generation, with backoff.

Throttling is the normal failure mode on Bedrock, not an exception — every call
here retries with exponential backoff and jitter.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
TEXT_EMBEDDING_MODEL = os.environ.get("TEXT_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
MULTIMODAL_EMBEDDING_MODEL = os.environ.get("MULTIMODAL_EMBEDDING_MODEL", "amazon.titan-embed-image-v1")
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
VISION_MODEL = os.environ.get("VISION_MODEL", GENERATION_MODEL)

EMBEDDING_DIMENSIONS = 1024
THROTTLE_CODES = {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
                  "ModelTimeoutException", "InternalServerException"}

_client = boto3.client(
    "bedrock-runtime",
    region_name=BEDROCK_REGION,
    config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=120),
)


def _invoke(model_id: str, body: dict[str, Any], attempts: int = 5) -> dict[str, Any]:
    for attempt in range(attempts):
        try:
            response = _client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body),
            )
            return json.loads(response["body"].read())
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "AccessDeniedException":
                raise RuntimeError(
                    f"No access to {model_id}. Enable it in the Bedrock console under Model access "
                    f"for region {BEDROCK_REGION}."
                ) from exc
            if code in THROTTLE_CODES and attempt < attempts - 1:
                delay = (2**attempt) + random.random()
                LOG.warning("%s on %s, retry %d in %.1fs", code, model_id, attempt + 1, delay)
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"{model_id} exhausted retries")


# ------------------------------------------------------------------ embedding
def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Titan Text Embeddings v2 — for prose chunks."""
    payload = {"inputText": text[:8000], "dimensions": dimensions, "normalize": True}
    response = _invoke(TEXT_EMBEDDING_MODEL, payload)
    return response["embedding"]


def embed_multimodal(text: str | None = None, image_bytes: bytes | None = None) -> list[float]:
    """Titan Multimodal Embeddings — text and images in one shared space.

    The text side has a hard ~128-token limit, so pass a caption or a short query,
    never a full chunk.
    """
    if not text and not image_bytes:
        raise ValueError("embed_multimodal needs text, image bytes, or both")

    payload: dict[str, Any] = {"embeddingConfig": {"outputEmbeddingLength": EMBEDDING_DIMENSIONS}}
    if text:
        payload["inputText"] = text[:500]
    if image_bytes:
        payload["inputImage"] = base64.b64encode(image_bytes).decode("utf-8")

    response = _invoke(MULTIMODAL_EMBEDDING_MODEL, payload)
    return response["embedding"]


# ---------------------------------------------------------------- generation
def generate(
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1500,
    temperature: float = 0.2,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Anthropic Messages API on Bedrock. Returns text plus token usage."""
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": messages,
    }
    response = _invoke(model_id or GENERATION_MODEL, payload)

    text = "".join(block.get("text", "") for block in response.get("content", []))
    usage = response.get("usage", {})

    return {
        "text": text,
        "stopReason": response.get("stop_reason"),
        "inputTokens": usage.get("input_tokens", 0),
        "outputTokens": usage.get("output_tokens", 0),
    }


def describe_image(image_bytes: bytes, media_type: str = "image/png", context: str = "") -> str:
    """Dense caption for an image, so text retrieval can find it and the LLM can use it."""
    instruction = (
        "Describe this image for a search index. Include: what kind of figure it is "
        "(chart, diagram, screenshot, photo, table), every label and axis title, the "
        "relationships or flow it depicts, and any numbers that carry meaning. "
        "Be specific and factual. Do not speculate about what is not shown."
    )
    if context:
        instruction += f"\n\nSurrounding document context: {context[:800]}"

    result = generate(
        system="You write precise, information-dense descriptions of document figures.",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_bytes).decode("utf-8"),
                        },
                    },
                    {"type": "text", "text": instruction},
                ],
            }
        ],
        max_tokens=600,
        temperature=0.0,
        model_id=VISION_MODEL,
    )
    return result["text"].strip()
