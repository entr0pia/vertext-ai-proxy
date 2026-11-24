# AGENTS.md

> Developer and AI Agent technical guide for `Vertext-AI-Proxy`.
> Follows the public guidance at [agents.md](https://agents.md/).

---

## 1. Project Overview

`Vertext-AI-Proxy` is a lightweight, asynchronous reverse proxy that translates Google Cloud Vertex AI's OpenAI-compatible endpoint (`https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/endpoints/openapi`) into standard static OpenAI API Key authentication.

### Core Functionality
- **Auth Translation**: Intercepts downstream OpenAI client requests authenticated with static API keys (`Authorization: Bearer <key>`) and proxies them to Vertex AI using short-lived Google Cloud OAuth 2.0 Bearer tokens derived from a Google Service Account JSON.
- **Dynamic Model Garden & Single-Model Retrieval**: Automatically queries Google Cloud Vertex AI's `v1beta1/publishers/google/models` endpoint to serve `GET /v1/models` and `GET /v1/models/{model:path}` dynamically, with in-memory 1-hour caching and graceful configuration fallback.
- **SSE Streaming Pass-Through**: Provides real-time, non-buffered Server-Sent Events (SSE) streaming for `/v1/chat/completions` using `httpx.AsyncClient` (`Accept-Encoding: identity` + `aiter_bytes()`).
- **Responses API with Bounded In-Memory State**: Translates `POST /v1/responses` (non-streaming and SSE semantic events, tools, reasoning, structured output) to Vertex AI `:generateContent` / `:streamGenerateContent?alt=sse`, backed by an LRU+TTL in-memory store supporting `previous_response_id`, `GET`/`DELETE /v1/responses/{response_id}`, and `POST /v1/responses/{response_id}/cancel`.
- **Images Generation & Editing**: Translates `POST /v1/images/generations` and `POST /v1/images/edits` (multipart) to Vertex AI Imagen (`:predict`) or Gemini image models (`:generateContent`), without format blocking or OpenAI model alias rewriting.
- **Audio Speech & Transcription/Translation**: Translates `POST /v1/audio/speech` (TTS, upstream-driven audio passthrough with stdlib `wave` WAV/PCM conversion, no `ffmpeg`), `POST /v1/audio/transcriptions`, and `POST /v1/audio/translations` to Vertex AI Gemini `:generateContent`.
- **Embeddings Proxy & Adaptation**: Translates standard OpenAI `POST /v1/embeddings` requests to Google Vertex AI native `:predict` or `:embedContent` endpoint, supporting `text-embedding-004`, `gemini-embedding-001`, `gemini-embedding-2`, and batching.
- **Secure by Default**: Binds to `127.0.0.1:8766` by default and enforces startup-time validation to halt execution if placeholder or empty keys are configured.

### Tech Stack
- **Language**: Python 3.11+
- **Framework**: FastAPI, Uvicorn, Starlette
- **HTTP Engine**: `httpx` (async client with connection pooling)
- **Auth**: `google-auth` (`google.oauth2.service_account`)
- **Config & Validation**: Pydantic v2, PyYAML
- **Package Manager**: `uv`
- **Specification**: OpenSpec (`spec-driven` schema)

---

## 2. Repository Structure

```text
Vertext-AI-Proxy/
├── .gitignore                   # Excludes credentials, *.json, config.local.yaml
├── .python-version              # Python version lock (3.11)
├── pyproject.toml               # Dependencies managed via uv
├── uv.lock                      # Exact package lockfile
├── config.example.yaml          # Template configuration file
├── README.md                    # User-facing setup and client guides
├── AGENTS.md                    # Agent technical context (this document)
├── openspec/
│   ├── config.yaml              # OpenSpec configuration
│   ├── specs/                   # Canonical system specifications (e.g., openai-proxy)
│   └── changes/archive/         # Archived changesets (e.g. 2026-10-27-01-vertex-openai-proxy)
├── src/
│   └── vertext_ai_proxy/
│       ├── __init__.py
│       ├── cli.py               # Standalone CLI interface (vap init, vap run)
│       ├── main.py              # CLI server startup runner
│       ├── app.py               # FastAPI application factory, lifespan, global middleware
│       ├── proxy.py             # Facade wrapper coordinating chat, models, and embeddings
│       ├── models.py            # Model identifier normalization (auto-prefix 'google/')
│       ├── core/                # Foundational components
│       │   ├── __init__.py
│       │   ├── auth.py          # GCPTokenManager (SA credentials & OAuth lifecycle)
│       │   ├── config.py        # Pydantic configuration loader & startup validator
│       │   ├── exceptions.py    # OpenAIAPIException and handlers
│       │   ├── security.py      # APIKeyValidator (constant-time token check)
│       │   └── templates.py     # Embedded configuration template for 'vap init'
│       ├── schemas/             # Request/response DTOs
│       │   ├── __init__.py
│       │   ├── audio.py         # Audio TTS/STT schemas
│       │   ├── common.py        # Error & usage schemas
│       │   ├── chat.py          # Chat completions schemas
│       │   ├── embeddings.py    # Embeddings request & response schemas
│       │   ├── images.py        # Image generation & response schemas
│       │   ├── models.py        # Model listing schemas
│       │   └── responses.py     # Responses API request/delete schemas
│       ├── services/            # Domain logic and upstream integrations
│       │   ├── __init__.py
│       │   ├── vertex_client.py # Low-level Vertex HTTP transport & URL resolution
│       │   ├── audio_service.py # Gemini TTS & STT/translation adaptation
│       │   ├── chat_service.py  # Chat completions forwarding & SSE streaming
│       │   ├── embedding_service.py # Embeddings protocol translation (OpenAI -> :predict/:embedContent)
│       │   ├── image_service.py # Imagen (:predict) & Gemini (:generateContent) image routing
│       │   ├── model_service.py # Publisher models discovery, single lookup & 1-hour caching
│       │   ├── response_store.py # Bounded LRU+TTL in-memory Responses store & cancel handles
│       │   └── response_service.py # Responses API translation & SSE semantic streaming
│       └── routers/             # FastAPI APIRouter modules
│           ├── __init__.py
│           ├── health.py        # GET /healthz
│           └── v1/
│               ├── __init__.py
│               ├── audio.py     # POST /v1/audio/{speech,transcriptions,translations}
│               ├── chat.py      # POST /v1/chat/completions
│               ├── embeddings.py# POST /v1/embeddings
│               ├── images.py    # POST /v1/images/{generations,edits}
│               ├── models.py    # GET /v1/models & GET /v1/models/{model:path}
│               └── responses.py # POST/GET/DELETE /v1/responses & cancel
└── tests/
    ├── test_audio.py            # Integration tests for Audio TTS/STT/translation
    ├── test_auth.py             # Unit tests for token manager and expiry logic
    ├── test_config.py           # Unit tests for config loading, priority, and validation
    ├── test_embeddings.py       # Integration tests for embeddings proxying & validation
    ├── test_images.py           # Integration tests for Imagen & Gemini image routes
    ├── test_models.py           # Unit & route tests for model normalization and lookup
    ├── test_proxy.py            # Integration tests for proxy routing and streaming
    ├── test_responses.py        # Unit & integration tests for Responses API & memory store
    ├── test_schemas.py          # Unit tests for schema validation
    ├── test_security.py         # Unit tests for API key validation and 401 handling
    └── test_vertex_client.py    # Unit tests for VertexClient URL builders & error parsing
```

---

## 3. Setup and Environment

This workstation runs on **Windows 10, PowerShell (`pwsh`)**, using **`uv`** as the dedicated Python package manager.

### Prerequisites
- Python 3.11+ installed (via Scoop or uv)
- `uv` installed and available in PATH

### Installing Dependencies
```powershell
uv sync
```

### Adding / Removing Dependencies
```powershell
# Add runtime dependency
uv add <package-name>

# Add development dependency
uv add --dev <package-name>
```

---

## 4. Development Workflow

### Configuration Setup
The proxy reads configuration in the following order of precedence:
1. `config.local.yaml` (highest priority, ignored by Git)
2. `config.yaml`
3. In-memory sensible defaults

To configure for local development:
```powershell
Copy-Item config.example.yaml config.local.yaml
```
Ensure `config.local.yaml` specifies:
- `proxy.api_keys`: A non-placeholder secret key (e.g. `sk-my-custom-dev-token`).
- `gcp.service_account_file`: Valid path to a Google Service Account JSON file (raw GCP download or wrapped `service_account` structure).

### Running the Server
Always set `PYTHONUTF8=1` on Windows when running via uv:
```powershell
$env:PYTHONUTF8=1; uv run python -m vertext_ai_proxy.app
```
By default, the server binds to `http://127.0.0.1:8766`.

---

## 5. Testing Instructions

All automated tests use `pytest` and `pytest-asyncio`.

### Running All Tests
```powershell
$env:PYTHONUTF8=1; uv run pytest -v
```

### Running Specific Test Modules
```powershell
# Test configuration loading and startup validation
$env:PYTHONUTF8=1; uv run pytest tests/test_config.py

# Test token management
$env:PYTHONUTF8=1; uv run pytest tests/test_auth.py

# Test proxy routing and streaming
$env:PYTHONUTF8=1; uv run pytest tests/test_proxy.py
```

### Testing Guidelines for Agents
- When writing tests for FastAPI endpoints, use `TestClient(app)` for synchronous route assertions, or `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")` for async/streaming assertions.
- Mock upstream Google Cloud HTTP calls using `unittest.mock.AsyncMock` on `httpx.AsyncClient`. Do NOT make unmocked external requests inside `tests/`.

### End-to-End (E2E) Live Testing
- For real upstream integration testing, launch the proxy server with local credentials (`vap run` or `$env:PYTHONUTF8=1; uv run python -m vertext_ai_proxy.app`) and execute live HTTP requests against `http://127.0.0.1:8766` using a scratch script (`<conversation-id>/scratch/test_e2e.py`).
- Verify health (`/healthz`), chat completions (`/v1/chat/completions`), and embeddings (`/v1/embeddings`) against actual Vertex AI upstream endpoints.

---

## 6. Code Style and Conventions

- **Python Version**: Strictly Python 3.11+. Use modern union and generic types (`str | None`, `list[str]`, `dict[str, Any]`, `set[str]`).
- **No `from __future__ import annotations`**: Do not introduce this import; use native Python 3.11 type annotations directly.
- **Security Guardrails**:
  - Never commit credentials (`service_account.json`, `*.json`) or local overrides (`config.local.yaml`).
  - Always use `secrets.compare_digest` when validating API keys to prevent timing attacks.
  - Fail fast: maintain startup checks in `validate_config` preventing execution with default placeholder keys.
- **OpenAI Compatibility**:
  - Exceptions must match OpenAI standard JSON: `{"error": {"message": ..., "type": ..., "code": ...}}`.
  - Streamed chunks must use standard SSE lines: `data: {"choices": [{"delta": ...}]}\n\n` ending with `data: [DONE]\n\n`.
  - Upstream SSE streams should pass `Accept-Encoding: identity` and consume via `aiter_bytes()` to prevent raw gzip bytes from leaking to clients.

---

## 7. OpenSpec Workflow

This project adheres to the **OpenSpec** specification-driven change management workflow:
- `openspec/specs/openai-proxy/spec.md`: The single source of truth for the proxy's observable behavior.
- Before introducing substantial feature changes, use the OpenSpec workflow (`/openspec-explore`, `/openspec-propose`, `/openspec-apply-change`, `/openspec-archive-change`).
- **Mandatory End-to-End (E2E) Verification**:
  - After completing tasks in `/openspec-apply-change` and before archiving or committing, ALWAYS execute an end-to-end integration test against the running proxy server (`http://127.0.0.1:8766` with local `config.local.yaml`) to verify real upstream Vertex AI connectivity and protocol behavior without mocks.
- **Archive Naming Convention**:
  - When archiving changes into `openspec/changes/archive/`, the archive directory name MUST include a two-digit incremental sequence number between the date and the change name: `YYYY-MM-DD-NN-<change-name>` (e.g., `2026-10-28-01-support-gemini-embedding-2`).
- Verify specification compliance with:
  ```powershell
  openspec validate --specs
  ```

---

## 8. Git Safety Rules
- **DO NOT** run `git add`, `git commit`, or `git push` automatically without prior user authorization. Always ask the user before executing git write operations.
