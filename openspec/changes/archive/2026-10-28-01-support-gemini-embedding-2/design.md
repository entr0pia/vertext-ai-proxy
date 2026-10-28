# Design

## Context

`Vertext-AI-Proxy` provides standard OpenAI-compatible endpoints that bridge to Google Cloud Vertex AI. Currently, `POST /v1/embeddings` in `EmbeddingService` routes all requests to Google's Core Vertex AI `:predict` endpoint with payload `{"instances": [{"content": ...}]}`.

Google Cloud's latest multimodal embedding model, `gemini-embedding-2`, operates on Google's Gemini-native REST API (`:embedContent` for single embeddings and `:batchEmbedContents` for bulk requests) rather than `:predict`. See `proposal.md` for background motivation.

## Goals / Non-Goals

**Goals:**
- Provide seamless, transparent protocol translation in `POST /v1/embeddings` for `gemini-embedding-2` without downstream client changes.
- Support both single string input (`input: "text"`) and array batch input (`input: ["text1", "text2"]`).
- Support optional dimension scaling (`dimensions: 768`, `1536`, `3072`) via `outputDimensionality`.
- Maintain complete backwards compatibility for traditional models (`text-embedding-004`, `gemini-embedding-001`).

**Non-Goals:**
- Downstream binary/multimodal upload payloads: OpenAI `/v1/embeddings` strictly accepts text strings or token arrays. Multimodal inputs (e.g. video files/audio files uploaded via HTTP) remain out of scope for the standard `/v1/embeddings` schema.
- Non-OpenAI proprietary embedding parameters.

## Decisions

### 1. Strategy Routing in `EmbeddingService`
- **Decision**: In `EmbeddingService.create_embeddings`, inspect the normalized model identifier:
  ```python
  clean_model = request.model.split("/")[-1].lower()
  if clean_model.startswith("gemini-embedding-2"):
      return await self._create_gemini_embeddings(request, clean_model)
  return await self._create_predict_embeddings(request, clean_model)
  ```
- **Rationale**: Isolates protocol differences cleanly without breaking existing `:predict` logic or cluttering a single monolith method.
- **Alternatives Considered**: Creating a new endpoint (e.g., `/v1/gemini/embeddings`). Rejected because downstream clients (Cursor, LangChain) expect the uniform `/v1/embeddings` path and rely on `model` parameter to choose the model.

### 2. Single vs. Batch Upstream API Dispatch
- **Decision**:
  - For single input (`len(texts) == 1`): call `:embedContent` with `{"content": {"parts": [{"text": texts[0]}]}}`.
  - For batch inputs (`len(texts) > 1`): call `:batchEmbedContents` with:
    ```json
    {
      "requests": [
        {
          "model": "publishers/google/models/{clean_model}",
          "content": {"parts": [{"text": text}]},
          "outputDimensionality": dimensions
        }
      ]
    }
    ```
- **Rationale**: Direct mapping aligns with Google Cloud documentation and avoids unnecessary batch wrapping overhead for single text queries, while supporting high-throughput batching.
- **Alternatives Considered**: Always using `:batchEmbedContents`. While valid, `:embedContent` has cleaner error reporting for single-item queries and lower serialized payload size.

### 3. URL Resolution in `VertexClient`
- **Decision**: Add helper method `get_gemini_embed_url(model: str, action: str = "embedContent")` to `VertexClient`:
  ```python
  def get_gemini_embed_url(self, model: str, action: str = "embedContent", location: str | None = None) -> str:
      loc = location or self.location
      domain = self.get_domain(loc)
      clean_model = model.split("/")[-1]
      return (
          f"https://{domain}/v1/projects/{self.project_id}"
          f"/locations/{loc}/publishers/google/models/{clean_model}:{action}"
      )
  ```
- **Rationale**: Reuses the established domain resolution pattern (`self.get_domain`) while accommodating `:embedContent` and `:batchEmbedContents`.

### 4. Normalization of Usage Statistics
- **Decision**: `gemini-embedding-2` responses may omit detailed token counts in certain API versions. If upstream does not provide token statistics, estimate prompt tokens as `max(1, len(text) // 4)` so downstream OpenAI clients always receive non-null integer values in `usage.prompt_tokens` and `usage.total_tokens`.

## Risks / Trade-offs

- **[Risk] Regional model availability**: `gemini-embedding-2` is available primarily in select regions (e.g., `us-central1`, `us`, `global`).
  - *Mitigation*: The proxy defaults location to `global` or the configured location in `config.local.yaml`. Clear upstream error messages will be forwarded if the configured location lacks the model.
- **[Risk] Model name formatting in batch requests**: `:batchEmbedContents` requires a `model` attribute inside each request element.
  - *Mitigation*: Format as `publishers/google/models/{clean_model}` as specified in Google Cloud GenAI API references.
