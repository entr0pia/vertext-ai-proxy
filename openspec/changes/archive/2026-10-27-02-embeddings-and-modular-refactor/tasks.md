# Tasks

## 1. Directory Structure and Core Module Migration

- [x] 1.1 Create `core/`, `schemas/`, `services/`, and `routers/` package directories with `__init__.py` files.
- [x] 1.2 Migrate `config.py`, `auth.py`, `security.py`, and create `exceptions.py` under `src/vertext_ai_proxy/core/`, verifying unit tests still pass.

## 2. Request & Response Schemas

- [x] 2.1 Implement `schemas/embeddings.py` and `schemas/common.py` modeling OpenAI embedding request/response structures with Pydantic validation tests.
- [x] 2.2 Implement `schemas/chat.py` and `schemas/models.py` for chat completion and model listing schemas.

## 3. Services & Upstream Client Implementation

- [x] 3.1 Implement `services/vertex_client.py` to encapsulate Vertex AI OAuth injection, base URL formatting, and timeouts with unit tests.
- [x] 3.2 Implement `services/embedding_service.py` translating OpenAI payloads to Vertex AI `:predict` format and mapping outputs, with unit tests.
- [x] 3.3 Migrate chat completions forwarding and SSE streaming pass-through to `services/chat_service.py`.
- [x] 3.4 Migrate dynamic model garden discovery and 1-hour caching to `services/model_service.py`.

## 4. Routers and FastAPI Application Reorganization

- [x] 4.1 Implement `routers/health.py`, `routers/v1/chat.py`, `routers/v1/embeddings.py`, and `routers/v1/models.py` using FastAPI `APIRouter`.
- [x] 4.2 Refactor `app.py` to mount routers and configure lifespan, and create `main.py` entrypoint ensuring `python -m vertext_ai_proxy.app` remains fully compatible.

## 5. End-to-End Verification & Documentation

- [x] 5.1 Implement comprehensive unit and integration tests in `tests/test_embeddings.py` covering single/multiple inputs, dimensions, and error cases.
- [x] 5.2 Run full automated test suite (`$env:PYTHONUTF8=1; uv run pytest -v`) and verify all tests pass.
- [x] 5.3 Update `README.md` and `AGENTS.md` to document the new `/v1/embeddings` endpoint and modular repository architecture.

## Workflow follow-up

- Archive the change after the project's review requirements are satisfied.
- Verify the archived result.
