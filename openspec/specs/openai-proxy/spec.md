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
The proxy SHALL expose a `/v1/models` endpoint returning available models, a `/v1/models/{model}` endpoint returning individual model details, and normalize model names that omit the upstream vendor prefix.

#### Scenario: Query available models
- **WHEN** a client sends a GET request to `/v1/models`
- **THEN** the proxy returns a list of supported Gemini models formatted in standard OpenAI model object schema

#### Scenario: Query a single model by identifier
- **WHEN** an authorized client sends a GET request to `/v1/models/{model}` using either an unprefixed identifier (such as `gemini-3.8-flash`) or a vendor-prefixed identifier (such as `google/gemini-3.8-flash`) that exists in the available model catalog
- **THEN** the proxy returns HTTP 200 with a single standard OpenAI `model` object matching the requested model

#### Scenario: Query a non-existent model
- **WHEN** an authorized client sends a GET request to `/v1/models/{model}` for a model identifier not present in the available model catalog
- **THEN** the proxy returns HTTP 404 Not Found with an OpenAI-compatible error payload (`code: "model_not_found"`)

#### Scenario: Model identifier prefix normalization
- **WHEN** a client sends a request with a model name omitting the `google/` prefix (such as `gemini-3.8-flash`)
- **THEN** the proxy normalizes the model identifier to `google/gemini-3.8-flash` before proxying upstream

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

### Requirement: Responses API Creation and Streaming Translation
The proxy SHALL expose `POST /v1/responses` to translate OpenAI Responses API requests (including text/multimodal inputs, instructions, function tools, reasoning configuration, and structured output format) into Vertex AI native `:generateContent` or `:streamGenerateContent` calls and return OpenAI Responses objects or SSE event streams.

#### Scenario: Non-streaming text response creation
- **WHEN** an authorized client sends `POST /v1/responses` with a valid Google model and string or structured array `input` with `stream` omitted or `false`
- **THEN** the proxy invokes Vertex AI `:generateContent` and returns HTTP 200 with an OpenAI `response` object containing a generated `resp_*` identifier, `status: "completed"`, `output` message items, `output_text`, and normalized `usage` counts

