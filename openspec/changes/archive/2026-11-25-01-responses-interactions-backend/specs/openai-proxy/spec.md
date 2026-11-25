# Spec Delta

## ADDED Requirements

### Requirement: Configurable Responses Backend Routing with Vertex AI Interactions API
The proxy SHALL support a `proxy.responses_backend` configuration setting accepting `"auto"` (default), `"interactions"`, or `"local"` to control whether `/v1/responses` routes to Vertex AI's native Interactions API (`/v1beta1/projects/{project}/locations/{location}/interactions`) or the local `:generateContent` + in-memory store backend.

#### Scenario: Interactions backend creates and chains server-side state
- **WHEN** `proxy.responses_backend` is `"interactions"` or `"auto"` (with valid `aiplatform.interactions.*` permissions) and an authorized client sends `POST /v1/responses`, followed by a second turn passing `previous_response_id` set to the returned response ID
- **THEN** the proxy forwards the first turn to Vertex AI `POST /interactions` and returns the upstream Interaction ID as the OpenAI response `id`, and on the second turn forwards only the incremental `input` along with `previous_interaction_id` so Vertex AI restores prior context server-side without the proxy resending earlier turn history

#### Scenario: Interactions backend streams SSE step events to OpenAI Responses SSE format
- **WHEN** `proxy.responses_backend` routes a `POST /v1/responses` request with `"stream": true` to Vertex AI Interactions API
- **THEN** the proxy sends `"stream": true` with `Accept: text/event-stream` to Vertex AI `POST /interactions` and translates upstream `interaction.created`, `step.start`, `step.delta`, `step.stop`, and `interaction.completed` events into OpenAI Responses SSE events (`response.created`, `response.in_progress`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.content_part.done`, `response.output_item.done`, `response.completed`)

#### Scenario: Interactions backend delegates retrieve, delete, and cancel operations upstream
- **WHEN** an authorized client calls `GET /v1/responses/{response_id}`, `DELETE /v1/responses/{response_id}`, or `POST /v1/responses/{response_id}/cancel` for an upstream Interactions ID
- **THEN** the proxy forwards the corresponding `GET /interactions/{id}`, `DELETE /interactions/{id}`, or `POST /interactions/{id}/cancel` call to Vertex AI and translates the result into the standard OpenAI Responses JSON format (or OpenAI 404 when not found)

#### Scenario: Auto mode falls back to local backend on HTTP 403 permission denied
- **WHEN** `proxy.responses_backend` is `"auto"` and Vertex AI `POST /interactions` responds with HTTP 403 (`PERMISSION_DENIED`)
- **THEN** the proxy logs a warning and transparently retries the request using the local `:generateContent` + in-memory store backend, returning HTTP 200 to the client without failing the request

#### Scenario: Auto mode routes local response IDs to local backend
- **WHEN** `proxy.responses_backend` is `"auto"` and an authorized client references a locally stored `resp_*` identifier via `previous_response_id`, `GET`, `DELETE`, or `cancel`
- **THEN** the proxy routes that request directly to the local in-memory store and `:generateContent` backend rather than sending a local `resp_*` ID to Vertex AI `/interactions`

#### Scenario: Explicit interactions mode surfaces upstream 403 error without silent fallback
- **WHEN** `proxy.responses_backend` is explicitly set to `"interactions"` and Vertex AI returns HTTP 403 (`PERMISSION_DENIED`)
- **THEN** the proxy does not silently fall back to the local backend and returns HTTP 403 with an OpenAI-compatible error payload

#### Scenario: Explicit local mode bypasses Interactions API
- **WHEN** `proxy.responses_backend` is set to `"local"`
- **THEN** the proxy handles all `/v1/responses` operations exclusively via Vertex AI `:generateContent` / `:streamGenerateContent` and the bounded in-memory store without calling `/interactions`

## MODIFIED Requirements

### Requirement: Responses API Creation and Streaming Translation
The proxy SHALL expose `POST /v1/responses` to translate OpenAI Responses API requests (including text/multimodal inputs, instructions, function tools, reasoning configuration, and structured output format) into either Vertex AI native Interactions API (`/interactions`) or Vertex AI `:generateContent` / `:streamGenerateContent` calls according to `proxy.responses_backend`, returning OpenAI Responses objects or SSE event streams.

#### Scenario: Non-streaming text response creation
- **WHEN** an authorized client sends `POST /v1/responses` with a valid Google model and string or structured array `input` with `stream` omitted or `false`
- **THEN** the proxy invokes the active Vertex AI backend and returns HTTP 200 with an OpenAI `response` object containing a response identifier (upstream Interaction ID in `interactions` mode, or generated `resp_*` identifier in `local` mode), `status: "completed"`, `output` message items, `output_text`, and normalized `usage` counts

#### Scenario: Streaming response creation via SSE
- **WHEN** an authorized client sends `POST /v1/responses` with `"stream": true`
- **THEN** the proxy invokes the active Vertex AI streaming backend and streams OpenAI Responses SSE semantic events (`response.created`, `response.in_progress`, `response.output_item.added`, `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.content_part.done`, `response.output_item.done`, `response.completed`) with `Content-Type: text/event-stream`

#### Scenario: Function tool calling and tool output continuation
- **WHEN** an authorized client provides `tools` of type `function` or submits `function_call_output` input items in `POST /v1/responses`
- **THEN** the proxy translates function declarations and outputs to the active Vertex AI backend format (`tools` + `function_result` for Interactions API, or `functionDeclarations` + `functionResponse` for `:generateContent`), returning `function_call` output items when the model invokes a tool

#### Scenario: Invalid or empty Responses input handling
- **WHEN** an authorized client sends `POST /v1/responses` with missing `model` or empty/invalid `input`
- **THEN** the proxy rejects the request with HTTP 400 Bad Request and an OpenAI-compatible error payload
