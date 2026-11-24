# Proposal

## Why

Google Cloud Vertex AI's managed OpenAI-compatible gateway (`.../endpoints/openapi`) natively exposes only `POST /v1/chat/completions`, leaving modern OpenAI SDK clients and multimodal applications unable to call single-model inspection, the newer Responses API, image generation/editing, or audio speech/transcription endpoints through `Vertext-AI-Proxy`. Expanding the proxy with lightweight, protocol-translated implementations for these Tier-1 OpenAI REST endpoints unlocks broad ecosystem compatibility while preserving the project's zero-external-storage, Service-Account-only architecture.

## What Changes

- **Single-Model Retrieval (`GET /v1/models/{model:path}`)**: Extend model listing to look up individual models (supporting both unprefixed `gemini-3.8-flash` and prefixed `google/gemini-3.8-flash` identifiers) against the existing cached publisher model inventory and return OpenAI `404` when absent.
- **OpenAI Responses API (`/v1/responses`) with Bounded In-Memory State**:
  - Add `POST /v1/responses` translating OpenAI Responses requests (string/array inputs, system `instructions`, multimodal image inputs, function tools & `function_call_output`, reasoning effort, structured `text.format`, non-streaming and SSE streaming semantic events) to Vertex AI native `:generateContent` / `:streamGenerateContent`.
  - Support multi-turn continuation via `previous_response_id` backed by a bounded, TTL-evicted in-memory response store (no database or external storage dependency; cleared on restart).
  - Add `GET /v1/responses/{response_id}`, `DELETE /v1/responses/{response_id}`, and `POST /v1/responses/{response_id}/cancel` for response retrieval, deletion, and active-generation cancellation.
- **Image Generation & Editing (`/v1/images/generations`, `/v1/images/edits`)**:
  - Add `POST /v1/images/generations` (JSON) and `POST /v1/images/edits` (`multipart/form-data`) returning OpenAI-formatted `b64_json` image payloads.
  - Route automatically by explicit Google model identifier: `imagen-*` models to Vertex AI `:predict` (including `referenceImages` + `maskImageConfig` for edits) and Gemini image models (`gemini-*`) to `:generateContent` with `responseModalities: ["IMAGE"]`.
- **Audio Speech & Transcription (`/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/audio/translations`)**:
  - Add `POST /v1/audio/transcriptions` and `POST /v1/audio/translations` (`multipart/form-data`) translating uploaded audio bytes into Gemini multimodal `:generateContent` requests and returning OpenAI-compatible formats (`json`, `text`, `verbose_json`, `srt`, `vtt`, or raw model text without format blocking).
  - Add `POST /v1/audio/speech` (JSON) translating synthesis requests to Vertex AI Gemini TTS `:generateContent` (`responseModalities: ["AUDIO"]`) without rejecting client `response_format` values; returns whatever audio bytes and MIME type the upstream model produces (with stdlib `wave` PCM↔WAV conversion when explicitly requesting `wav` or `pcm`, and zero `ffmpeg` dependency).
- **Explicit Google Model Identifiers Only**: No implicit OpenAI model alias mapping (such as `whisper-1`, `tts-1`, `dall-e-3`, or `gpt-image-1`). Clients must pass explicit Google model identifiers (with existing optional `google/` prefix normalization preserved).
- **Multipart Upload Dependency**: Add `python-multipart` to `pyproject.toml` so FastAPI can parse `multipart/form-data` payloads for audio and image-edit routes.

## Capabilities

### New Capabilities

<!-- None: All proxy endpoint behaviors are consolidated under the canonical `openai-proxy` specification per project convention. -->

### Modified Capabilities

- `openai-proxy`: Extend model listing with single-model retrieval (`GET /v1/models/{model:path}`), and add requirements for Responses API proxying & bounded in-memory state management, Images generation/editing translation, and Audio speech/transcription/translation adaptation.

## Impact

- **Dependencies**: Add `python-multipart` to `pyproject.toml` (managed via `uv`).
- **Core / Transport**: Extend `src/vertext_ai_proxy/services/vertex_client.py` with native `:generateContent` and `:streamGenerateContent` URL builders and shared upstream error handling helpers; add optional in-memory response cache sizing/TTL settings to `src/vertext_ai_proxy/core/config.py`.
- **Schemas**: Add `schemas/responses.py`, `schemas/images.py`, and `schemas/audio.py`.
- **Services**: Update `services/model_service.py`; add `services/response_store.py`, `services/response_service.py`, `services/image_service.py`, and `services/audio_service.py`.
- **Routers & Facade**: Update `routers/v1/models.py`, `proxy.py`, and `app.py`; add `routers/v1/responses.py`, `routers/v1/images.py`, and `routers/v1/audio.py`.
- **Tests & Docs**: Add unit/integration test suites (`tests/test_responses.py`, `tests/test_images.py`, `tests/test_audio.py`, plus updates to `tests/test_models.py` / `tests/test_proxy.py`), run live Vertex AI E2E verification, and update `README.md` and `AGENTS.md`.
