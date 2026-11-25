# Design

## Context

See `proposal.md` for motivation. Today `ResponseService` (`src/vertext_ai_proxy/services/response_service.py`) translates `/v1/responses` exclusively to Vertex AI `:generateContent` / `:streamGenerateContent` and persists history in the in-memory `ResponseStore` (`response_store.py`).

Live probes against Vertex AI confirmed the exact Interactions API contract on `https://{domain}/v1beta1/projects/{project_id}/locations/{location}/interactions`:
- Requires IAM permissions `aiplatform.interactions.*` (bundled in `roles/aiplatform.user` / *Agent Platform User*; absent in `Agent Platform Express User`).
- `POST /interactions` supports `model`, `input`, `system_instruction`, `previous_interaction_id`, `store`, `stream`, `background`, `tools`, `response_format`, and `generation_config` (`temperature`, `top_p`, `max_output_tokens`, `tool_choice`, `thinking_level`).
- Streaming uses `POST /interactions` **without** `?alt=sse`, setting `"stream": true` in the JSON body and `Accept: text/event-stream` + `Accept-Encoding: identity` in headers. Upstream emits `interaction.created`, `interaction.status_update`, `step.start`, `step.delta`, `step.stop`, `interaction.completed`, and `done`.
- Lifecycle endpoints are `GET /interactions/{id}`, `DELETE /interactions/{id}`, and `POST /interactions/{id}/cancel`.

## Goals / Non-Goals

**Goals:**
- Route `/v1/responses` through Vertex AI Interactions API so multi-turn conversations (`previous_response_id`) send only incremental turn input upstream and persist state server-side on Google Cloud.
- Provide zero-config resilience via `proxy.responses_backend: "auto"` so projects without `aiplatform.interactions.*` permissions transparently fall back to the existing `:generateContent` + `ResponseStore` path.
- Preserve full OpenAI Responses API wire compatibility (non-streaming, SSE semantic events, function calling, structured output, multimodal inputs, `store: false`, `GET`/`DELETE`/`cancel`).
- Keep unit tests fast and deterministic without real network calls, and verify all paths live against Vertex AI before archiving.

**Non-Goals:**
- Do not add external databases (SQLite/Redis) for local fallback state.
- Do not add Vertex AI managed-agent-only features (such as remote sandboxes or `client.agents.create`) to the OpenAI `/v1/responses` schema.
- Do not break existing `"local"` backend behavior or existing `test_responses.py` contracts.

## Decisions

### 1. Configuration Surface (`proxy.responses_backend`)
- Add `responses_backend: Literal["auto", "interactions", "local"] = "auto"` to `ProxyConfig`.
- **Rationale**: `"auto"` gives immediate zero-friction upgrade to users who have `roles/aiplatform.user` while keeping `Agent Platform Express User` setups working without manual config edits. Explicit `"interactions"` and `"local"` allow operators to enforce strict behavior or disable fallback.
- **Alternative considered**: Boolean `use_interactions_api: bool` — rejected because it cannot distinguish strict `"interactions"` (fail fast on 403) from `"auto"` (fallback on 403).

### 2. ID Disambiguation in `auto` Mode
- Local `ResponseStore` records always use the prefix `resp_` (`resp_<24 hex>`).
- Google Vertex AI Interaction IDs are opaque base64-like or `v1_`-prefixed strings and never start with `resp_`.
- In `auto` mode:
  - `POST /v1/responses`: if `previous_response_id` starts with `resp_` (or exists in `self.store`), dispatch directly to the local backend; otherwise try `interactions` first.
  - `GET` / `DELETE` / `cancel` on `{response_id}`: if `response_id` exists in local `self.store` (active or stored) or starts with `resp_`, serve from the local backend; otherwise forward to Vertex AI `/interactions/{response_id}`.
