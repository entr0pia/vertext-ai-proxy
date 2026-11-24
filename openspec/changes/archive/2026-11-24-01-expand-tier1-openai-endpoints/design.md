# Design

## Context

`Vertext-AI-Proxy` currently exposes `/healthz`, `GET /v1/models`, `POST /v1/chat/completions`, and `POST /v1/embeddings` via a clean layered FastAPI architecture (`routers/v1/` → `services/` → `VertexClient` → `GCPTokenManager`). See `proposal.md` for motivation and scope.

Because Vertex AI's managed OpenAI compatibility layer (`.../endpoints/openapi`) natively serves only `chat/completions`, all newly added Tier-1 OpenAI endpoints (except `GET /v1/models/{model}`, which reads cached publisher metadata) must translate OpenAI wire contracts into native Vertex AI REST endpoints (`:generateContent`, `:streamGenerateContent`, and `:predict`). The project runs on Windows/Linux/macOS with Python 3.11+ managed by `uv`, and must remain lightweight with zero external database or media binary (`ffmpeg`) requirements.

## Goals / Non-Goals

**Goals:**
- Provide modular, independently testable domain services for Models lookup, Responses API (with bounded in-memory state), Images API (`generations` & `edits`), and Audio API (`speech`, `transcriptions`, `translations`).
- Reuse existing `APIKeyValidator`, `GCPTokenManager`, `VertexClient`, and `OpenAIAPIException` conventions uniformly across all new routes.
- Preserve exact OpenAI error shapes (`{"error": {"message", "type", "param", "code"}}`) and SSE streaming guarantees (`Accept-Encoding: identity`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`).
- Support real-world OpenAI Python/JS SDK clients passing explicit Google model identifiers.

**Non-Goals:**
- No persistent storage (SQLite/Redis/GCS) for Responses state or generated image URLs (`url` response format is out of scope).
- No external audio transcoding (`ffmpeg` / `pydub` / `av`); TTS natively emits `wav` or `pcm` via Python stdlib `wave`.
- No implicit OpenAI model alias rewriting (`whisper-1`, `tts-1`, `dall-e-3`, `gpt-image-1`).
- No Tier-2/Tier-3 endpoints (`WSS /v1/realtime`, `/v1/files`, `/v1/vector_stores`, `/v1/batches`, `/v1/fine_tuning/jobs`, `/v1/evals`).

## Decisions

### D1: Shared Transport Extensions in `VertexClient`
Extend `src/vertext_ai_proxy/services/vertex_client.py` with native Gemini content URL builders:
- `get_generate_content_url(model: str, stream: bool = False, location: str | None = None) -> str`:
  - Strips optional `google/` vendor prefix.
  - Builds `https://{domain}/v1/projects/{project_id}/locations/{loc}/publishers/google/models/{clean_model}:generateContent` (or `:streamGenerateContent?alt=sse` when `stream=True`).
- Extract a shared helper `extract_upstream_error_message(resp: httpx.Response) -> str` so `EmbeddingService`, `ResponseService`, `ImageService`, and `AudioService` consistently unwrap Google Cloud JSON error payloads into `OpenAIAPIException`.
- **Alternative considered**: Calling `google-genai` SDK directly. Rejected because `Vertext-AI-Proxy` already maintains async `httpx.AsyncClient` connection pooling, `http_proxy` routing, custom timeout control, and `GCPTokenManager` OAuth caching, and mocking `httpx.AsyncClient` keeps unit tests fast and uniform.

### D2: Single-Model Inspection (`GET /v1/models/{model:path}`)
- Register `@router.get("/models/{model:path}")` in `routers/v1/models.py`. Using Starlette's `:path` converter is essential because Vertex AI model identifiers frequently contain a slash (e.g., `google/gemini-3.8-flash`).
- `ModelService.get_model(model_id: str)` fetches the cached list from `get_models()` (populating the 1-hour cache if cold) and searches by exact match, stripped short ID, or `google/`-prefixed canonical ID. Returns HTTP `404` (`code="model_not_found"`, `type="invalid_request_error"`) when not found.

### D3: Responses API Architecture (`ResponseService` + `ResponseStore`)
- **Upstream target**: Vertex AI native `:generateContent` / `:streamGenerateContent?alt=sse` rather than Vertex's `/endpoints/openapi/chat/completions`.
  - *Rationale*: Native Gemini REST directly supports multimodal parts, `systemInstruction`, `tools.functionDeclarations`, `functionResponse`, `generationConfig.thinkingConfig`, and `generationConfig.responseMimeType`/`responseSchema` without lossy double translation.
- **Request translation (`POST /v1/responses`)**:
  - `instructions` → `systemInstruction.parts = [{"text": instructions}]`.
  - `input`:
    - Plain `str` → single `user` content with `text` part.
    - `list` of items:
      - Role messages (`user`, `assistant`, `system`, `developer`) with string or content-part arrays (`input_text`, `output_text`, `input_image` via `image_url` data URI / URL, `input_file` base64 data) → Gemini `contents` (`role`: `user` / `model`; `system`/`developer` merged into `systemInstruction`).
      - `function_call` items → `model` turn with `functionCall` part.
      - `function_call_output` items → `user` turn with `functionResponse` part (resolving function `name` from prior stored or inline `function_call` by `call_id`).
  - `tools`: OpenAI `{"type": "function", "name": ..., "description": ..., "parameters": ...}` → Gemini `tools: [{"functionDeclarations": [...]}]`.
  - `tool_choice`: `"auto"` / `"none"` / `"required"` / `{"type": "function", "name": ...}` → `toolConfig.functionCallingConfig`.
  - `max_output_tokens` → `generationConfig.maxOutputTokens`; `temperature` / `top_p` → `generationConfig`.
  - `reasoning.effort` (`low` / `medium` / `high`) → `generationConfig.thinkingConfig` (mapped cleanly when supplied).
  - `text.format`: `json_object` → `responseMimeType: "application/json"`; `json_schema` → `responseMimeType: "application/json"` + `responseSchema`.
- **Bounded In-Memory State (`ResponseStore`)**:
  - Backed by `collections.OrderedDict[str, StoredResponseRecord]` storing:
    - `response`: full OpenAI `Response` dict (`id="resp_<uuid4hex>"`, `object="response"`, `created_at`, `status`, `model`, `output`, `output_text`, `usage`, `previous_response_id`, etc.)
    - `gemini_contents`: full accumulated Gemini `contents` list up to and including this turn's model output, enabling O(1) multi-turn reconstruction when a subsequent request passes `previous_response_id`.
    - `expires_at`: `time.monotonic() + ttl_seconds`.
  - Bounded by `proxy.response_store_max_items` (default `500`) and `proxy.response_store_ttl_seconds` (default `3600`) in `ProxyConfig`. Evicts expired entries lazily and evicts LRU oldest entry when capacity is exceeded.
  - Active in-flight generations register a cancellation handle (`asyncio.Event` + cancel callback) in `ResponseStore` so `POST /v1/responses/{response_id}/cancel` cancels the ongoing upstream request/stream, marks the stored record `status: "cancelled"`, and returns it.
  - When request sets `store=false`, skip persisting the finished record (still track transient cancel handle during execution, then remove on completion).
- **Streaming SSE Protocol (`stream=true`)**:
  - Consume Vertex AI `:streamGenerateContent?alt=sse` chunks with `Accept-Encoding: identity` and `aiter_lines()`.
  - Emit standard OpenAI Responses SSE events (`event: <name>\ndata: <json>\n\n`) with monotonic `sequence_number`:
    1. `response.created` (`status: "in_progress"`)
    2. `response.in_progress`
    3. For text output: `response.output_item.added` (`type: "message"`), `response.content_part.added` (`type: "output_text"`), incremental `response.output_text.delta` chunks, then `response.output_text.done`, `response.content_part.done`, `response.output_item.done`.
    4. For tool calls: `response.output_item.added` (`type: "function_call"`), `response.function_call_arguments.delta`, `response.function_call_arguments.done`, `response.output_item.done`.
    5. Terminal `response.completed` (or `response.failed` / `response.incomplete` on error/cancellation), persisting the completed record to `ResponseStore` if `store` is true.

### D4: Images API Dual-Protocol Routing (`ImageService`)
- Supported endpoints:
  - `POST /v1/images/generations` (JSON body via `ImageGenerationRequest`).
  - `POST /v1/images/edits` (`multipart/form-data` accepting `image` (single or multiple `UploadFile`), optional `mask` (`UploadFile`), `prompt`, `model`, optional `n`, `size`, `response_format`).
- Validation:
  - Require non-empty `prompt` and non-empty `model`.
  - Do NOT block on `response_format`; always dispatch to the upstream model and return whatever payload format the model returns (`b64_json` or `url` if present).
- Routing by stripped lowercase model name (`clean_model = model.split("/")[-1].lower()`):
  - **Branch A (`clean_model.startswith("imagen-")`) → Vertex AI `:predict`**:
    - Map OpenAI `size` (`1024x1024` → `1:1`, `1536x1024`/`1792x1024` → `16:9`, `1024x1536`/`1024x1792` → `9:16`, `1024x768` → `4:3`, `768x1024` → `3:4`; also accept direct aspect ratios like `16:9`).
    - Generations payload: `{"instances": [{"prompt": prompt}], "parameters": {"sampleCount": n, "aspectRatio": aspect_ratio}}`.
    - Edits payload: `{"instances": [{"prompt": prompt, "referenceImages": [RAW, optional MASK]}], "parameters": {"editMode": "EDIT_MODE_INPAINT_INSERTION", "sampleCount": n}}` (with `MASK_MODE_USER_PROVIDED` when `mask` is uploaded, or `MASK_MODE_BACKGROUND` / maskless editing parameters when no mask is provided).
    - Parse `predictions[*].bytesBase64Encoded` → OpenAI `data: [{"b64_json": ...}]`.
  - **Branch B (all other Google models, e.g., `gemini-nano-banana-2.1`, `gemini-3-pro-image`, `gemini-3.1-flash-lite-image`, `gemini-*-image*`) → Vertex AI `:generateContent`**:
    - Generations payload: `contents: [{"role": "user", "parts": [{"text": prompt}]}]`, `generationConfig: {"responseModalities": ["IMAGE", "TEXT"], "candidateCount": n}` (plus `imageConfig.aspectRatio` if `size` maps to an aspect ratio).
    - Edits payload: `contents: [{"role": "user", "parts": [{"text": prompt}, *image_inline_data_parts, optional_mask_part]}]`, `generationConfig: {"responseModalities": ["IMAGE", "TEXT"]}`.
    - Extract all `inlineData` parts with `image/*` MIME types across candidates into OpenAI `data: [{"b64_json": ...}]`.

### D5: Audio API Adaptation (`AudioService`)
- **TTS (`POST /v1/audio/speech`)**:
  - Request schema: `model` (required explicit Google model, e.g., `gemini-3.8-flash-tts`), `input` (required non-empty text), `voice` (default `"Kore"` or mapped OpenAI voice), `response_format` (optional, never rejected), `speed` (optional float), `instructions` (optional style guidance prepended to prompt text).
  - Format handling (model-driven, zero external binaries): Do NOT reject `response_format` values (`mp3`, `opus`, `aac`, `flac`, etc.). Always invoke Vertex AI `:generateContent` and return the model's returned audio bytes and `mimeType` as the source of truth:
    - If client explicitly requests `"wav"` and upstream returned raw PCM (`audio/L16` / `audio/pcm`), wrap via stdlib `wave` and return `audio/wav`.
    - If client explicitly requests `"pcm"` and upstream returned WAV (`audio/wav` or `RIFF`), extract raw frames via stdlib `wave` and return `audio/pcm`.
    - Otherwise (omitted `response_format` or any other requested format), return the decoded audio bytes and upstream `mimeType` directly.
  - Voice resolution: Map standard OpenAI voice names (`alloy`→`Puck`, `nova`→`Aoede`, `echo`→`Charon`, `onyx`→`Fenrir`, `shimmer`→`Leda`, `fable`→`Kore`, `ash`→`Orus`, `coral`→`Zephyr`, `sage`→`Aoede`, `ballad`→`Puck`, `verse`→`Fenrir`) case-insensitively; pass any non-OpenAI voice name (such as native Gemini `Kore`, `Puck`, `Aoede`) verbatim to `prebuiltVoiceConfig.voiceName`.
  - Upstream call: Vertex AI `:generateContent` with `generationConfig: {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": resolved_voice}}}}`.
- **STT (`POST /v1/audio/transcriptions` and `POST /v1/audio/translations`)**:
  - Parse `multipart/form-data` using `python-multipart` (`file: UploadFile`, `model: str`, optional `language`, `prompt`, `response_format`, `temperature`).
  - Validate non-empty file bytes and non-empty `model`. Infer audio MIME type from `file.content_type` (ignoring generic `application/octet-stream`) or filename extension (`.mp3`→`audio/mpeg`, `.wav`→`audio/wav`, `.flac`→`audio/flac`, `.ogg`→`audio/ogg`, `.m4a`/`.mp4`→`audio/mp4`, `.webm`→`audio/webm`, default `audio/wav`).
  - Call Vertex AI `:generateContent` with `inlineData` audio part + deterministic instruction:
    - Transcriptions: transcribe verbatim (respecting optional `language` and `prompt` hints; requesting SRT/VTT timestamps when `response_format` is `srt`/`vtt`).
    - Translations: translate spoken audio directly into English (respecting optional `prompt` hint).
  - Response formatting:
    - `"json"` (default) → `200 application/json`: `{"text": "..."}`
    - `"verbose_json"` → `200 application/json`: `{"task": "transcribe"|"translate", "language": ..., "duration": 0.0, "text": "...", "segments": []}`
    - `"text"` / `"srt"` / `"vtt"` → `200 text/plain; charset=utf-8` returning raw text string.

### D6: Configuration & Wiring
- Add optional fields to `ProxyConfig` in `src/vertext_ai_proxy/core/config.py`:
  - `response_store_max_items: int = 500`
  - `response_store_ttl_seconds: int = 3600`
- Keep `config.example.yaml` and `core/templates.py` updated with commented examples of these optional fields.
- Expose `response_service`, `image_service`, and `audio_service` on `VertexAIProxy` (`proxy.py`) and `app.state` (`app.py`), and mount `responses_router`, `images_router`, and `audio_router` in `create_app()`.
- Add `python-multipart` to `pyproject.toml` via `uv add python-multipart`.

## Risks / Trade-offs

- **[In-memory Responses state lost on restart]** → Acceptable per user requirement (zero external DB). Documented clearly in README and spec; bounded LRU + TTL prevents unbounded RAM growth.
- **[Vertex AI `global` vs regional availability for Imagen/TTS models]** → Some Imagen or TTS models may only be deployed in specific regions (e.g., `us-central1`) rather than `global`. Mitigation: Surface upstream Vertex AI error details transparently via `OpenAIAPIException`, verify actual behavior against local GCP credentials during live E2E testing, and ensure `VertexClient` cleanly respects `gcp.location`.
- **[TTS default format expectation in OpenAI SDK]** → Official OpenAI API defaults omitted `response_format` to `mp3`, whereas this proxy defaults omitted `response_format` to `wav` (and rejects explicit `mp3` with HTTP 400) to avoid external `ffmpeg` dependencies. Mitigation: Return accurate `Content-Type: audio/wav` header and document this clearly in README and error messages.
- **[Gemini `inlineData` size limits for large audio/image uploads]** → Base64-encoding very large uploads in memory increases RAM usage and may hit Vertex AI inline payload limits (~20MB). Mitigation: Document recommended upload sizes and propagate upstream HTTP 413/400 errors cleanly.
- **[Responses API streaming parity edge cases]** → OpenAI's Responses API defines many specialized event types (built-in web search, code interpreter, etc.). Mitigation: Focus strictly on core text, multimodal input, function tool calling, and structured output events so standard OpenAI SDK `responses.create(..., stream=True)` and `responses.stream(...)` work reliably.
