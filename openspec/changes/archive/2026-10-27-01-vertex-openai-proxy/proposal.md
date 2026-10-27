# Proposal

## Why

Google Cloud Vertex AI (Gemini Enterprise Agent Platform) provides an OpenAI-compatible wire protocol (`.../endpoints/openapi`), but requires Google Cloud OAuth 2.0 Bearer tokens derived from Google credentials (such as Service Account keys), which expire within 1 hour. Downstream LLM client applications and tools (e.g., Cursor, NextChat, Cherry Studio, LangChain, OpenAI SDK) generally only support static OpenAI-compatible API keys (`sk-...`) and cannot natively perform GCP OAuth signature exchanges or automatic token refreshes.

This project creates a lightweight reverse proxy bridge that accepts standard OpenAI client requests with a static API key, converts authentication into active Google Cloud OAuth tokens using a Service Account JSON file, and forwards requests to Google Cloud Vertex AI OpenAPI endpoints with streaming pass-through.

## What Changes

- Implement a lightweight FastAPI reverse proxy service with streaming pass-through (`httpx.AsyncClient`).
- Load Google Cloud Service Account JSON keys to automatically obtain, cache, and refresh GCP OAuth access tokens.
- Support YAML-based configuration (`config.example.yaml` / `config.yaml` / `config.local.yaml`), supporting multiple client API keys, proxy port, upstream location, and HTTP proxy settings.
- Ensure sensitive configuration (`config.local.yaml`) and Google Service Account credentials (`service_account.json`, `*.json`) are strictly excluded in `.gitignore`.
- Validate downstream requests against the list of authorized API keys (`proxy.api_keys`).
- Map standard OpenAI routes (`/v1/chat/completions`, `/v1/models`) to Google Cloud Vertex AI OpenAPI endpoints (`https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/endpoints/openapi/*`).
- Support automatic model name normalization (such as adding the `google/` prefix if omitted by the client).
- Provide uv-based project configuration (`pyproject.toml`) and health check / ping endpoints.

## Capabilities

### New Capabilities
- `openai-proxy`: Exposes OpenAI-compatible endpoints (`/v1/*`), validates client API keys from YAML configuration, manages Google Cloud OAuth token lifecycle via Service Account credentials, and proxies chat completions and models requests to Vertex AI with streaming support.

### Modified Capabilities
None.

## Impact

- **New Service**: Standalone proxy service runnable via `uv run`.
- **Dependencies**: Introduces `fastapi`, `uvicorn`, `httpx`, `google-auth`, `pyyaml`, and `pydantic`.
- **APIs**: Exposes `/v1/chat/completions`, `/v1/models`, `/healthz`.
- **Security & Configuration**: Reads YAML configuration with local override support (`config.local.yaml`), and prevents leaks of credentials and local configuration via `.gitignore`.