- **Fallback Caching in `auto` Mode**: When `interactions` returns HTTP `403`, log a warning and retry the current request on the local backend. Do NOT permanently disable `interactions` across the entire process lifetime unless desired — wait: actually, to avoid paying an extra 403 round-trip on every single request for restricted SAs, cache a soft TTL cooldown (e.g., 5 minutes, configurable/resettable in tests) or fall back cleanly on 403 while still allowing recovery if IAM is granted later! Let's use a 5-minute (`300s`) soft cooldown on 403 in `auto` mode (logged clearly once per cooldown window).

### 3. VertexClient URL Helpers
- Add to `VertexClient`:
  - `get_interactions_url(location: str | None = None) -> str` → `https://{domain}/v1beta1/projects/{project_id}/locations/{loc}/interactions`
  - `get_interaction_url(interaction_id: str, location: str | None = None) -> str` → `{base}/{interaction_id}`
  - `get_interaction_cancel_url(interaction_id: str, location: str | None = None) -> str` → `{base}/{interaction_id}/cancel`
- Uses `self.get_domain(location)` so regional configs (`us-central1`) and `global` are both respected consistently.

### 4. Request Translation (OpenAI Responses → Vertex AI Interactions)
- **`model`**: Strip leading `google/` vendor prefix (Interactions API expects bare model ID such as `gemini-3.8-flash`).
- **`previous_response_id`**: Pass directly as `previous_interaction_id` when non-empty.
- **`store`**: Pass boolean directly as `store`.
- **`background`**: Pass optional boolean directly if supplied.
- **`instructions` + `system`/`developer` input messages**: Join into top-level `system_instruction`.
- **`input`**:
  - Plain string → string `input`.
  - Array of OpenAI items:
    - `function_call_output` (`call_id`, `output`) → `{"type": "function_result", "call_id": call_id, "name": resolved_name, "result": output}` (or list of content items; `result` accepts string or content list per official Interactions schema). To resolve `name` (which OpenAI omits in `function_call_output`), maintain a lightweight bounded `call_id -> name` map populated whenever Interactions emits a `function_call` step; if missing (e.g., after restart) and `previous_response_id` is set, perform a single best-effort `GET /interactions/{previous_response_id}` to recover the function name.
    - Single user message with text/multimodal content parts → content block list (`{"type": "text", "text": ...}`, `{"type": "image", "data": ..., "mime_type": ...}` or `{"type": "image", "uri": ...}`, `{"type": "document", "data": ..., "mime_type": ...}` or `{"type": "document", "uri": ...}`).
    - Multi-turn array mixing `user` and `assistant` messages → Interactions `Turn` array `[{"role": "user"|"model", "content": [...]}]`.
- **`tools`**: Translate OpenAI function tools (`{"type": "function", "name", "description", "parameters"}`) into Interactions function tool definitions (`{"type": "function", "name", "description", "parameters"}`).
- **`tool_choice`** (inside `generation_config`):
  - `"auto"` / `"none"` → `"auto"` / `"none"`
  - `"required"` → `"any"`
  - `{"type": "function", "name": "fn"}` → `{"allowed_tools": {"mode": "any", "tools": ["fn"]}}`
- **`generation_config`**:
  - `temperature`, `top_p`, `max_output_tokens` mapped 1:1.
  - `reasoning.effort` (`minimal` | `low` | `medium` | `high`) → `thinking_level`.
- **`text.format`** → top-level `response_format`:
  - `type == "json_schema"` → `{"type": "text", "mime_type": "application/json", "schema": schema}`
  - `type == "json_object"` → `{"type": "text", "mime_type": "application/json"}`

