# Design

## Context

See `proposal.md` for background and motivation. The repository is greenfield and will host a lightweight reverse proxy bridge between OpenAI-compatible clients and Google Cloud Vertex AI OpenAPI endpoints (`https://aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/endpoints/openapi`).

## Goals / Non-Goals

**Goals:**
- Provide a high-performance, asynchronous proxy using FastAPI and `httpx`.
- Default server port to `8766` and host to local loopback (`127.0.0.1`).
- Load configuration from structured YAML files (`config.yaml` / `config.local.yaml`).
- Enforce strict startup security validation: immediately abort startup if `proxy.api_keys` contains example/placeholder values or is empty.
- Automatically obtain and refresh Google Cloud OAuth 2.0 Access Tokens using a Google Service Account JSON key.
- Support bidirectional HTTP streaming (Server-Sent Events) with zero buffering and proper timeout handling.
- Extract `project_id` automatically from the Service Account JSON while allowing configuration overrides.
- Provide simple model name auto-prefixing (`google/*`) for common Gemini model names.
- Guarantee that sensitive files (`config.local.yaml`, `service_account.json`) are ignored by Git.

**Non-Goals:**
- Full-featured token billing, quota, or user management system (e.g. New API / One API features).
- Protocol transformation from non-OpenAI Gemini endpoints (the proxy relies exclusively on Google's native OpenAPI compatible endpoint).
- Web frontend dashboard or database persistence.

## Decisions

### 1. Security by Default: Local Loopback (`127.0.0.1`) & Port 8766
- **Decision**: In `config.example.yaml` and default configuration values, `server.host` defaults to `127.0.0.1` and `server.port` defaults to `8766`.
- **Rationale**: Reverse proxy services that hold cloud credentials should never bind to all interfaces (`0.0.0.0`) by default. Binding to `127.0.0.1` ensures that by default only the local machine (Cursor, local apps) can communicate with the proxy. Port `8766` avoids common port collisions (such as 8000/8080/3000).

### 2. Startup Security Validation for API Keys
- **Decision**: During startup (lifespan hook), the application validates all entries in `proxy.api_keys`. If the list is empty, or if any key matches a known placeholder (such as `sk-vertex-local-test`, `sk-change-me`, `sk-example-key`), startup is intentionally halted with `RuntimeError` and a clear instructional message.
- **Rationale**: Prevents users from accidentally exposing the proxy with insecure well-known sample keys into production or internal networks.

### 3. Framework & Runtime: Python with FastAPI, PyYAML, and uv
- **Decision**: Build the application in Python 3.11+ using FastAPI, PyYAML, Pydantic, and run via `uv`.
- **Rationale**: Python provides the official `google-auth` library with battle-tested Service Account credential handling and token minting. PyYAML + Pydantic provides clean, type-safe YAML parsing. FastAPI provides async route handling and native streaming responses.

### 4. Configuration Management: YAML with Local Override
- **Decision**: Use `config.example.yaml` as the template committed to Git. The loader inspects `config.local.yaml` first, falls back to `config.yaml`, and provides safe default fallbacks.
- **Git Ignore Rule**: `.gitignore` must explicitly include:
  - `config.local.yaml`
  - `service_account.json`
  - `*.json` (as precaution against credential leak, except project manifests if any)

### 5. Google OAuth Token Lifecycle Management
- **Decision**: Manage the OAuth token in-memory using `google.oauth2.service_account.Credentials` and `asyncio.Lock`.
- **Rationale**: Google OAuth 2.0 access tokens have a 1-hour lifespan. By refreshing the token when within 5 minutes of expiration, requests avoid latency overhead on every call while avoiding token expiration errors during upstream calls.

### 6. Asynchronous Streaming Proxying with httpx
- **Decision**: Use `httpx.AsyncClient` with custom timeouts and optional `proxy` setting (from `proxy.http_proxy`), streaming responses via FastAPI's `StreamingResponse(response.aiter_raw())`.

## Risks / Trade-offs

- **[Risk]** Accidental exposure of `service_account.json` or private configuration.
  - **Mitigation**: Add `config.local.yaml`, `service_account.json`, and credentials patterns to `.gitignore` from project initialization.
- **[Risk]** Unintended network exposure or running with sample keys.
  - **Mitigation**: Default binding host to `127.0.0.1`, port `8766`, and fail fast at startup if placeholder keys are unreplaced.
- **[Risk]** Downstream client sends model name without `google/` prefix (e.g., `gemini-2.5-flash`), causing upstream Vertex AI 404 / 400.
  - **Mitigation**: If `auto_prefix_model` is enabled, automatically prepend `google/` if the model starts with `gemini-` and lacks a vendor prefix.
