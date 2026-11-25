import base64
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import openai
import pytest

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig


def _make_app(
    upstream_http: MagicMock,
    responses_backend: str = "interactions",
):
    config = AppConfig(
        proxy=ProxyConfig(
            api_keys=["sk-sdk-compat-key"],
            responses_backend=responses_backend,
        ),
        gcp=GCPConfig(project_id="sdk-test-proj", location="global"),
    )
    mock_token_manager = MagicMock()
    mock_token_manager.project_id = "sdk-test-proj"
    mock_token_manager.location = "global"
    mock_token_manager.get_access_token = AsyncMock(return_value="mock-gcp-token")

    return create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=upstream_http,
    )


def _make_sdk_client(app, api_key: str = "sk-sdk-compat-key") -> openai.AsyncOpenAI:
    asgi_http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test/v1",
    )
    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url="http://test/v1",
        http_client=asgi_http,
        max_retries=0,
    )


@pytest.mark.asyncio
async def test_sdk_models_list_retrieve_and_errors():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "publisherModels": [
                    {"name": "publishers/google/models/gemini-3.8-flash"},
                    {"name": "publishers/google/models/gemini-embedding-2"},
                ]
            },
        )
    )
    mock_http.aclose = AsyncMock()
    app = _make_app(mock_http)

    async with _make_sdk_client(app) as sdk:
        models_page = await sdk.models.list()
        ids = [m.id for m in models_page.data]
        assert "gemini-3.8-flash" in ids
        assert "gemini-embedding-2" in ids

        model = await sdk.models.retrieve("gemini-3.8-flash")
        assert model.id == "gemini-3.8-flash"
        assert model.object == "model"

        with pytest.raises(openai.NotFoundError):
            await sdk.models.retrieve("does-not-exist-model")

    async with _make_sdk_client(app, api_key="sk-wrong-key") as bad_sdk:
        with pytest.raises(openai.AuthenticationError):
            await bad_sdk.models.list()


@pytest.mark.asyncio
async def test_sdk_chat_completions_non_streaming_and_streaming():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-sdk-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "google/gemini-3.8-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello via SDK!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            },
        )
    )

    sse_chunks = [
        b'data: {"id":"chatcmpl-s1","object":"chat.completion.chunk","created":1700000000,"model":"google/gemini-3.8-flash","choices":[{"index":0,"delta":{"role":"assistant","content":"Hi "},"finish_reason":null}]}\n\n',
        b'data: {"id":"chatcmpl-s1","object":"chat.completion.chunk","created":1700000000,"model":"google/gemini-3.8-flash","choices":[{"index":0,"delta":{"content":"stream!"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200

    async def fake_aiter_bytes():
        for chunk in sse_chunks:
            yield chunk

    mock_stream_resp.aiter_bytes = fake_aiter_bytes
    mock_stream_resp.aclose = AsyncMock()
    mock_http.build_request = MagicMock(return_value=MagicMock())
    mock_http.send = AsyncMock(return_value=mock_stream_resp)
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http)
    async with _make_sdk_client(app) as sdk:
        completion = await sdk.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert completion.choices[0].message.content == "Hello via SDK!"
        assert completion.usage is not None
        assert completion.usage.total_tokens == 10

        stream = await sdk.chat.completions.create(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "Stream"}],
            stream=True,
        )
        collected: list[str] = []
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                collected.append(chunk.choices[0].delta.content)
        assert "".join(collected) == "Hi stream!"


@pytest.mark.asyncio
async def test_sdk_embeddings_create():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        side_effect=[
            httpx.Response(200, json={"embedding": {"values": [0.1, 0.2, 0.3, 0.4]}}),
            httpx.Response(200, json={"embedding": {"values": [0.5, 0.6, 0.7, 0.8]}}),
        ]
    )
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http)
    async with _make_sdk_client(app) as sdk:
        emb = await sdk.embeddings.create(
            model="gemini-embedding-2",
            input=["alpha", "beta"],
            dimensions=4,
        )
        assert len(emb.data) == 2
        assert emb.data[0].embedding == [0.1, 0.2, 0.3, 0.4]
        assert emb.data[1].embedding == [0.5, 0.6, 0.7, 0.8]
        assert emb.data[1].index == 1


@pytest.mark.asyncio
async def test_sdk_responses_interactions_lifecycle():
    post_count = 0

    async def fake_post(url, headers=None, json=None, timeout=None):
        nonlocal post_count
        if url.endswith("/cancel"):
            return httpx.Response(
                200,
                json={
                    "id": "ChA_sdk_1",
                    "status": "cancelled",
                    "model": "gemini-3.8-flash",
                    "created": "2026-11-25T06:00:00Z",
                    "steps": [],
                },
            )
        post_count += 1
        if post_count == 1:
            return httpx.Response(
                200,
                json={
                    "id": "ChA_sdk_1",
                    "status": "completed",
                    "model": "gemini-3.8-flash",
                    "created": "2026-11-25T06:00:00Z",
                    "usage": {
                        "total_input_tokens": 10,
                        "total_output_tokens": 5,
                        "total_thought_tokens": 12,
                        "total_tokens": 27,
                    },
                    "steps": [
                        {"type": "model_output", "content": [{"type": "text", "text": "Noted."}]}
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "ChA_sdk_2",
                "status": "completed",
                "model": "gemini-3.8-flash",
                "previous_interaction_id": "ChA_sdk_1",
                "created": "2026-11-25T06:01:00Z",
                "usage": {"total_input_tokens": 14, "total_output_tokens": 4, "total_tokens": 18},
                "steps": [
                    {"type": "model_output", "content": [{"type": "text", "text": "Code is 777."}]}
                ],
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ChA_sdk_1",
                "status": "completed",
                "model": "gemini-3.8-flash",
                "created": "2026-11-25T06:00:00Z",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Noted."}]}],
            },
        )
    )
    mock_http.delete = AsyncMock(return_value=httpx.Response(200, json={}))
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http, responses_backend="interactions")
    async with _make_sdk_client(app) as sdk:
        r1 = await sdk.responses.create(
            model="gemini-3.8-flash",
            instructions="Be concise.",
            input="My code is 777.",
        )
        assert r1.id == "ChA_sdk_1"
        assert r1.status == "completed"
        assert r1.output_text == "Noted."
        assert r1.usage is not None
        assert r1.usage.output_tokens_details.reasoning_tokens == 12
        assert r1.usage.total_tokens == 27

        r2 = await sdk.responses.create(
            model="gemini-3.8-flash",
            previous_response_id=r1.id,
            input="What is my code?",
        )
        assert r2.id == "ChA_sdk_2"
        assert r2.output_text == "Code is 777."

        rg = await sdk.responses.retrieve(r1.id)
        assert rg.id == r1.id
        assert rg.output_text == "Noted."

        rc = await sdk.responses.cancel(r1.id)
        assert rc.status == "cancelled"

        await sdk.responses.delete(r1.id)


