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

### Requirement: Embeddings Proxying and Schema Translation
The proxy SHALL expose an OpenAI-compatible `POST /v1/embeddings` endpoint that validates authentication, accepts standard OpenAI embedding payloads, routes requests to the appropriate upstream Vertex AI protocol (either `:predict` for traditional models or `:embedContent`/`:batchEmbedContents` for Gemini multimodal models), and returns embeddings in standard OpenAI format.

#### Scenario: Single string input embedding generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": "text to embed"` and a traditional model name (e.g. `text-embedding-004`)
- **THEN** the proxy transforms the input into an instance object `{"content": "text to embed"}`, calls the Vertex AI native prediction endpoint (`:predict`), and returns an OpenAI-compatible embedding response with `data[0].embedding` and usage statistics

#### Scenario: Multiple strings batch input embedding generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": ["text 1", "text 2"]` and a traditional model name
- **THEN** the proxy transforms each item into a separate prediction instance, queries the Vertex AI prediction endpoint (`:predict`), and returns each embedding preserving its original index

#### Scenario: Optional dimensions parameter handling
- **WHEN** an authorized client provides an optional `"dimensions": 512` parameter in the request payload for traditional models
- **THEN** the proxy passes `"parameters": {"outputDimensionality": 512}` to the Vertex AI prediction endpoint

#### Scenario: Model identifier normalization for embeddings
- **WHEN** an authorized client requests an embedding with an unprefixed model identifier (such as `text-embedding-004`, `gemini-embedding-001`, or `gemini-embedding-2`)
- **THEN** the proxy correctly maps the model name to the Vertex AI publisher model resource path

#### Scenario: Empty or invalid input handling
- **WHEN** an authorized client sends an empty string or empty array in `"input"`
- **THEN** the proxy rejects the request immediately with an HTTP 400 Bad Request and an OpenAI-compatible error payload

#### Scenario: Gemini multimodal embedding single string generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": "text to embed"` and model `gemini-embedding-2` (or `google/gemini-embedding-2`)
- **THEN** the proxy translates the payload into `{"content": {"parts": [{"text": "text to embed"}]}}`, sends a POST request to the `:embedContent` endpoint, and normalizes the returned `embedding.values` into an OpenAI-compatible embedding object at index 0

#### Scenario: Gemini multimodal embedding batch strings generation
- **WHEN** an authorized client sends a POST request to `/v1/embeddings` with `"input": ["doc1", "doc2"]` and model `gemini-embedding-2`
- **THEN** the proxy translates the payload into `{"requests": [{"model": "...", "content": {"parts": [{"text": "doc1"}]}}, {"model": "...", "content": {"parts": [{"text": "doc2"}]}}]}`, sends a POST request to `:batchEmbedContents`, and normalizes the returned array of embeddings preserving original input order

#### Scenario: Gemini embedding dimension scaling
- **WHEN** an authorized client requests embeddings for `gemini-embedding-2` with `"dimensions": 768`
- **THEN** the proxy sets `outputDimensionality: 768` in the upstream Gemini embedding payload, returning vectors scaled to the requested dimension

### Requirement: Command Line Interface and Scaffolding
The proxy SHALL provide a command-line interface executable named `vap` supporting subcommands to scaffold configurations (`vap init`) and launch the proxy server (`vap run`).

#### Scenario: Initializing new configuration file with vap init
- **WHEN** user executes `vap init` in a directory where `config.local.yaml` does not exist
- **THEN** the CLI generates a new `config.local.yaml` populated from the package's default configuration template and prints setup instructions

#### Scenario: Overwrite protection on vap init
- **WHEN** user executes `vap init` in a directory where `config.local.yaml` already exists without the `--force` flag
- **THEN** the CLI terminates without modifying the existing file and outputs a warning advising to use `--force` to overwrite

#### Scenario: Force overwrite on vap init
- **WHEN** user executes `vap init --force` in a directory where `config.local.yaml` already exists
- **THEN** the CLI overwrites `config.local.yaml` with the default template and confirms successful initialization

#### Scenario: Starting server with vap run
- **WHEN** user executes `vap run` in a directory containing a valid `config.local.yaml` or `config.yaml`
- **THEN** the CLI loads the configuration, validates API keys, and starts the FastAPI server using Uvicorn

#### Scenario: Custom host and port overrides on vap run
- **WHEN** user executes `vap run --host 0.0.0.0 --port 9000`
- **THEN** the CLI overrides the configuration settings and binds the server to `0.0.0.0:9000`
