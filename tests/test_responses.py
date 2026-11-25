import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import httpx
from httpx import ASGITransport
import pytest

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig
from vertext_ai_proxy.schemas.responses import ResponseCreateRequest
from vertext_ai_proxy.services.interactions_adapter import (
    InteractionsSSETranslator,
    translate_interaction_to_openai_response,
    translate_request_to_interactions,
)
from vertext_ai_proxy.services.response_store import ResponseStore


def _make_app(
    http_client: MagicMock,
    max_items: int = 500,
    ttl_seconds: int = 3600,
    responses_backend: str = "local",
):
    config = AppConfig(
        proxy=ProxyConfig(
            api_keys=["sk-resp-test-key"],
            responses_backend=responses_backend,
            response_store_max_items=max_items,
            response_store_ttl_seconds=ttl_seconds,
        ),
        gcp=GCPConfig(project_id="test-proj", location="global"),
    )
    mock_token_manager = MagicMock()
    mock_token_manager.project_id = "test-proj"
    mock_token_manager.location = "global"
    mock_token_manager.get_access_token = AsyncMock(return_value="mock-gcp-token")

    return create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=http_client,
    )


def test_response_store_lru_and_ttl(monkeypatch):
    fake_now = 1000.0
    monkeypatch.setattr("time.monotonic", lambda: fake_now)

    store = ResponseStore(max_items=2, ttl_seconds=10)
    store.save("resp_1", {"id": "resp_1", "status": "completed"}, [])
    store.save("resp_2", {"id": "resp_2", "status": "completed"}, [])
    # Access resp_1 so resp_2 becomes LRU
    assert store.get("resp_1") is not None
    store.save("resp_3", {"id": "resp_3", "status": "completed"}, [])

    assert store.get("resp_2") is None
    assert store.get("resp_1") is not None
    assert store.get("resp_3") is not None

    # Advance past TTL
    fake_now = 1020.0
    assert store.get("resp_1") is None
    assert store.get("resp_3") is None


def test_responses_create_retrieve_delete_and_previous_id():
    captured_payloads: list[dict] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured_payloads.append(json)
        turn = len(captured_payloads)
        text = "Hello! Your name is Alice." if turn == 2 else "Nice to meet you, Alice!"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": text}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 8,
                    "totalTokenCount": 20,
                },
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http)
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-resp-test-key"}

    # Turn 1
    r1 = client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": "gemini-3.8-flash",
            "instructions": "Be concise.",
            "input": "My name is Alice.",
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["id"].startswith("resp_")
    assert b1["object"] == "response"
    assert b1["status"] == "completed"
    assert b1["output_text"] == "Nice to meet you, Alice!"
    assert b1["usage"]["input_tokens"] == 12
    assert b1["usage"]["output_tokens"] == 8
    assert b1["usage"]["total_tokens"] == 20
    assert captured_payloads[0]["systemInstruction"]["parts"][0]["text"] == "Be concise."

    # GET stored response
    rg = client.get(f"/v1/responses/{b1['id']}", headers=headers)
    assert rg.status_code == 200
    assert rg.json()["id"] == b1["id"]

    # Turn 2 with previous_response_id
    r2 = client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": "gemini-3.8-flash",
            "previous_response_id": b1["id"],
            "input": "What is my name?",
        },
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["output_text"] == "Hello! Your name is Alice."
    assert len(captured_payloads[1]["contents"]) == 3
    assert captured_payloads[1]["contents"][0]["role"] == "user"
    assert captured_payloads[1]["contents"][1]["role"] == "model"
    assert captured_payloads[1]["contents"][2]["parts"][0]["text"] == "What is my name?"

    # DELETE response
    rd = client.delete(f"/v1/responses/{b1['id']}", headers=headers)
    assert rd.status_code == 200
    assert rd.json() == {"id": b1["id"], "object": "response.deleted", "deleted": True}

    # GET after delete -> 404
    rg2 = client.get(f"/v1/responses/{b1['id']}", headers=headers)
    assert rg2.status_code == 404

    # Using deleted previous_response_id -> 404
    r3 = client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": "gemini-3.8-flash",
            "previous_response_id": b1["id"],
            "input": "Still remember?",
        },
    )
    assert r3.status_code == 404


