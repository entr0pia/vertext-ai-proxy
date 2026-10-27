# Tasks

## 1. Project Scaffolding and Environment Configuration

- [x] 1.1 Initialize Python project with `pyproject.toml`, `.python-version`, and `.gitignore` (specifically ignoring `config.local.yaml`, `service_account.json`, and `*.json`), verified via `uv sync` and git status inspection
- [x] 1.2 Create `config.example.yaml` (defaulting to host `127.0.0.1` and port `8766`) and YAML configuration loader (`config.py`) with startup validation rejecting placeholder keys, verified by unit tests

## 2. Authentication and Token Management

- [x] 2.1 Implement `GCPTokenManager` to load Google Service Account credentials, extract default `project_id`, and manage OAuth 2.0 access token caching/refreshing with `asyncio.Lock`, verified by unit tests mocking Google Auth transport
- [x] 2.2 Implement proxy API key verification dependency checking incoming `Authorization: Bearer <key>` against `proxy.api_keys`, verified by unit tests asserting 200 on authorized keys and 401 on unauthorized requests

## 3. Proxy Routes and Streaming Pass-Through

- [x] 3.1 Implement model identifier normalization helper (auto-prefixing `google/` for unadorned Gemini models) and the `/v1/models` endpoint, verified with unit tests
- [x] 3.2 Implement `/v1/chat/completions` proxy endpoint using `httpx.AsyncClient` (supporting `http_proxy` egress setting) with non-blocking SSE streaming pass-through, verified with mock upstream tests for streaming and non-streaming responses
- [x] 3.3 Implement error handling and `/healthz` probe endpoint, verified by checking error response format matches OpenAI error structure

## 4. End-to-End Verification and Documentation

- [x] 4.1 Write comprehensive automated test suite and verify all tests pass via `uv run pytest`
- [x] 4.2 Create `README.md` providing step-by-step instructions for placing `service_account.json`, creating `config.local.yaml`, replacing example API keys, and connecting clients on port 8766
