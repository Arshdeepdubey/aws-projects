"""Query Lambda: hybrid retrieval + grounded generation.

POST /ask {"question": "...", "topK": 6, "filters": {"documentId": "report.pdf"}}

Retrieval is three searches fused with reciprocal rank fusion:
  1. k-NN over text embeddings      — semantic match on prose
  2. k-NN over multimodal embeddings — finds figures and screenshots
  3. BM25 keyword search             — exact identifiers vector search misses

RRF needs no score normalisation across engines, which is exactly the problem
when mixing an L2 distance with a BM25 score.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import bedrock  # noqa: E402
from shared.aoss import OpenSearchServerless  # noqa: E402

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

s3 = boto3.client("s3")

INDEX_NAME = os.environ["INDEX_NAME"]
COLLECTION_ENDPOINT = os.environ["COLLECTION_ENDPOINT"]
DEFAULT_TOP_K = int(os.environ.get("DEFAULT_TOP_K", 6))
MAX_TOP_K = 20
MAX_CONTEXT_CHARS = int(os.environ.get("MAX_CONTEXT_CHARS", 24000))
MAX_IMAGES_IN_PROMPT = 3
RRF_K = 60  # standard reciprocal rank fusion constant

SYSTEM_PROMPT = """You answer questions using only the numbered sources provided.

Rules:
- Cite the sources you used as [1], [2] inline, immediately after the claim they support.
- If the sources do not contain the answer, say so plainly and name what is missing. Never fill
  the gap from general knowledge.
- When a source is an image, describe what the image actually shows rather than guessing at intent.
- Prefer precise numbers and names from the sources over paraphrase.
- Keep the answer as short as the question allows."""

_search: OpenSearchServerless | None = None


def search_client() -> OpenSearchServerless:
    global _search
    if _search is None:
        _search = OpenSearchServerless(COLLECTION_ENDPOINT)
    return _search


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}") if "body" in event else event
        question = (body.get("question") or "").strip()

        if not question:
            return _response(400, {"error": "question is required"})
        if len(question) > 2000:
            return _response(400, {"error": "question is too long (max 2000 characters)"})

        top_k = min(int(body.get("topK", DEFAULT_TOP_K)), MAX_TOP_K)
        filters = body.get("filters") or {}

        hits = retrieve(question, top_k, filters)

        if not hits:
            return _response(
                200,
                {
                    "answer": "I couldn't find anything relevant in the indexed documents.",
                    "citations": [],
                    "usage": {"inputTokens": 0, "outputTokens": 0},
                },
            )

        answer = generate_answer(question, hits)
        return _response(200, answer)

    except Exception:  # noqa: BLE001
        LOG.exception("query failed")
        return _response(500, {"error": "query failed"})


# ------------------------------------------------------------------ retrieval
def retrieve(question: str, top_k: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
    client = search_client()
    fetch = max(top_k * 3, 15)
    filter_clause = _filter_clause(filters)

    text_vector = bedrock.embed_text(question)
    multimodal_vector = bedrock.embed_multimodal(text=question)

    rankings = [
        client.knn_search(INDEX_NAME, "textVector", text_vector, k=fetch, filters=filter_clause),
        client.knn_search(INDEX_NAME, "multimodalVector", multimodal_vector, k=fetch, filters=filter_clause),
        client.keyword_search(INDEX_NAME, question, k=fetch),
    ]

    fused = reciprocal_rank_fusion(rankings)
    LOG.info("retrieved %d unique chunks, returning top %d", len(fused), top_k)
    return fused[:top_k]


def reciprocal_rank_fusion(rankings: list[list[dict[str, Any]]], k: int = RRF_K) -> list[dict[str, Any]]:
    """Fuse ranked lists by 1/(k + rank). No score normalisation needed."""
    scores: dict[str, float] = {}
    documents: dict[str, dict[str, Any]] = {}

    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            source = hit.get("_source", {})
            key = source.get("chunkId") or hit.get("_id", "")
            if not key:
                continue

            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in documents:
                documents[key] = {**source, "_rawScore": hit.get("_score", 0.0)}

    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return [{**documents[key], "score": round(score, 6)} for key, score in ordered]


def _filter_clause(filters: dict[str, Any]) -> dict[str, Any] | None:
    terms = [{"term": {field: value}} for field, value in filters.items() if value not in (None, "")]
    return {"bool": {"filter": terms}} if terms else None


# ----------------------------------------------------------------- generation
def generate_answer(question: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    budget = MAX_CONTEXT_CHARS
    images_used = 0

    for position, hit in enumerate(hits, start=1):
        label = f"[{position}]"
        header = _source_header(label, hit)

        if hit.get("modality") == "image" and images_used < MAX_IMAGES_IN_PROMPT:
            image = _load_image(hit.get("s3Uri", ""))
            if image:
                content.append({"type": "text", "text": f"{header}\nCaption: {hit.get('caption', '')}"})
                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": image[1], "data": image[0]},
                    }
                )
                images_used += 1
                citations.append(_citation(position, hit))
                continue

        text = (hit.get("text") or "")[:budget]
        if not text:
            continue

        content.append({"type": "text", "text": f"{header}\n{text}"})
        budget -= len(text)
        citations.append(_citation(position, hit))

        if budget <= 0:
            LOG.info("context budget exhausted after %d sources", position)
            break

    content.append({"type": "text", "text": f"\nQuestion: {question}"})

    result = bedrock.generate(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        max_tokens=1500,
        temperature=0.1,
    )

    cited = _cited_positions(result["text"])
    return {
        "answer": result["text"],
        "citations": [c for c in citations if not cited or c["position"] in cited],
        "retrieved": len(hits),
        "usage": {"inputTokens": result["inputTokens"], "outputTokens": result["outputTokens"]},
    }


def _source_header(label: str, hit: dict[str, Any]) -> str:
    parts = [f"Source {label}", hit.get("title") or hit.get("documentId", "")]
    if hit.get("page"):
        parts.append(f"page {hit['page']}")
    if hit.get("modality") == "image":
        parts.append("(image)")
    return " — ".join(p for p in parts if p)


def _citation(position: int, hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": position,
        "documentId": hit.get("documentId", ""),
        "chunkId": hit.get("chunkId", ""),
        "page": hit.get("page"),
        "modality": hit.get("modality", "text"),
        "s3Uri": hit.get("s3Uri", ""),
        "score": hit.get("score", 0.0),
        "preview": (hit.get("text") or "")[:200],
    }


def _cited_positions(answer: str) -> set[int]:
    positions = set()
    for token in answer.split("["):
        head = token.split("]")[0]
        if head.isdigit():
            positions.add(int(head))
    return positions


def _load_image(s3_uri: str) -> tuple[str, str] | None:
    import base64

    if not s3_uri.startswith("s3://"):
        return None

    bucket, _, key = s3_uri[5:].partition("/")
    try:
        payload = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception:  # noqa: BLE001
        LOG.warning("could not load image %s", s3_uri)
        return None

    if len(payload) > 4 * 1024 * 1024:  # Bedrock image limit
        LOG.warning("image %s too large for the prompt (%d bytes)", s3_uri, len(payload))
        return None

    suffix = os.path.splitext(key)[1].lower()
    media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                  ".webp": "image/webp"}.get(suffix, "image/png")

    return base64.b64encode(payload).decode("utf-8"), media_type


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
