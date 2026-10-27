# Design

## Context

See `proposal.md` for background and motivation. The existing flat structure in `src/vertext_ai_proxy` couples routing, HTTP transport, authentication, streaming, and model logic. Introducing vector embeddings via Vertex AI's native `:predict` endpoint requires clean protocol adaptation.

## Goals / Non-Goals

**Goals:**
- Provide a clean, robust OpenAI-compatible `POST /v1/embeddings` endpoint proxying to Vertex AI native `:predict` API.
- Support standard embedding models: `text-embedding-004`, `gemini-embedding-001`, and `text-multilingual-embedding-002`.
- Refactor the codebase from a flat layout into distinct, cohesive layers: `core/`, `schemas/`, `services/`, and `routers/`.
- Maintain 100% backward compatibility for existing entry points, client configurations, and tests.

**Non-Goals:**
- Supporting custom fine-tuned embedding model endpoints hosted on dedicated Vertex AI private endpoints (out of scope for public publisher models).
- Implementing local vector computation or caching of embedding vectors.

## Decisions

### 1. Directory and Package Layout
Refactor `src/vertext_ai_proxy/` into clear domain layers:
```text
src/vertext_ai_proxy/
├── __init__.py
├── main.py                     # Entrypoint running uvicorn.run()
├── app.py                      # FastAPI app factory, lifespan, global middleware
├── core/                       # Foundational components
│   ├── __init__.py
│   ├── config.py               # Pydantic configuration and validation
│   ├── auth.py                 # GCP TokenManager & service account credentials
│   ├── security.py             # APIKeyValidator using secrets.compare_digest
│   └── exceptions.py           # OpenAIAPIException and exception handlers
├── schemas/                    # Pydantic request/response models (DTOs)
│   ├── __init__.py
│   ├── chat.py                 # Chat request/response schemas
│   ├── embeddings.py           # Embedding request/response schemas
│   └── models.py               # Model listing schemas
├── services/                   # Business and upstream interaction logic
│   ├── __init__.py
│   ├── vertex_client.py        # Low-level Vertex HTTP transport & URL resolution
│   ├── chat_service.py         # Chat completions forwarding & SSE streaming
│   ├── embedding_service.py    # Embeddings translation (OpenAI -> Vertex :predict)
│   └── model_service.py        # Publisher models discovery & in-memory caching
└── routers/                    # FastAPI route definitions
    ├── __init__.py
    ├── health.py               # /healthz endpoint
    └── v1/
        ├── __init__.py
        ├── chat.py             # /v1/chat/completions
        ├── embeddings.py       # /v1/embeddings
        └── models.py           # /v1/models
```
*Rationale*: Separates HTTP routing from business logic and upstream communications, ensuring modularity, easy mockability in unit tests, and scalability.

### 2. Embeddings Protocol Translation (Adapter Pattern)
Because Google's `endpoints/openapi/embeddings` is unreliable/internal, `EmbeddingService` translates requests to the native Vertex AI prediction endpoint:
`https://{domain}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model}:predict`
- **Model Normalization**: Strips optional `google/` prefix if present, resolving the clean model identifier (e.g. `text-embedding-004`).
- **Input Normalization**: Normalizes `input` from `str` or `list[str]` into `instances: [{"content": t} for t in inputs]`.
- **Dimensions**: If `dimensions` is provided, passes `parameters: {"outputDimensionality": dimensions}`.
- **Output Mapping**: Maps each `predictions[i].embeddings.values` into an OpenAI embedding item `{"object": "embedding", "index": i, "embedding": values}`.
- **Usage Statistics**: Computes token counts from `statistics.token_count` to return standard OpenAI `usage: {"prompt_tokens": N, "total_tokens": N}`.

### 3. Backwards-Compatible Invocation
Keep `src/vertext_ai_proxy/app.py` functioning both as module import and direct script runner (`if __name__ == '__main__': main()`), delegating to `main.py`, so commands like `python -m vertext_ai_proxy.app` continue to work without modification.

## Risks / Trade-offs

- **[Risk] High-volume embedding batches** → Vertex AI `:predict` has a limit on maximum instances per request (typically 250).  
  *Mitigation*: Validate and reject batches exceeding 250 inputs with a descriptive HTTP 400 error.
- **[Risk] Location differences for regional models** → Certain models may only reside in specific regions.  
  *Mitigation*: Use configured `gcp.location` (defaulting to `global` or `us-central1`), automatically routing to `{location}-aiplatform.googleapis.com` for regional endpoints or `aiplatform.googleapis.com` for global.