@pytest.mark.asyncio
async def test_sdk_responses_interactions_streaming_and_function_call():
    sse_lines = [
        "event: interaction.created",
        'data: {"event_type":"interaction.created","interaction":{"id":"ChA_sdk_stream","status":"in_progress","model":"gemini-3.8-flash"}}',
        "",
        "event: step.start",
        'data: {"event_type":"step.start","index":0,"step":{"type":"model_output"}}',
        "",
        "event: step.delta",
        'data: {"event_type":"step.delta","index":0,"delta":{"type":"text","text":"Checking "}}',
        "",
        "event: step.delta",
        'data: {"event_type":"step.delta","index":0,"delta":{"type":"text","text":"weather."}}',
        "",
        "event: step.stop",
        'data: {"event_type":"step.stop","index":0}',
        "",
        "event: step.start",
        'data: {"event_type":"step.start","index":1,"step":{"type":"function_call","id":"call_sdk_1","name":"get_weather","arguments":{}}}',
        "",
        "event: step.delta",
        'data: {"event_type":"step.delta","index":1,"delta":{"type":"arguments_delta","arguments":"{\\"city\\":\\"Tokyo\\"}"}}',
        "",
        "event: step.stop",
        'data: {"event_type":"step.stop","index":1}',
        "",
        "event: interaction.completed",
        'data: {"event_type":"interaction.completed","interaction":{"id":"ChA_sdk_stream","status":"requires_action","usage":{"total_input_tokens":7,"total_output_tokens":9,"total_tokens":16}}}',
        "",
        "event: done",
        "data: [DONE]",
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

    app = _make_app(mock_http, responses_backend="interactions")
    async with _make_sdk_client(app) as sdk:
        stream = await sdk.responses.create(
            model="gemini-3.8-flash",
            input="Weather in Tokyo?",
            stream=True,
        )
        event_types: list[str] = []
        text_deltas: list[str] = []
        arg_deltas: list[str] = []
        completed_response = None

        async for event in stream:
            event_types.append(event.type)
            if event.type == "response.output_text.delta":
                text_deltas.append(event.delta)
            elif event.type == "response.function_call_arguments.delta":
                arg_deltas.append(event.delta)
            elif event.type == "response.completed":
                completed_response = event.response

        assert "response.created" in event_types
        assert event_types[-1] == "response.completed"
        assert "".join(text_deltas) == "Checking weather."
        assert json.loads("".join(arg_deltas)) == {"city": "Tokyo"}
        assert completed_response is not None
        assert completed_response.id == "ChA_sdk_stream"
        assert completed_response.output_text == "Checking weather."
        fc_items = [item for item in completed_response.output if item.type == "function_call"]
        assert len(fc_items) == 1
        assert fc_items[0].name == "get_weather"
        assert fc_items[0].call_id == "call_sdk_1"
        assert json.loads(fc_items[0].arguments) == {"city": "Tokyo"}


@pytest.mark.asyncio
async def test_sdk_images_and_audio():
    fake_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
    fake_pcm_b64 = base64.b64encode(b"\x00\x00" * 16).decode("ascii")

    async def fake_post(url, headers=None, json=None, timeout=None):
        if "gemini-nano-banana" in url:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"inlineData": {"mimeType": "image/png", "data": fake_png_b64}}
                                ]
                            }
                        }
                    ]
                },
            )
        if "gemini-3.8-flash-tts" in url:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "audio/L16;rate=24000",
                                            "data": fake_pcm_b64,
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "Hello from audio"}]}}]},
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    app = _make_app(mock_http)
    async with _make_sdk_client(app) as sdk:
        img = await sdk.images.generate(
            model="gemini-nano-banana-2.1",
            prompt="A minimal cube",
        )
        assert img.data is not None
        assert img.data[0].b64_json == fake_png_b64

        speech = await sdk.audio.speech.create(
            model="gemini-3.8-flash-tts",
            voice="Kore",
            input="Hello",
            response_format="wav",
        )
        wav_bytes = speech.read()
        assert wav_bytes[:4] == b"RIFF"

        transcript = await sdk.audio.transcriptions.create(
            model="gemini-3.8-flash",
            file=("hello.wav", wav_bytes, "audio/wav"),
        )
        assert transcript.text == "Hello from audio"
