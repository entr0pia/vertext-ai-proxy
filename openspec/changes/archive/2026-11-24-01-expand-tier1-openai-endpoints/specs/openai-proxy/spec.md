# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

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
