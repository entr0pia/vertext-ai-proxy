# Tasks

## 1. Configuration and VertexClient Interactions URLs

- [x] 1.1 Add `responses_backend: Literal["auto", "interactions", "local"] = "auto"` to `ProxyConfig` in `src/vertext_ai_proxy/core/config.py`, update `config.example.yaml` and `src/vertext_ai_proxy/core/templates.py`, and add unit tests in `tests/test_config.py` verifying default, valid overrides, and invalid value rejection.
- [x] 1.2 Add `get_interactions_url`, `get_interaction_url`, and `get_interaction_cancel_url` to `VertexClient` in `src/vertext_ai_proxy/services/vertex_client.py` respecting `get_domain(location)`, and verify with unit tests in `tests/test_vertex_client.py` covering both `global` and regional locations.

## 2. Vertex AI Interactions Protocol Adapter

- [x] 2.1 Create `src/vertext_ai_proxy/services/interactions_adapter.py` implementing OpenAI-to-Interactions request translation (`model`, `input` text/multimodal/turns/`function_result`, `system_instruction`, `previous_interaction_id`, `store`, `background`, `tools`, `generation_config` including `tool_choice` and `thinking_level`, and `response_format`) and verify with unit tests in `tests/test_responses.py`.
- [x] 2.2 Implement Interactions-to-OpenAI response translation in `src/vertext_ai_proxy/services/interactions_adapter.py` mapping `steps` (`model_output`, `function_call`, `thought`), `status` (including `requires_action` -> `completed`), ISO timestamp conversion, and normalized `usage` (`input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`), verified by unit tests in `tests/test_responses.py`.
- [x] 2.3 Implement Interactions SSE stream translation in `src/vertext_ai_proxy/services/interactions_adapter.py` converting upstream `interaction.created`, `step.start`, `step.delta` (`text`, `arguments_delta`), `step.stop`, and `interaction.completed` frames into OpenAI Responses semantic SSE events, verified by streaming unit tests in `tests/test_responses.py`.

## 3. ResponseService Backend Routing, Lifecycle, and Auto Fallback

- [x] 3.1 Extend `src/vertext_ai_proxy/schemas/responses.py` with optional `background: bool | None = None` passthrough and wire `ResponseService` in `src/vertext_ai_proxy/services/response_service.py` to support `"interactions"`, `"local"`, and `"auto"` routing for `POST /v1/responses` (both non-streaming and streaming), including `call_id -> name` lookup for `function_call_output`. Verify with unit tests in `tests/test_responses.py`.
- [x] 3.2 Wire `GET /v1/responses/{response_id}`, `DELETE /v1/responses/{response_id}`, and `POST /v1/responses/{response_id}/cancel` in `ResponseService` to delegate upstream Interactions IDs to Vertex AI `/interactions/{id}` (and `/cancel`) while keeping local `resp_*` IDs routed to `ResponseStore`, verified by unit tests in `tests/test_responses.py`.
- [x] 3.3 Implement `"auto"` mode HTTP 403 (`PERMISSION_DENIED`) fallback with soft cooldown to the local `:generateContent` + `ResponseStore` backend for both non-streaming and streaming calls (while surfacing HTTP 403 directly in explicit `"interactions"` mode), and verify both paths plus explicit `"local"` mode in `tests/test_responses.py`.
- [x] 3.4 Document `proxy.responses_backend` (`auto` / `interactions` / `local`) and required GCP IAM role (`roles/aiplatform.user` / *Agent Platform User*) in `README.md` and `AGENTS.md`, and verify existing + new pytest suite passes (`uv run pytest -v`).

## 4. Integration and Live Vertex AI End-to-End Verification

- [x] 4.1 Run full test suite (`$env:PYTHONUTF8=1; uv run pytest -v`) and `openspec validate "responses-interactions-backend" --strict` to confirm zero regressions and spec compliance.
- [x] 4.2 Execute live E2E verification against a running local proxy (`http://127.0.0.1:8766` with `config.local.yaml`) verifying real Vertex AI Interactions API execution: non-streaming `POST /v1/responses`, multi-turn `previous_response_id` server-side state recall, `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, `stream: true` SSE events, function calling + `function_call_output`, and structured `text.format`.

## Workflow follow-up

- Archive the change under `openspec/changes/archive/YYYY-MM-DD-NN-responses-interactions-backend/` after all tasks and live E2E verification succeed.
- Bump package version and commit locally only after explicit user confirmation.