#### Scenario: Streaming response creation via SSE
- **WHEN** an authorized client sends `POST /v1/responses` with `"stream": true`
- **THEN** the proxy invokes Vertex AI `:streamGenerateContent` and streams OpenAI Responses SSE semantic events (`response.created`, `response.in_progress`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.content_part.done`, `response.output_item.done`, `response.completed`) with `Content-Type: text/event-stream`

#### Scenario: Function tool calling and tool output continuation
- **WHEN** an authorized client provides `tools` of type `function` or submits `function_call_output` input items in `POST /v1/responses`
- **THEN** the proxy translates function declarations and outputs to Vertex AI function declarations and function responses, returning `function_call` output items when the model invokes a tool

#### Scenario: Invalid or empty Responses input handling
- **WHEN** an authorized client sends `POST /v1/responses` with missing `model` or empty/invalid `input`
- **THEN** the proxy rejects the request with HTTP 400 Bad Request and an OpenAI-compatible error payload

### Requirement: Responses API Bounded In-Memory State Management
The proxy SHALL maintain a bounded, TTL-evicted in-memory store for Responses API records (when `store` is not explicitly `false`) to support `previous_response_id` multi-turn continuation, `GET /v1/responses/{response_id}`, `DELETE /v1/responses/{response_id}`, and `POST /v1/responses/{response_id}/cancel` without external database dependencies.

#### Scenario: Multi-turn continuation with previous_response_id
- **WHEN** an authorized client sends `POST /v1/responses` referencing an existing `previous_response_id` stored in memory
- **THEN** the proxy reconstructs the prior conversation history from the in-memory store, appends the new turn input, and dispatches the combined context to Vertex AI

#### Scenario: Unknown or expired previous_response_id
- **WHEN** an authorized client sends `POST /v1/responses` with a `previous_response_id` that does not exist or has been evicted from memory
- **THEN** the proxy rejects the request with HTTP 404 Not Found and an OpenAI-compatible error payload

#### Scenario: Retrieve and delete stored response
- **WHEN** an authorized client calls `GET /v1/responses/{response_id}` or `DELETE /v1/responses/{response_id}` for an existing response
- **THEN** `GET` returns the stored OpenAI `response` object and `DELETE` removes it from memory and returns `{"id": "<response_id>", "object": "response.deleted", "deleted": true}`

#### Scenario: Cancel an in-flight response
- **WHEN** an authorized client calls `POST /v1/responses/{response_id}/cancel` while generation is in progress
- **THEN** the proxy signals cancellation of the active upstream task and returns the response object with `status: "cancelled"`

#### Scenario: Opt out of response storage
- **WHEN** an authorized client sends `POST /v1/responses` with `"store": false`
- **THEN** the proxy returns the completed response without persisting it in the in-memory store, and subsequent `GET /v1/responses/{response_id}` returns HTTP 404

### Requirement: Image Generation and Editing Translation
The proxy SHALL expose `POST /v1/images/generations` and `POST /v1/images/edits`, routing `imagen-*` models to Vertex AI `:predict` and `gemini-*` image models to Vertex AI `:generateContent`, and returning OpenAI-formatted `b64_json` image responses.

#### Scenario: Image generation with Imagen model
- **WHEN** an authorized client sends `POST /v1/images/generations` with an `imagen-*` model and a non-empty `prompt`
- **THEN** the proxy translates parameters such as `n` and `size` into a Vertex AI `:predict` payload and returns HTTP 200 with `{"created": <ts>, "data": [{"b64_json": "..."}]}`

#### Scenario: Image generation with Gemini image model
- **WHEN** an authorized client sends `POST /v1/images/generations` with a `gemini-*` image model and a non-empty `prompt`
- **THEN** the proxy invokes Vertex AI `:generateContent` requesting image output modality and returns extracted images in OpenAI `b64_json` format

#### Scenario: Image editing via multipart upload
- **WHEN** an authorized client sends `POST /v1/images/edits` with `multipart/form-data` containing `image`, `prompt`, `model`, and an optional `mask`
- **THEN** the proxy encodes the uploaded binary image(s) and routes `imagen-*` requests to `:predict` with reference/mask images or `gemini-*` requests to `:generateContent` multimodal parts, returning edited images based on what the upstream model produces

#### Scenario: No client response format blocking for images
- **WHEN** an authorized client supplies any `response_format` value (such as `url` or `b64_json`) on `/v1/images/generations` or `/v1/images/edits`
- **THEN** the proxy does not reject the request based on `response_format` and returns the image data produced by the upstream model

### Requirement: Audio Speech Synthesis Adaptation
The proxy SHALL expose `POST /v1/audio/speech` translating OpenAI TTS requests into Vertex AI Gemini `:generateContent` audio synthesis calls without rejecting client `response_format` values, returning the audio bytes and MIME type produced by the upstream model (with stdlib WAV/PCM conversion when explicitly requested).

#### Scenario: Pass through upstream audio by default or for any requested format
- **WHEN** an authorized client sends `POST /v1/audio/speech` with a Google TTS model, non-empty `input`, `voice`, and either omits `response_format` or passes any format value (such as `"mp3"`, `"opus"`, `"aac"`, `"flac"`)
- **THEN** the proxy does not block the request on `response_format`, calls Vertex AI `:generateContent` with audio modality, and returns HTTP 200 with the audio bytes and `Content-Type` returned by the upstream model

#### Scenario: Explicit WAV or PCM conversion using standard library
- **WHEN** an authorized client sends `POST /v1/audio/speech` explicitly requesting `"response_format": "wav"` (while upstream returns raw `audio/L16` PCM) or `"response_format": "pcm"` (while upstream returns `audio/wav`)
- **THEN** the proxy converts between raw PCM and RIFF/WAV using Python's standard library without invoking external transcoding binaries and returns the corresponding `audio/wav` or `audio/pcm` payload

### Requirement: Audio Transcription and Translation Adaptation
The proxy SHALL expose `POST /v1/audio/transcriptions` and `POST /v1/audio/translations` accepting `multipart/form-data` audio uploads, forwarding audio bytes to Vertex AI Gemini `:generateContent`, and returning OpenAI-compatible transcript payloads.

#### Scenario: Transcribe uploaded audio to JSON or plain text
- **WHEN** an authorized client sends `POST /v1/audio/transcriptions` with an audio `file`, a Google model, and optional `language`, `prompt`, and `response_format` (`json`, `text`, `verbose_json`, `srt`, `vtt`)
- **THEN** the proxy encodes the audio as inline multimodal data for Gemini `:generateContent` and returns the transcription in the requested OpenAI response format

#### Scenario: Translate foreign audio into English text
- **WHEN** an authorized client sends `POST /v1/audio/translations` with an audio `file` and a Google model
- **THEN** the proxy instructs Gemini `:generateContent` to translate the spoken audio content directly into English and returns the result in the requested OpenAI response format

#### Scenario: Empty or missing audio file rejected
- **WHEN** an authorized client sends an empty file to `/v1/audio/transcriptions` or `/v1/audio/translations`
- **THEN** the proxy rejects the request with HTTP 400 Bad Request and an OpenAI-compatible error payload

### Requirement: Explicit Google Model Identifiers Without Alias Mapping
The proxy SHALL NOT implicitly remap OpenAI proprietary model aliases (such as `whisper-1`, `tts-1`, `dall-e-3`, or `gpt-image-1`) to Google models; clients MUST supply explicit Google model identifiers across all new endpoints.

#### Scenario: Explicit Google model name passed directly
- **WHEN** an authorized client calls `/v1/responses`, `/v1/images/*`, or `/v1/audio/*` with an explicit Google model identifier (with or without `google/` prefix)
- **THEN** the proxy targets that exact Google model resource on Vertex AI without substituting an unrelated default model

#### Scenario: OpenAI proprietary alias not rewritten
- **WHEN** an authorized client supplies an OpenAI proprietary model name such as `whisper-1`, `tts-1`, or `dall-e-3` to `/v1/images/*` or `/v1/audio/*`
- **THEN** the proxy does not silently rewrite the model name to a hardcoded Google model alias