def test_responses_store_false_not_persisted():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "Ephemeral"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7},
            },
        )
    )
    mock_http.aclose = AsyncMock()

    client = TestClient(_make_app(mock_http))
    headers = {"Authorization": "Bearer sk-resp-test-key"}

    resp = client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "gemini-3.8-flash", "input": "Hi", "store": False},
    )
    assert resp.status_code == 200
    rid = resp.json()["id"]
    assert client.get(f"/v1/responses/{rid}", headers=headers).status_code == 404


def test_responses_function_calling_and_tool_output_continuation():
    captured: list[dict] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(json)
        if len(captured) == 1:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "get_weather",
                                            "args": {"city": "Tokyo"},
                                        }
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "It is 22C in Tokyo."}]}}
                ]
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = TestClient(_make_app(mock_http))
    headers = {"Authorization": "Bearer sk-resp-test-key"}

    r1 = client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": "gemini-3.8-flash",
            "input": "Weather in Tokyo?",
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get city weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                }
            ],
            "tool_choice": "auto",
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "weather_resp",
                    "schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
                }
            },
        },
    )
    assert r1.status_code == 200
    body1 = r1.json()
    fc_item = body1["output"][0]
    assert fc_item["type"] == "function_call"
    assert fc_item["name"] == "get_weather"
    assert json.loads(fc_item["arguments"]) == {"city": "Tokyo"}
    assert captured[0]["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"
    assert captured[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    assert captured[0]["generationConfig"]["responseMimeType"] == "application/json"

    # Send function_call_output referencing previous_response_id
    r2 = client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": "gemini-3.8-flash",
            "previous_response_id": body1["id"],
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": fc_item["call_id"],
                    "output": '{"temp": 22}',
                }
            ],
        },
    )
    assert r2.status_code == 200
    assert r2.json()["output_text"] == "It is 22C in Tokyo."
    last_turn = captured[1]["contents"][-1]
    assert last_turn["parts"][0]["functionResponse"]["name"] == "get_weather"
    assert last_turn["parts"][0]["functionResponse"]["response"] == {"temp": 22}


@pytest.mark.asyncio
async def test_responses_streaming_sse_events():
    sse_lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"Hello "}]}}]}',
        "",
        'data: {"candidates":[{"content":{"parts":[{"text":"world!"}]}}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2,"totalTokenCount":6}}',
        "",
    ]

    mock_upstream = MagicMock()
    mock_upstream.status_code = 200

    async def fake_aiter_lines():
        for line in sse_lines:
            yield line

    mock_upstream.aiter_lines = fake_aiter_lines
    mock_upstream.aclose = AsyncMock()

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.build_request = MagicMock(return_value=MagicMock())
    mock_http.send = AsyncMock(return_value=mock_upstream)
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer sk-resp-test-key"},
            json={"model": "gemini-3.8-flash", "input": "Say hello", "stream": True},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        events: list[tuple[str, dict]] = []
        current_event = None
        for line in resp.text.splitlines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                events.append((current_event, json.loads(line[6:])))
                current_event = None

        event_names = [e[0] for e in events]
        assert event_names == [
            "response.created",
            "response.in_progress",
            "response.output_item.added",
            "response.content_part.added",
            "response.output_text.delta",
            "response.output_text.delta",
            "response.output_text.done",
            "response.content_part.done",
            "response.output_item.done",
            "response.completed",
        ]
        deltas = [e[1]["delta"] for e in events if e[0] == "response.output_text.delta"]
        assert "".join(deltas) == "Hello world!"
        completed_resp = events[-1][1]["response"]
        assert completed_resp["status"] == "completed"
        assert completed_resp["output_text"] == "Hello world!"
        assert completed_resp["usage"]["total_tokens"] == 6

        # Verify streamed response was saved to in-memory store
        retrieved = await client.get(
            f"/v1/responses/{completed_resp['id']}",
            headers={"Authorization": "Bearer sk-resp-test-key"},
        )
        assert retrieved.status_code == 200
        assert retrieved.json()["output_text"] == "Hello world!"


