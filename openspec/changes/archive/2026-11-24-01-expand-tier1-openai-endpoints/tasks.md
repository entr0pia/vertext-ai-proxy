# Tasks

## 1. Shared Foundation & Configuration

- [x] 1.1 Add `python-multipart` runtime dependency via `uv add python-multipart` and verify `uv sync` and `uv run python -c "import multipart"` succeed.
- [x] 1.2 Extend `ProxyConfig` in `src/vertext_ai_proxy/core/config.py` (plus `config.example.yaml` and `src/vertext_ai_proxy/core/templates.py`) with `response_store_max_items` (default `500`) and `response_store_ttl_seconds` (default `3600`), and verify `tests/test_config.py` covers loading and defaults.
- [x] 1.3 Extend `VertexClient` in `src/vertext_ai_proxy/services/vertex_client.py` with `get_generate_content_url(model, stream=False, location=None)` and a shared `extract_upstream_error_message(resp)` helper, and verify unit tests cover URL building (both `global` and regional locations, prefix stripping, and `?alt=sse` streaming query) and error parsing.

## 2. Single-Model Retrieval (`GET /v1/models/{model}`)

- [x] 2.1 Implement `ModelService.get_model(model_id)` in `src/vertext_ai_proxy/services/model_service.py` supporting exact, short-ID, and `google/`-prefixed lookups against cached models (returning OpenAI 404 `model_not_found` when absent), and mount `GET /v1/models/{model:path}` in `src/vertext_ai_proxy/routers/v1/models.py` and `VertexAIProxy`.
- [x] 2.2 Add unit and route tests in `tests/test_models.py` covering unprefixed lookup, prefixed lookup (`google/...`), 404 non-existent model error format, and 401 unauthorized rejection, and verify `$env:PYTHONUTF8=1; uv run pytest tests/test_models.py -v` passes.

## 3. OpenAI Responses API (`/v1/responses`)

- [x] 3.1 Create Pydantic schemas in `src/vertext_ai_proxy/schemas/responses.py` for Responses creation requests (`model`, `input`, `instructions`, `previous_response_id`, `store`, `stream`, `tools`, `tool_choice`, `temperature`, `top_p`, `max_output_tokens`, `reasoning`, `text`), response objects, and delete responses.
- [x] 3.2 Implement bounded LRU+TTL in-memory `ResponseStore` in `src/vertext_ai_proxy/services/response_store.py` tracking stored response records, accumulated Gemini `contents` chains, and active cancellation handles, and verify unit tests confirm TTL expiration, LRU eviction, `get`/`delete`, and active cancel signaling.
- [x] 3.3 Implement `ResponseService` in `src/vertext_ai_proxy/services/response_service.py` translating Responses requests (text, multimodal image inputs, system instructions, function tools, `function_call_output`, reasoning effort, structured `text.format`) to Vertex AI `:generateContent` and `:streamGenerateContent?alt=sse`, emitting OpenAI Responses SSE semantic events (`response.created` through `response.completed`) and supporting `previous_response_id`, `store=false`, `GET`, `DELETE`, and `POST .../cancel`.
- [x] 3.4 Add `src/vertext_ai_proxy/routers/v1/responses.py` (`POST /v1/responses`, `GET /v1/responses/{response_id}`, `DELETE /v1/responses/{response_id}`, `POST /v1/responses/{response_id}/cancel`), wire `response_service` into `proxy.py` and `app.py`, and verify with comprehensive mocked tests in `tests/test_responses.py` (`$env:PYTHONUTF8=1; uv run pytest tests/test_responses.py -v`).
- [x] 3.5 Document `/v1/responses` usage, in-memory state semantics, and explicit Google model requirement in `README.md` and `AGENTS.md`.

## 4. Images API (`/v1/images/generations`, `/v1/images/edits`)

