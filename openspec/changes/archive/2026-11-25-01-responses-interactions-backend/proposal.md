# Proposal

## Why

Currently, `POST /v1/responses` and its lifecycle endpoints (`GET`, `DELETE`, `cancel`) translate requests into Vertex AI's stateless `:generateContent` / `:streamGenerateContent` API and maintain conversation history exclusively in a volatile in-memory `ResponseStore`. While this saves client-to-proxy bandwidth, the proxy still re-sends the full conversation history upstream to Vertex AI on every turn, loses all state across proxy restarts, and cannot leverage Google's native server-side state retention or cross-turn encrypted thought signatures.

Google Cloud Vertex AI now exposes the native **Gemini Interactions API** (`/v1beta1/projects/{project}/locations/{location}/interactions`), which directly mirrors OpenAI's Responses API with server-side `previous_interaction_id` chaining, `store` persistence, native SSE streaming (`interaction.created` / `step.*` / `interaction.completed`), `GET`/`DELETE`/`cancel` endpoints, and preserved thought signatures. Routing `/v1/responses` through Vertex AI Interactions API eliminates proxy-to-Vertex redundant context uploads and persists conversation state on Google's servers, while keeping graceful fallback to local `:generateContent` + `ResponseStore` for service accounts that lack `aiplatform.interactions.*` IAM permissions.

## What Changes

- Add a configurable `proxy.responses_backend` setting accepting `"auto"` (default), `"interactions"`, or `"local"`.
- Add Vertex AI Interactions API URL helpers to `VertexClient` (`get_interactions_url`, `get_interaction_url`, `get_interaction_cancel_url`).
- Extend `ResponseService` to translate OpenAI Responses API requests and responses (non-streaming and SSE streaming, `previous_response_id`, `store`, `instructions`, multimodal text/image/file inputs, function `tools`/`tool_choice`/`function_call_output`, `reasoning.effort`, `text.format`, `GET`, `DELETE`, and `cancel`) to and from Vertex AI native `/interactions`.
- Implement `"auto"` mode routing: prefer Vertex AI Interactions API by default, automatically fall back to the local `:generateContent` + in-memory `ResponseStore` path when upstream rejects with HTTP 403 (`PERMISSION_DENIED`), and route `previous_response_id` / `GET` / `DELETE` / `cancel` calls to the matching backend based on whether the ID belongs to the local store (`resp_*`) or upstream Google Interactions.
- Keep `"local"` mode intact for full backward compatibility and environments using restricted `Agent Platform Express User` service accounts.
- Update configuration examples, templates, documentation (`README.md`, `AGENTS.md`), unit tests, and live Vertex AI E2E verification.

## Capabilities

### New Capabilities
<!-- None — this change extends the existing OpenAI Responses API capability within `openai-proxy`. -->

### Modified Capabilities
- `openai-proxy`: Extend the Responses API creation/streaming and state-management requirements to support configurable backend routing (`auto` | `interactions` | `local`), native Vertex AI Interactions API translation with server-side state (`previous_interaction_id`, `GET`/`DELETE`/`cancel`, SSE step-event mapping), and automatic HTTP 403 fallback to the bounded in-memory `:generateContent` backend.

## Impact

- **Config & Templates**: `src/vertext_ai_proxy/core/config.py`, `src/vertext_ai_proxy/core/templates.py`, `config.example.yaml`
- **Services & Transport**: `src/vertext_ai_proxy/services/vertex_client.py`, `src/vertext_ai_proxy/services/response_service.py` (optionally decomposed with a dedicated `interactions` translation helper module), `src/vertext_ai_proxy/services/response_store.py`
- **Schemas**: `src/vertext_ai_proxy/schemas/responses.py` (optional `background` passthrough if needed, no breaking schema changes)
- **Tests & Docs**: `tests/test_config.py`, `tests/test_vertex_client.py`, `tests/test_responses.py`, `README.md`, `AGENTS.md`
- **Dependencies**: Zero new third-party dependencies required (uses existing `httpx` + `google-auth`).
