import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import httpx
from httpx import ASGITransport
import pytest

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig
from vertext_ai_proxy.services.response_store import ResponseStore


def _make_app(
    http_client: MagicMock,
    max_items: int = 500,
    ttl_seconds: int = 3600,
):
    config = AppConfig(
        proxy=ProxyConfig(
            api_keys=["sk-resp-test-key"],
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