@pytest.mark.asyncio
async def test_responses_cancel_in_flight_generation():
    started = asyncio.Event()
    gate = asyncio.Event()

    async def slow_post(url, headers=None, json=None, timeout=None):
        started.set()
        await gate.wait()
        return httpx.Response(200, json={"candidates": []})

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=slow_post)
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer sk-resp-test-key"}
        create_task = asyncio.create_task(
            client.post(
                "/v1/responses",
                headers=headers,
                json={"model": "gemini-3.8-flash", "input": "Take your time"},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2.0)

        active_ids = list(app.state.response_service.store._active.keys())
        assert len(active_ids) == 1
        rid = active_ids[0]

        cancel_resp = await client.post(f"/v1/responses/{rid}/cancel", headers=headers)
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        create_resp = await asyncio.wait_for(create_task, timeout=2.0)
        assert create_resp.status_code == 200
        assert create_resp.json()["status"] == "cancelled"


def test_responses_invalid_empty_input_returns_400():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.aclose = AsyncMock()
    client = TestClient(_make_app(mock_http))
    headers = {"Authorization": "Bearer sk-resp-test-key"}

    resp = client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "gemini-3.8-flash", "input": "   "},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"


# ============================================================
# Interactions Adapter Unit Tests (Tasks 2.1 - 2.3)
# ============================================================


def test_interactions_translate_request_multimodal_tools_and_formats():
    req = ResponseCreateRequest(
        model="google/gemini-3.8-flash",
        instructions="Follow rules.",
        previous_response_id="ChA_prev_123",
        store=True,
        background=True,
        temperature=0.4,
        top_p=0.9,
        max_output_tokens=256,
        reasoning={"effort": "high"},
        tools=[
            {
                "type": "function",
                "name": "lookup_order",
                "description": "Find an order by ID",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            }
        ],
        tool_choice={"type": "function", "name": "lookup_order"},
        text={
            "format": {
                "type": "json_schema",
                "name": "order_result",
                "schema": {"type": "object", "properties": {"status": {"type": "string"}}},
            }
        },
        input=[
            {"role": "developer", "content": "Be exact."},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Inspect this"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                    {"type": "input_file", "file_data": "data:application/pdf;base64,BBBB"},
                ],
            },
        ],
    )
    payload = translate_request_to_interactions(req, function_call_names={}, stream=True)

    assert payload["model"] == "gemini-3.8-flash"
    assert payload["stream"] is True
    assert payload["store"] is True
    assert payload["background"] is True
    assert payload["previous_interaction_id"] == "ChA_prev_123"
    assert payload["system_instruction"] == "Follow rules.\n\nBe exact."
    assert payload["input"] == [
        {"type": "text", "text": "Inspect this"},
        {"type": "image", "mime_type": "image/png", "data": "AAAA"},
        {"type": "document", "mime_type": "application/pdf", "data": "BBBB"},
    ]
    assert payload["generation_config"] == {
        "temperature": 0.4,
        "top_p": 0.9,
        "max_output_tokens": 256,
        "thinking_level": "high",
        "tool_choice": {"allowed_tools": {"mode": "any", "tools": ["lookup_order"]}},
    }
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "lookup_order",
            "description": "Find an order by ID",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        }
    ]
    assert payload["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": {"type": "object", "properties": {"status": {"type": "string"}}},
    }


