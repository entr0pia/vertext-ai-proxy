# Proposal

## Why

Downstream AI workflows (such as RAG pipelines, Cursor indexing, LangChain, and Dify) rely heavily on the OpenAI-compatible `/v1/embeddings` endpoint to generate text representations. While Google Cloud Vertex AI's experimental `endpoints/openapi/embeddings` is not yet generally available (returning 500/400 in testing), its native `:predict` endpoint (`publishers/google/models/{model}:predict`) provides high-performance, stable embeddings for models like `text-embedding-004` and `gemini-embedding-001`. 

Simultaneously, the existing proxy codebase is entirely flat in `src/vertext_ai_proxy/`, combining HTTP handlers, streaming mechanics, authentication, caching, and model normalization in monolithic modules. To support additional capabilities cleanly without technical debt, the architecture must transition to a decoupled, layered structure (`core/`, `schemas/`, `services/`, `routers/`).

## What Changes

- **Embeddings Proxying & Schema Adaptation**: Implement a dedicated `POST /v1/embeddings` endpoint conforming to OpenAI embedding schema, translating incoming OpenAI requests into Vertex AI's native `:predict` instances format, calling `publishers/google/models/{model}:predict`, and formatting predictions back to standard OpenAI embedding responses.
- **Architectural Modularization (Non-Flat Refactor)**:
  - Migrate core concerns to `core/` (`config.py`, `auth.py`, `security.py`, `exceptions.py`).
  - Introduce explicit Pydantic DTOs in `schemas/` (`common.py`, `chat.py`, `embeddings.py`, `models.py`).
  - Isolate business logic and upstream interactions in `services/` (`vertex_client.py`, `chat_service.py`, `embedding_service.py`, `model_service.py`).
  - Organize endpoint declarations cleanly using FastAPI `APIRouter` in `routers/` (`health.py`, `v1/chat.py`, `v1/embeddings.py`, `v1/models.py`).
  - Provide a clean application factory in `app.py` and dedicated CLI entrypoint in `main.py`.

## Capabilities

### New Capabilities
*(None. Vector embeddings falls under the durable `openai-proxy` boundary).*

### Modified Capabilities
- `openai-proxy`: Add requirement for Embeddings Proxying and Schema Translation (`POST /v1/embeddings`), ensuring standard OpenAI input/output formatting, dimension negotiation, and token usage accounting over Vertex AI's native prediction endpoint.

## Impact

- **Downstream Compatibility**: Enables all standard OpenAI SDKs and tools to query vector embeddings via `POST /v1/embeddings` alongside chat completions and model listing.
- **Codebase Organization**: Internal import paths within `vertext_ai_proxy` will be modernized to follow the new package hierarchy, while public API contracts and CLI invocation remain fully backwards compatible.
- **Tests**: Existing test suite will be reorganized to validate individual services and schemas alongside end-to-end integration tests.
