"""Chunking and fusion tests — pure logic, no AWS."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shared.chunking import chunk_text, normalise  # noqa: E402


def _hit(chunk_id: str, score: float = 1.0) -> dict:
    return {"_id": chunk_id, "_score": score, "_source": {"chunkId": chunk_id, "text": chunk_id}}


@pytest.fixture
def rrf():
    os.environ.setdefault("INDEX_NAME", "test")
    os.environ.setdefault("COLLECTION_ENDPOINT", "https://example.aoss.amazonaws.com")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    from query.handler import reciprocal_rank_fusion

    return reciprocal_rank_fusion


def test_chunks_respect_the_size_limit():
    text = "Sentence number one. " * 400
    chunks = chunk_text(text, chunk_size=500, overlap=80)

    assert chunks
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_chunks_overlap_so_facts_survive_the_seam():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text, chunk_size=300, overlap=60)

    assert len(chunks) > 1
    tail_of_first = chunks[0][-40:]
    assert any(token in chunks[1] for token in tail_of_first.split())


def test_short_and_empty_inputs():
    assert chunk_text("just a line") == ["just a line"]
    assert chunk_text("   ") == []


def test_normalise_collapses_whitespace():
    assert normalise("a   b\n\n\n\nc") == "a b\n\nc"


def test_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=100, overlap=100)


def test_rrf_rewards_documents_ranked_well_by_several_engines(rrf):
    vector_hits = [_hit("a"), _hit("b"), _hit("c")]
    keyword_hits = [_hit("c"), _hit("a"), _hit("d")]

    fused = rrf([vector_hits, keyword_hits])
    order = [item["chunkId"] for item in fused]

    # 'a' is 1st and 2nd; 'c' is 3rd and 1st. Both beat single-list entries.
    assert order[0] == "a"
    assert set(order[:2]) == {"a", "c"}
    assert order[-1] == "d"


def test_rrf_deduplicates(rrf):
    fused = rrf([[_hit("a"), _hit("a")], [_hit("a")]])
    assert len([item for item in fused if item["chunkId"] == "a"]) == 1