- [x] 4.1 Create Pydantic schemas in `src/vertext_ai_proxy/schemas/images.py` for image generation requests/responses (supporting upstream-driven `b64_json` / `url` output without format blocking).
- [x] 4.2 Implement `ImageService` in `src/vertext_ai_proxy/services/image_service.py` routing `imagen-*` models to Vertex AI `:predict` (generations with `sampleCount`/`aspectRatio` and edits with `referenceImages` + `maskImageConfig`) and `gemini-*` models to `:generateContent` (`responseModalities: ["IMAGE", "TEXT"]`) without OpenAI model alias rewriting or format interception.
- [x] 4.3 Add `src/vertext_ai_proxy/routers/v1/images.py` (`POST /v1/images/generations` JSON and `POST /v1/images/edits` `multipart/form-data`), wire `image_service` into `proxy.py` and `app.py`, and verify with mocked tests in `tests/test_images.py` covering Imagen generation/edit, Gemini image generation/edit, non-blocking `response_format`, and explicit model passthrough (`$env:PYTHONUTF8=1; uv run pytest tests/test_images.py -v`).
- [x] 4.4 Document `/v1/images/generations` and `/v1/images/edits` usage and supported Google image models in `README.md` and `AGENTS.md`.

## 5. Audio API (`/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/audio/translations`)

- [x] 5.1 Create Pydantic schemas in `src/vertext_ai_proxy/schemas/audio.py` for TTS speech requests (without format interception) and STT transcription/translation responses (`json`, `verbose_json`).
- [x] 5.2 Implement `AudioService` in `src/vertext_ai_proxy/services/audio_service.py`:
  - `create_speech`: invoke Vertex AI Gemini TTS `:generateContent` (`responseModalities: ["AUDIO"]`, `speechConfig.voiceConfig.prebuiltVoiceConfig`) with OpenAI-to-Gemini voice mapping and upstream-driven audio passthrough (plus stdlib `wave` PCM↔WAV conversion when explicitly requesting `wav`/`pcm`, no format rejection, no `ffmpeg`).
  - `create_transcription` & `create_translation`: encode uploaded audio bytes into Gemini `:generateContent` `inlineData` with MIME inference and format output as `json`, `text`, `verbose_json`, `srt`, `vtt`, or raw model text without format blocking.
- [x] 5.3 Add `src/vertext_ai_proxy/routers/v1/audio.py` (`POST /v1/audio/speech`, `POST /v1/audio/transcriptions`, `POST /v1/audio/translations`), wire `audio_service` into `proxy.py` and `app.py`, and verify with mocked tests in `tests/test_audio.py` covering upstream audio passthrough (including non-wav `response_format`), explicit WAV wrapping / PCM extraction, transcription/translation formats, empty upload 400 rejection, and no model alias rewriting (`$env:PYTHONUTF8=1; uv run pytest tests/test_audio.py -v`).
- [x] 5.4 Document `/v1/audio/speech`, `/v1/audio/transcriptions`, and `/v1/audio/translations` (including upstream-driven audio output and explicit Google model names) in `README.md` and `AGENTS.md`.

## 6. System Verification & Live E2E Testing

- [x] 6.1 Run the full automated test suite (`$env:PYTHONUTF8=1; uv run pytest -v`) and verify all existing and newly added tests pass with zero failures.
- [x] 6.2 Validate OpenSpec compliance (`openspec validate "expand-tier1-openai-endpoints" --strict` and `openspec validate --specs`) and confirm zero errors or warnings.
- [x] 6.3 Launch the proxy server locally with `config.local.yaml` (`http://127.0.0.1:8766`) and run a live E2E verification script against real Vertex AI upstream endpoints covering `/healthz`, `GET /v1/models/{model}`, `POST /v1/responses` (non-streaming, streaming SSE, `previous_response_id`, `GET`, `DELETE`), `POST /v1/images/generations` & `edits`, `POST /v1/audio/speech` (`wav` & `pcm`), and `POST /v1/audio/transcriptions` & `translations`.

## Workflow follow-up

- Archive the change under `openspec/changes/archive/YYYY-MM-DD-NN-expand-tier1-openai-endpoints` after implementation and live E2E verification succeed.
- Confirm git changes with the user before running any `git add`, `git commit`, or `git push`.
