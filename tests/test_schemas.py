import pytest
from pydantic import ValidationError
from vertext_ai_proxy.schemas.embeddings import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingObject,
    EmbeddingUsage,
)


def test_embedding_request_single_string_valid():
    req = EmbeddingRequest(model="text-embedding-004", input="hello world")
    assert req.model == "text-embedding-004"
    assert req.input == "hello world"
    assert req.dimensions is None


def test_embedding_request_list_valid():
    req = EmbeddingRequest(
        model="google/gemini-embedding-001",
        input=["text 1", "text 2"],
        dimensions=512,
    )
    assert req.model == "google/gemini-embedding-001"
    assert len(req.input) == 2
    assert req.dimensions == 512


def test_embedding_request_empty_string_rejected():
    with pytest.raises(ValidationError):
        EmbeddingRequest(model="text-embedding-004", input="   ")


def test_embedding_request_empty_list_rejected():
    with pytest.raises(ValidationError):
        EmbeddingRequest(model="text-embedding-004", input=[])


def test_embedding_request_batch_limit_rejected():
    with pytest.raises(ValidationError):
        EmbeddingRequest(model="text-embedding-004", input=["text"] * 251)


def test_embedding_response_serialization():
    resp = EmbeddingResponse(
        model="text-embedding-004",
        data=[
            EmbeddingObject(index=0, embedding=[0.1, 0.2, 0.3]),
            EmbeddingObject(index=1, embedding=[0.4, 0.5, 0.6]),
        ],
        usage=EmbeddingUsage(prompt_tokens=10, total_tokens=10),
    )
    dumped = resp.model_dump()
    assert dumped["object"] == "list"
    assert len(dumped["data"]) == 2
    assert dumped["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert dumped["usage"]["total_tokens"] == 10
