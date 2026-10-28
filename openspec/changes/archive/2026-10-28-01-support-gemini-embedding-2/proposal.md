# Proposal

## Why

Google Cloud Vertex AI (Gemini Enterprise Agent Platform) provides `gemini-embedding-2` as its flagship multimodal embedding model, featuring an 8192-token context window, unified cross-modal vector space, and Matryoshka Representation Learning (MRL) dimension scaling. However, unlike traditional text embedding models (`text-embedding-004`, `gemini-embedding-001`) that use Vertex AI's Core `:predict` REST endpoint with `{"instances": [{"content": ...}]}`, `gemini-embedding-2` uses Google's newer `:embedContent` (for single inputs) and `:batchEmbedContents` (for batch inputs) REST protocol. Without adapter routing in the proxy, downstream OpenAI clients attempting to embed via `gemini-embedding-2` fail with upstream HTTP errors.

Adding protocol adaptation for `gemini-embedding-2` enables downstream OpenAI-compatible applications (Cursor, LangChain, LlamaIndex, Dify, Cherry Studio) to seamlessly leverage Google's latest multimodal embedding model through the standard `POST /v1/embeddings` endpoint.

## What Changes

- **Model Routing Strategy**: Enhance `EmbeddingService` to detect model identifiers and dispatch to the appropriate upstream protocol adapter:
  - Models matching `gemini-embedding-2` (e.g. `gemini-embedding-2`, `google/gemini-embedding-2`) route to the Gemini EmbedContent adapter.
  - Legacy/traditional models (`text-embedding-004`, `gemini-embedding-001`, `text-multilingual-embedding-002`) continue routing to Vertex AI's `:predict` endpoint.
- **Gemini Embeddings Protocol Adaptation**:
  - Implement upstream URL resolution for `:embedContent` and `:batchEmbedContents` in `VertexClient`.
  - For single input string: format request as `{"content": {"parts": [{"text": <input>}]}}` and call `:embedContent`.
  - For batch input strings: format request as `{"requests": [{"model": <model_path>, "content": {"parts": [{"text": t}]}} for t in inputs]}` and call `:batchEmbedContents`.
  - Translate `dimensions` to top-level `outputDimensionality`.
  - Parse upstream response (`embedding.values` or `embeddings[i].values`) and normalize into standard OpenAI `EmbeddingResponse` DTO.
- **Token Usage Estimation**: Calculate token usage for `gemini-embedding-2` response (or rough heuristic fallback) to populate OpenAI `usage` field (`prompt_tokens`, `total_tokens`).

## Capabilities

### Modified Capabilities
- `openai-proxy`: Extend requirement `Embeddings Proxying and Schema Translation` to support dual upstream protocols—dispatching traditional models to `:predict` and Gemini multimodal embedding models (`gemini-embedding-2`) to `:embedContent` / `:batchEmbedContents`.

## Impact

- **Affected Code**:
  - `src/vertext_ai_proxy/services/vertex_client.py`: Add URL builders for `:embedContent` and `:batchEmbedContents`.
  - `src/vertext_ai_proxy/services/embedding_service.py`: Add model detection and protocol translation for `gemini-embedding-2`.
  - `tests/test_embeddings.py`: Add unit and integration tests verifying both single and batch `gemini-embedding-2` calls, error handling, and dimension scaling.
- **APIs & Dependencies**: No external package additions; uses existing `httpx` and `pydantic`.
- **Breaking Changes**: None. Backwards compatibility for `text-embedding-004` and existing endpoints is 100% preserved.
