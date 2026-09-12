"""Minimal OpenSearch Serverless client signed with SigV4.

Uses only botocore, which is already present in the Lambda runtime — no
opensearch-py, no Docker bundling step, ~100 lines. It covers what a RAG system
actually needs: create index, bulk index, search, delete by query.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

LOG = logging.getLogger(__name__)

SERVICE = "aoss"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenSearchServerless:
    def __init__(self, endpoint: str, region: str | None = None, timeout: int = 30):
        if not endpoint:
            raise ValueError("collection endpoint is required")
        self.endpoint = endpoint.rstrip("/")
        session = boto3.Session()
        self.region = region or session.region_name
        self.credentials = session.get_credentials()
        self.timeout = timeout

    # ------------------------------------------------------------- low level
    def request(self, method: str, path: str, body: Any = None, retries: int = 4) -> dict[str, Any]:
        url = f"{self.endpoint}{path}"
        payload = None
        headers = {"Content-Type": "application/json"}

        if body is not None:
            if isinstance(body, str):  # already-serialised NDJSON for _bulk
                payload = body.encode("utf-8")
                headers["Content-Type"] = "application/x-ndjson"
            else:
                payload = json.dumps(body).encode("utf-8")

        last_error: Exception | None = None

        for attempt in range(retries):
            signed = AWSRequest(method=method, url=url, data=payload, headers=dict(headers))
            SigV4Auth(self.credentials, SERVICE, self.region).add_auth(signed)

            request = urllib.request.Request(
                url, data=payload, headers=dict(signed.headers), method=method
            )

            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:800]
                if exc.code in RETRYABLE_STATUS and attempt < retries - 1:
                    sleep_for = 2**attempt * 0.5
                    LOG.warning("AOSS %s %s -> %d, retrying in %.1fs", method, path, exc.code, sleep_for)
                    time.sleep(sleep_for)
                    last_error = exc
                    continue
                raise RuntimeError(f"AOSS {method} {path} failed ({exc.code}): {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < retries - 1:
                    time.sleep(2**attempt * 0.5)
                    last_error = exc
                    continue
                raise RuntimeError(f"AOSS {method} {path} failed: {exc}") from exc

        raise RuntimeError(f"AOSS {method} {path} exhausted retries: {last_error}")

    # ------------------------------------------------------------ index mgmt
    def index_exists(self, index: str) -> bool:
        try:
            self.request("GET", f"/{index}/_mapping")
            return True
        except RuntimeError as exc:
            if "404" in str(exc) or "index_not_found" in str(exc):
                return False
            raise

    def create_index(self, index: str, text_dims: int = 1024, multimodal_dims: int = 1024) -> dict[str, Any]:
        """Two vector fields: long-text embeddings and multimodal embeddings.

        Keeping them separate matters — a text-only vector and a multimodal vector
        are not comparable even when they have the same dimensionality.
        """
        mapping = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 512,
                }
            },
            "mappings": {
                "properties": {
                    "documentId": {"type": "keyword"},
                    "chunkId": {"type": "keyword"},
                    "modality": {"type": "keyword"},
                    "page": {"type": "integer"},
                    "s3Uri": {"type": "keyword"},
                    "sourceUri": {"type": "keyword"},
                    "title": {"type": "text"},
                    "text": {"type": "text", "analyzer": "standard"},
                    "caption": {"type": "text"},
                    "createdAt": {"type": "date"},
                    "metadata": {"type": "object", "enabled": True},
                    "textVector": {
                        "type": "knn_vector",
                        "dimension": text_dims,
                        "method": {
                            "name": "hnsw",
                            "engine": "faiss",
                            "space_type": "l2",
                            "parameters": {"ef_construction": 512, "m": 16},
                        },
                    },
                    "multimodalVector": {
                        "type": "knn_vector",
                        "dimension": multimodal_dims,
                        "method": {
                            "name": "hnsw",
                            "engine": "faiss",
                            "space_type": "l2",
                            "parameters": {"ef_construction": 512, "m": 16},
                        },
                    },
                }
            },
        }
        return self.request("PUT", f"/{index}", mapping)

    # --------------------------------------------------------------- writing
    def bulk_index(self, index: str, documents: list[dict[str, Any]], batch_size: int = 50) -> int:
        """Index documents in batches. AOSS assigns ids, so omit _id."""
        indexed = 0

        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            lines: list[str] = []

            for doc in batch:
                lines.append(json.dumps({"index": {"_index": index}}))
                lines.append(json.dumps(doc))

            response = self.request("POST", "/_bulk", "\n".join(lines) + "\n")

            if response.get("errors"):
                failures = [
                    item["index"].get("error")
                    for item in response.get("items", [])
                    if item.get("index", {}).get("error")
                ]
                LOG.error("bulk index errors (%d): %s", len(failures), json.dumps(failures[:3]))
                raise RuntimeError(f"{len(failures)} documents failed to index")

            indexed += len(batch)

        return indexed

    def delete_by_document(self, index: str, document_id: str) -> dict[str, Any]:
        """Used on re-ingestion so an updated file does not leave stale chunks behind."""
        return self.request(
            "POST",
            f"/{index}/_delete_by_query",
            {"query": {"term": {"documentId": document_id}}},
        )

    # --------------------------------------------------------------- reading
    def knn_search(
        self,
        index: str,
        field: str,
        vector: list[float],
        k: int = 10,
        filters: dict[str, Any] | None = None,
        source_excludes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        knn_clause: dict[str, Any] = {"vector": vector, "k": k}
        if filters:
            knn_clause["filter"] = filters

        body = {
            "size": k,
            "query": {"knn": {field: knn_clause}},
            "_source": {"excludes": source_excludes or ["textVector", "multimodalVector"]},
        }
        response = self.request("POST", f"/{index}/_search", body)
        return response.get("hits", {}).get("hits", [])

    def keyword_search(self, index: str, query: str, k: int = 10) -> list[dict[str, Any]]:
        body = {
            "size": k,
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["text^2", "caption^1.5", "title"],
                    "type": "best_fields",
                }
            },
            "_source": {"excludes": ["textVector", "multimodalVector"]},
        }
        response = self.request("POST", f"/{index}/_search", body)
        return response.get("hits", {}).get("hits", [])

    def count(self, index: str) -> int:
        return int(self.request("POST", f"/{index}/_count", {"query": {"match_all": {}}}).get("count", 0))
