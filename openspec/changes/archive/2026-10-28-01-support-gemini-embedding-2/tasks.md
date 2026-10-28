# Tasks

## 1. Upstream Client & URL Resolution

- [x] 1.1 In `VertexClient` (`src/vertext_ai_proxy/services/vertex_client.py`), implement `get_gemini_embed_url(model: str, action: str = "embedContent")` and verify URL formats for both `:embedContent` and `:batchEmbedContents`
- [x] 1.2 Verify that location-based regional domains (e.g. `aiplatform.googleapis.com` or `{location}-aiplatform.googleapis.com`) correctly resolve in `get_gemini_embed_url`

## 2. Gemini Embedding Protocol Adaptation

- [x] 2.1 In `EmbeddingService` (`src/vertext_ai_proxy/services/embedding_service.py`), add model family detection to branch `gemini-embedding-2` requests away from legacy `:predict`
- [x] 2.2 Implement `_create_gemini_embeddings` in `EmbeddingService` to handle single string input (`input: "text"`) via `:embedContent` with payload `{"content": {"parts": [{"text": "text"}]}}`
- [x] 2.3 Implement batch strings input (`input: ["text1", "text2"]`) via `:batchEmbedContents` with payload `{"requests": [{"model": ..., "content": {"parts": [{"text": ...}]}}]}`
- [x] 2.4 Translate optional `dimensions` parameter to `outputDimensionality` in the upstream Gemini payload and ensure valid non-null integer token counts in `usage`

## 3. Integration Testing & Documentation

- [x] 3.1 In `tests/test_embeddings.py`, add mock tests covering:
  - Single text embedding with `gemini-embedding-2`
  - Batch text embeddings with `gemini-embedding-2`
  - Custom dimension scaling with `dimensions=768`
  - Upstream 4xx/5xx error handling propagation
- [x] 3.2 Run the complete test suite (`$env:PYTHONUTF8=1; uv run pytest -v`) and verify all tests pass without regressions
- [x] 3.3 Update `README.md` to list `gemini-embedding-2` among supported embedding models with example code
