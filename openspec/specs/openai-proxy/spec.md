# openai-proxy Specification

## Purpose

Provides an OpenAI-compatible API gateway that translates client API key authentication into Google Cloud OAuth tokens and transparently proxies requests to Vertex AI OpenAPI endpoints.

## Requirements

### Requirement: Configuration File Loading and Priority
The proxy SHALL load configuration from YAML files, prioritizing `config.local.yaml` over `config.yaml`, and default server binding to `127.0.0.1` and port `8766`.

#### Scenario: Local override file present
- **WHEN** both `config.yaml` and `config.local.yaml` exist in the root directory
- **THEN** the proxy loads settings from `config.local.yaml` with precedence

#### Scenario: Default host and port settings
- **WHEN** the server configuration omits host or port
- **THEN** the proxy defaults to binding strictly on `127.0.0.1` and listening on port `8766`

### Requirement: Startup Security Validation of API Keys
The proxy SHALL enforce strict validation of `proxy.api_keys` on application startup and refuse to start if keys are empty or match known example/placeholder values.

#### Scenario: Unreplaced placeholder key detected
- **WHEN** the application starts and `proxy.api_keys` contains known placeholder keys (such as `sk-vertex-local-test`, `sk-change-me`, `sk-example-key`)
- **THEN** the proxy aborts startup immediately with a clear security error message instructing the user to configure custom keys in `config.local.yaml`

#### Scenario: Empty key list provided
- **WHEN** the application starts and `proxy.api_keys` is empty or missing
- **THEN** the proxy aborts startup immediately with an error requiring at least one valid key

#### Scenario: Valid custom keys provided
- **WHEN** the application starts and `proxy.api_keys` contains one or more valid, non-placeholder keys
- **THEN** the proxy passes validation and continues startup normally

### Requirement: Proxy API Key Authentication
The proxy SHALL authenticate incoming client requests against the configured list of authorized API keys supplied in the HTTP Authorization header or `api-key` header.

#### Scenario: Valid API key provided
- **WHEN** a client sends a request with `Authorization: Bearer <valid-proxy-key>` matching any key in `proxy.api_keys`
- **THEN** the proxy authorizes the request and forwards it to the upstream service

#### Scenario: Missing or invalid API key provided
- **WHEN** a client sends a request with an invalid, missing, or empty authorization header
- **THEN** the proxy rejects the request immediately with HTTP 401 Unauthorized and an OpenAI-compatible error payload

### Requirement: Upstream Request Proxying and Token Injection
The proxy SHALL acquire Google Cloud OAuth 2.0 access tokens using local service account credentials, maintain token caching with automatic refresh, and proxy OpenAI chat completion requests to the Vertex AI OpenAPI endpoint.

#### Scenario: Forward chat completions with valid OAuth token
- **WHEN** an authorized client sends a POST request to `/v1/chat/completions`
- **THEN** the proxy replaces the Authorization header with a valid Google Cloud OAuth Bearer token and proxies the payload to the corresponding Vertex AI OpenAPI endpoint

#### Scenario: Automatic token refresh on expiry
- **WHEN** the cached Google Cloud access token has expired or is nearing expiration
- **THEN** the proxy refreshes the token against Google OAuth endpoints before dispatching upstream requests

### Requirement: Streaming Response Pass-Through
The proxy SHALL support Server-Sent Events (SSE) streaming for chat completions without buffering chunks.

#### Scenario: Streaming chat completions request
- **WHEN** a client requests a completion with `"stream": true`
- **THEN** the proxy streams SSE chunks back to the client in real-time with `Content-Type: text/event-stream` and disables transport buffering

### Requirement: Model Listing and Normalization
The proxy SHALL expose a `/v1/models` endpoint returning available models and normalize model names that omit the upstream vendor prefix.

#### Scenario: Query available models
- **WHEN** a client sends a GET request to `/v1/models`
- **THEN** the proxy returns a list of supported Gemini models formatted in standard OpenAI model object schema

#### Scenario: Model identifier prefix normalization
- **WHEN** a client sends a request with a model name omitting the `google/` prefix (such as `gemini-2.5-flash`)
- **THEN** the proxy normalizes the model identifier to `google/gemini-2.5-flash` before proxying upstream