### 5. Response & SSE Translation (Vertex AI Interactions → OpenAI Responses)
- **Non-streaming & `GET` / `cancel` response mapping**:
  - `id`: upstream `interaction["id"]`
  - `status`: map `requires_action` → `"completed"` (with `function_call` items in `output`, matching OpenAI Responses SDK expectations where tool-call responses have `status: "completed"` and `output` contains `function_call` items), preserve `completed` / `in_progress` / `queued` / `incomplete` / `failed` / `cancelled`.
  - `created_at`: parse ISO-8601 `created` timestamp to Unix int seconds (fallback to current epoch).
  - `output` & `output_text`: iterate `steps`:
    - `type == "model_output"` → extract `content` items (`type == "text"`) into an OpenAI `message` item (`role: "assistant"`, `content: [{"type": "output_text", "text": ..., "annotations": []}]`) and accumulate `output_text`.
    - `type == "function_call"` → emit OpenAI `function_call` item (`id`, `call_id`, `name`, `arguments` serialized as JSON string, `status: "completed"`), and record `call_id -> name` in the lightweight cache.
    - `type == "thought"` → record reasoning summary if present (without breaking text extraction).
  - `usage`: map `total_input_tokens` → `input_tokens`, `total_output_tokens` → `output_tokens`, `total_tokens` → `total_tokens`, `total_cached_tokens` → `input_tokens_details.cached_tokens`, `total_thought_tokens` → `output_tokens_details.reasoning_tokens`.
- **Streaming SSE translation**:
  - Open upstream stream via `POST /interactions` with `stream: true`, `Accept: text/event-stream`, `Accept-Encoding: identity`.
  - Inspect HTTP status BEFORE returning `StreamingResponse`: if status is `403` and backend mode is `"auto"`, close upstream stream, set cooldown, and fall back to local streaming cleanly!
  - Parse upstream SSE frames (`data: {...}`):
    - `interaction.created` → capture upstream `interaction.id`, emit `response.created` + `response.in_progress`.
    - `step.start` (`model_output`) → allocate message item ID (`msg_*`), emit `response.output_item.added` + `response.content_part.added`.
    - `step.delta` (`text`) → append delta text, emit `response.output_text.delta`.
    - `step.stop` (`model_output`) → emit `response.output_text.done`, `response.content_part.done`, `response.output_item.done`.
    - `step.start` (`function_call`) → emit `response.output_item.added` (`type: "function_call"`), record `call_id -> name`.
    - `step.delta` (`arguments_delta`) → accumulate arguments, emit `response.function_call_arguments.delta`.
    - `step.stop` (`function_call`) → emit `response.function_call_arguments.done` + `response.output_item.done`.
    - `interaction.completed` → map final usage/status, emit `response.completed`.
  - Also register active in-flight stream/non-stream handles locally by `interaction_id` so local cancellation works alongside upstream `POST /interactions/{id}/cancel`.

### 6. Code Organization
- Keep `ResponseService` clean and maintainable by extracting pure Interactions translation helpers into `src/vertext_ai_proxy/services/interactions_adapter.py` (payload builder, step/response parser, SSE translator) while `ResponseService` orchestrates backend routing (`auto` / `interactions` / `local`), 403 fallback, and active-handle lifecycle.

## Risks / Trade-offs

- **[Risk] Function call `name` missing in OpenAI `function_call_output` after proxy restart** → Mitigation: keep a bounded in-memory `call_id -> name` map for normal flows, and fall back to a best-effort `GET /interactions/{previous_response_id}` lookup when the ID isn't cached.
- **[Risk] Interactions API streaming errors or HTTP 403 after stream starts** → Mitigation: send the request and inspect `resp.status_code` **before** returning `StreamingResponse`, enabling clean HTTP 403 fallback in `auto` mode and structured OpenAI JSON errors for non-200 responses.
- **[Risk] Model unsupported by Interactions API (e.g., older legacy models)** → Mitigation: users can set `proxy.responses_backend: local` when targeting legacy models not listed in Interactions API, while keeping `auto` optimized for modern Gemini models (`gemini-2.5-*`, `gemini-3.*`, `gemini-4-*`).
- **[Risk] Existing unit tests assume local `:generateContent` backend** → Mitigation: tests that mock `:generateContent` should explicitly configure `responses_backend="local"` OR test both backends explicitly so there is zero ambiguity and full regression coverage.