def test_interactions_translate_request_turns_and_function_result():
    multi_req = ResponseCreateRequest(
        model="gemini-3.8-flash",
        input=[
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "How are you?"},
        ],
        tool_choice="required",
        text={"format": {"type": "json_object"}},
    )
    multi_payload = translate_request_to_interactions(multi_req, function_call_names={})
    assert multi_payload["input"] == [
        {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
        {"role": "model", "content": [{"type": "text", "text": "Hello!"}]},
        {"role": "user", "content": [{"type": "text", "text": "How are you?"}]},
    ]
    assert multi_payload["generation_config"]["tool_choice"] == "any"
    assert multi_payload["response_format"] == {"type": "text", "mime_type": "application/json"}

    tool_req = ResponseCreateRequest(
        model="gemini-3.8-flash",
        previous_response_id="ChA_call_turn",
        input=[
            {
                "type": "function_call_output",
                "call_id": "fc_call_1",
                "output": '{"temp": 21}',
            }
        ],
    )
    tool_payload = translate_request_to_interactions(
        tool_req, function_call_names={"fc_call_1": "get_weather"}
    )
    assert tool_payload["previous_interaction_id"] == "ChA_call_turn"
    assert tool_payload["input"] == [
        {
            "type": "function_result",
            "name": "get_weather",
            "call_id": "fc_call_1",
            "result": [{"type": "text", "text": '{"temp": 21}'}],
        }
    ]


def test_interactions_translate_response_steps_usage_and_status():
    interaction = {
        "id": "ChA_interaction_001",
        "status": "requires_action",
        "model": "gemini-3.8-flash",
        "created": "2026-11-25T06:00:00Z",
        "usage": {
            "total_input_tokens": 11,
            "total_output_tokens": 20,
            "total_cached_tokens": 5,
            "total_thought_tokens": 30,
            "total_tokens": 61,
        },
        "steps": [
            {"type": "thought", "signature": "sig123", "summary": [{"type": "text", "text": "Checking weather"}]},
            {"type": "model_output", "content": [{"type": "text", "text": "Let me check."}]},
            {"type": "function_call", "id": "call_xyz", "name": "get_weather", "arguments": {"city": "Osaka"}},
        ],
    }
    discovered: dict[str, str] = {}
    resp = translate_interaction_to_openai_response(interaction, function_call_names=discovered)

    assert resp["id"] == "ChA_interaction_001"
    assert resp["status"] == "completed"
    assert resp["created_at"] == 1795586400
    assert resp["output_text"] == "Let me check."
    assert resp["reasoning"]["summary"] == "Checking weather"
    assert resp["output"][0]["type"] == "message"
    assert resp["output"][1]["type"] == "function_call"
    assert resp["output"][1]["call_id"] == "call_xyz"
    assert resp["output"][1]["name"] == "get_weather"
    assert json.loads(resp["output"][1]["arguments"]) == {"city": "Osaka"}
    assert discovered == {"call_xyz": "get_weather"}
    assert resp["usage"] == {
        "input_tokens": 11,
        "input_tokens_details": {"cached_tokens": 5},
        "output_tokens": 50,
        "output_tokens_details": {"reasoning_tokens": 30},
        "total_tokens": 61,
    }


def test_interactions_sse_translator_text_and_function_call():
    req = ResponseCreateRequest(model="gemini-3.8-flash", input="Weather?", stream=True)
    discovered: dict[str, str] = {}
    translator = InteractionsSSETranslator(request=req, function_call_names=discovered)

    upstream_events = [
        {
            "event_type": "interaction.created",
            "interaction": {"id": "ChA_stream_1", "status": "in_progress", "model": "gemini-3.8-flash"},
        },
        {"event_type": "step.start", "index": 0, "step": {"type": "thought"}},
        {"event_type": "step.delta", "index": 0, "delta": {"type": "thought_signature", "signature": "abc"}},
        {"event_type": "step.stop", "index": 0},
        {"event_type": "step.start", "index": 1, "step": {"type": "model_output"}},
        {"event_type": "step.delta", "index": 1, "delta": {"type": "text", "text": "Checking "}},
        {"event_type": "step.delta", "index": 1, "delta": {"type": "text", "text": "now."}},
        {"event_type": "step.stop", "index": 1},
        {
            "event_type": "step.start",
            "index": 2,
            "step": {"type": "function_call", "id": "fc_99", "name": "get_weather", "arguments": {}},
        },
        {
            "event_type": "step.delta",
            "index": 2,
            "delta": {"type": "arguments_delta", "arguments": '{"city":'},
        },
        {
            "event_type": "step.delta",
            "index": 2,
            "delta": {"type": "arguments_delta", "arguments": '"Kyoto"}'},
        },
        {"event_type": "step.stop", "index": 2},
        {
            "event_type": "interaction.completed",
            "interaction": {
                "id": "ChA_stream_1",
                "status": "requires_action",
                "usage": {"total_input_tokens": 8, "total_output_tokens": 12, "total_tokens": 20},
            },
        },
    ]

    parsed: list[tuple[str, dict]] = []
    for ev in upstream_events:
        for frame in translator.process_event(ev):
            lines = frame.strip().splitlines()
            ev_name = lines[0].removeprefix("event: ").strip()
            ev_data = json.loads(lines[1].removeprefix("data: ").strip())
            parsed.append((ev_name, ev_data))

    names = [p[0] for p in parsed]
    assert names == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert discovered == {"fc_99": "get_weather"}
    completed = parsed[-1][1]["response"]
    assert completed["id"] == "ChA_stream_1"
    assert completed["status"] == "completed"
    assert completed["output_text"] == "Checking now."
    assert completed["output"][1]["type"] == "function_call"
    assert json.loads(completed["output"][1]["arguments"]) == {"city": "Kyoto"}
    assert completed["usage"]["total_tokens"] == 20


# ============================================================
# ResponseService Interactions Routing, Lifecycle & Auto Fallback (Tasks 3.1 - 3.3)
# ============================================================


def test_responses_interactions_chain_retrieve_delete_and_cancel():
    captured_posts: list[tuple[str, dict | None]] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured_posts.append((url, json))
        if url.endswith("/cancel"):
            return httpx.Response(
                200,
                json={"id": "ChA_int_1", "status": "cancelled", "model": "gemini-3.8-flash", "steps": []},
            )
        if len(captured_posts) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "ChA_int_1",
                    "status": "completed",
                    "model": "gemini-3.8-flash",
                    "created": "2026-11-25T06:00:00Z",
                    "usage": {"total_input_tokens": 10, "total_output_tokens": 5, "total_tokens": 15},
                    "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Stored on Google!"}]}],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "ChA_int_2",
                "status": "completed",
                "model": "gemini-3.8-flash",
                "previous_interaction_id": "ChA_int_1",
                "created": "2026-11-25T06:01:00Z",
                "usage": {"total_input_tokens": 15, "total_output_tokens": 4, "total_tokens": 19},
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Your secret is 42."}]}],
            },
        )

    async def fake_get(url, headers=None, timeout=None):
        if url.endswith("/ChA_missing"):
            return httpx.Response(404, json={"error": {"message": "Not found"}})
        return httpx.Response(
            200,
            json={
                "id": "ChA_int_1",
                "status": "completed",
                "model": "gemini-3.8-flash",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Stored on Google!"}]}],
            },
        )

    async def fake_delete(url, headers=None, timeout=None):
        if url.endswith("/ChA_missing"):
            return httpx.Response(404, json={"error": {"message": "Not found"}})
        return httpx.Response(200, json={})

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_http.delete = AsyncMock(side_effect=fake_delete)
    mock_http.aclose = AsyncMock()

    client = TestClient(_make_app(mock_http, responses_backend="interactions"))
    headers = {"Authorization": "Bearer sk-resp-test-key"}

    r1 = client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "google/gemini-3.8-flash", "input": "My secret is 42."},
    )
    assert r1.status_code == 200
    assert r1.json()["id"] == "ChA_int_1"
    assert r1.json()["output_text"] == "Stored on Google!"
    assert captured_posts[0][0].endswith("/v1beta1/projects/test-proj/locations/global/interactions")
    assert captured_posts[0][1]["model"] == "gemini-3.8-flash"

    r2 = client.post(
        "/v1/responses",
        headers=headers,
        json={
            "model": "gemini-3.8-flash",
            "previous_response_id": "ChA_int_1",
            "input": "What is my secret?",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == "ChA_int_2"
    assert captured_posts[1][1]["previous_interaction_id"] == "ChA_int_1"
    assert captured_posts[1][1]["input"] == "What is my secret?"

    rg = client.get("/v1/responses/ChA_int_1", headers=headers)
    assert rg.status_code == 200
    assert rg.json()["id"] == "ChA_int_1"
    assert rg.json()["output_text"] == "Stored on Google!"
    assert client.get("/v1/responses/ChA_missing", headers=headers).status_code == 404

    rc = client.post("/v1/responses/ChA_int_1/cancel", headers=headers)
    assert rc.status_code == 200
    assert rc.json()["status"] == "cancelled"
    assert captured_posts[2][0].endswith("/interactions/ChA_int_1/cancel")

    rd = client.delete("/v1/responses/ChA_int_1", headers=headers)
    assert rd.status_code == 200
    assert rd.json() == {"id": "ChA_int_1", "object": "response.deleted", "deleted": True}
    assert client.delete("/v1/responses/ChA_missing", headers=headers).status_code == 404


def test_responses_interactions_function_call_name_recovery_after_restart():
    captured_posts: list[dict] = []

    async def fake_get(url, headers=None, timeout=None):
        return httpx.Response(
            200,
            json={
                "id": "ChA_prev_fc",
                "status": "requires_action",
                "steps": [
                    {"type": "function_call", "id": "call_recovered_1", "name": "get_stock", "arguments": {"sym": "GOOG"}}
                ],
            },
        )

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured_posts.append(json)
        return httpx.Response(
            200,
            json={
                "id": "ChA_after_fc",
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "GOOG is 190."}]}],
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.get = AsyncMock(side_effect=fake_get)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = TestClient(_make_app(mock_http, responses_backend="interactions"))
    resp = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer sk-resp-test-key"},
        json={
            "model": "gemini-3.8-flash",
            "previous_response_id": "ChA_prev_fc",
            "input": [{"type": "function_call_output", "call_id": "call_recovered_1", "output": '{"price": 190}'}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["output_text"] == "GOOG is 190."
    assert captured_posts[0]["input"][0]["name"] == "get_stock"


@pytest.mark.asyncio
async def test_responses_interactions_streaming_sse_route():
    sse_lines = [
        'event: interaction.created',
        'data: {"event_type":"interaction.created","interaction":{"id":"ChA_sse_42","status":"in_progress","model":"gemini-3.8-flash"}}',
        '',
        'event: step.start',
        'data: {"event_type":"step.start","index":0,"step":{"type":"model_output"}}',
        '',
        'event: step.delta',
        'data: {"event_type":"step.delta","index":0,"delta":{"type":"text","text":"Hi "}}',
        '',
        'event: step.delta',
        'data: {"event_type":"step.delta","index":0,"delta":{"type":"text","text":"Interactions!"}}',
        '',
        'event: step.stop',
        'data: {"event_type":"step.stop","index":0}',
        '',
        'event: interaction.completed',
        'data: {"event_type":"interaction.completed","interaction":{"id":"ChA_sse_42","status":"completed","usage":{"total_input_tokens":5,"total_output_tokens":3,"total_tokens":8}}}',
        '',
        'event: done',
        'data: [DONE]',
    ]

    mock_upstream = MagicMock()
    mock_upstream.status_code = 200

    async def fake_aiter_lines():
        for line in sse_lines:
            yield line

    mock_upstream.aiter_lines = fake_aiter_lines
    mock_upstream.aclose = AsyncMock()

    mock_http = MagicMock(spec=httpx.AsyncClient)
    built_requests: list[tuple] = []

    def fake_build_request(method, url, headers=None, json=None, timeout=None):
        built_requests.append((method, url, headers, json))
        return MagicMock()

    mock_http.build_request = MagicMock(side_effect=fake_build_request)
    mock_http.send = AsyncMock(return_value=mock_upstream)
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http, responses_backend="interactions")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer sk-resp-test-key"},
            json={"model": "gemini-3.8-flash", "input": "Greet", "stream": True},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert built_requests[0][1].endswith("/interactions")
        assert "?alt=sse" not in built_requests[0][1]
        assert built_requests[0][3]["stream"] is True

        events: list[tuple[str, dict]] = []
        current_event = None
        for line in resp.text.splitlines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                events.append((current_event, json.loads(line[6:])))
                current_event = None

        assert events[0][0] == "response.created"
        assert events[0][1]["response"]["id"] == "ChA_sse_42"
        assert events[-1][0] == "response.completed"
        assert events[-1][1]["response"]["output_text"] == "Hi Interactions!"


def test_responses_auto_mode_403_fallback_and_local_id_routing():
    called_urls: list[str] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        called_urls.append(url)
        if url.endswith("/interactions"):
            return httpx.Response(
                403,
                json={"error": {"code": 403, "message": "Permission aiplatform.interactions.create denied"}},
            )
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"role": "model", "parts": [{"text": "Fallback OK"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7},
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = TestClient(_make_app(mock_http, responses_backend="auto"))
    headers = {"Authorization": "Bearer sk-resp-test-key"}

    r1 = client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "gemini-3.8-flash", "input": "Hello"},
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["id"].startswith("resp_")
    assert b1["output_text"] == "Fallback OK"
    assert len(called_urls) == 2
    assert called_urls[0].endswith("/interactions")
    assert called_urls[1].endswith(":generateContent")

    r2 = client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "gemini-3.8-flash", "previous_response_id": b1["id"], "input": "Follow up"},
    )
    assert r2.status_code == 200
    assert len(called_urls) == 3
    assert called_urls[2].endswith(":generateContent")

    assert client.get(f"/v1/responses/{b1['id']}", headers=headers).status_code == 200
    assert client.delete(f"/v1/responses/{b1['id']}", headers=headers).status_code == 200


@pytest.mark.asyncio
async def test_responses_auto_mode_streaming_403_fallback():
    interactions_403 = MagicMock()
    interactions_403.status_code = 403
    interactions_403.text = '{"error": {"message": "Permission denied"}}'
    interactions_403.json = MagicMock(return_value={"error": {"message": "Permission denied"}})
    interactions_403.aread = AsyncMock()
    interactions_403.aclose = AsyncMock()

    local_stream = MagicMock()
    local_stream.status_code = 200

    async def fake_local_lines():
        yield 'data: {"candidates":[{"content":{"parts":[{"text":"Stream fallback!"}]}}]}'

    local_stream.aiter_lines = fake_local_lines
    local_stream.aclose = AsyncMock()

    urls: list[str] = []

    def fake_build_request(method, url, headers=None, json=None, timeout=None):
        urls.append(url)
        return MagicMock(url=url)

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.build_request = MagicMock(side_effect=fake_build_request)
    mock_http.send = AsyncMock(side_effect=[interactions_403, local_stream])
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http, responses_backend="auto")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer sk-resp-test-key"},
            json={"model": "gemini-3.8-flash", "input": "Stream test", "stream": True},
        )
        assert resp.status_code == 200
        assert "Stream fallback!" in resp.text
        assert urls[0].endswith("/interactions")
        assert ":streamGenerateContent?alt=sse" in urls[1]


def test_responses_explicit_interactions_mode_surfaces_403():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        return_value=httpx.Response(
            403,
            json={"error": {"code": 403, "message": "Permission denied on interactions"}},
        )
    )
    mock_http.aclose = AsyncMock()

    client = TestClient(_make_app(mock_http, responses_backend="interactions"))
    resp = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer sk-resp-test-key"},
        json={"model": "gemini-3.8-flash", "input": "Strict test"},
    )
    assert resp.status_code == 403
    assert "Permission denied on interactions" in resp.json()["error"]["message"]
    assert mock_http.post.call_count == 1
