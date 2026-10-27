from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from fastapi.testclient import TestClient
from google.oauth2 import service_account

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.auth import GCPTokenManager
from vertext_ai_proxy.config import AppConfig, GCPConfig, ProxyConfig, ServerConfig


def create_mock_manager() -> GCPTokenManager:
    creds = MagicMock(spec=service_account.Credentials)
    creds.project_id = "test-project-123"
    creds.token = "mock-gcp-bearer-token"
    creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    creds.valid = True
    config = GCPConfig(location="global", timeout_seconds=30)
    return GCPTokenManager(config, credentials=creds)


@pytest.fixture
def test_setup():
    config = AppConfig(
        server=ServerConfig(port=8766),
        proxy=ProxyConfig(
            api_keys=["sk-test-client-key"],
            auto_prefix_model=True,
            models=["gemini-2.5-flash"],
        ),
        gcp=GCPConfig(location="global"),
    )
    token_manager = create_mock_manager()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # Mock upstream /v1beta1/publishers/google/models response
    mock_client.get.return_value = httpx.Response(
        status_code=200,
        json={
            "publisherModels": [
                {"name": "publishers/google/models/gemini-2.5-flash"},
                {"name": "publishers/google/models/gemini-1.5-pro"},
            ]
        },
        headers={"content-type": "application/json"},
    )

    app = create_app(
        config=config,
        token_manager=token_manager,
        http_client=mock_client,
    )
    return app, mock_client


def test_healthz_endpoint(test_setup):
    app, _ = test_setup
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["project_id"] == "test-project-123"
        assert data["location"] == "global"


def test_models_endpoint_auth_and_structure(test_setup):
    app, mock_client = test_setup
    with TestClient(app) as client:
        # Without auth -> 401
        res_unauth = client.get("/v1/models")
        assert res_unauth.status_code == 401

        # With auth -> 200
        res_auth = client.get(
            "/v1/models", headers={"Authorization": "Bearer sk-test-client-key"}
        )
        assert res_auth.status_code == 200
        data = res_auth.json()
        assert data["object"] == "list"
        ids = {item["id"] for item in data["data"]}
        assert "gemini-2.5-flash" in ids
        assert "google/gemini-2.5-flash" in ids
        assert "gemini-1.5-pro" in ids

        # Verify upstream call targeted publisher models
        mock_client.get.assert_called_once()
        target_url = mock_client.get.call_args[0][0]
        assert "publishers/google/models" in target_url


@pytest.mark.asyncio
async def test_chat_completions_non_streaming():
    config = AppConfig(
        proxy=ProxyConfig(api_keys=["sk-test-key"], auto_prefix_model=True),
        gcp=GCPConfig(location="global"),
    )
    token_manager = create_mock_manager()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    # Mock upstream response
    mock_upstream_resp = httpx.Response(
        status_code=200,
        json={"id": "chatcmpl-1", "choices": [{"message": {"content": "Hello world!"}}]},
        headers={"content-type": "application/json"},
    )
    mock_client.post.return_value = mock_upstream_resp

    app = create_app(config=config, token_manager=token_manager, http_client=mock_client)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "hi"}],
        }
        res = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer sk-test-key"},
        )
        assert res.status_code == 200
        assert res.json()["choices"][0]["message"]["content"] == "Hello world!"

        # Verify upstream call
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        target_url = call_args[0][0]
        assert "aiplatform.googleapis.com" in target_url
        assert "/endpoints/openapi/chat/completions" in target_url

        # Verify Authorization header replaced with GCP bearer token
        sent_headers = call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer mock-gcp-bearer-token"

        # Verify model name was auto-prefixed with google/
        sent_payload = call_args.kwargs["json"]
        assert sent_payload["model"] == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_chat_completions_streaming():
    config = AppConfig(
        proxy=ProxyConfig(api_keys=["sk-test-key"], auto_prefix_model=True),
        gcp=GCPConfig(location="global"),
    )
    token_manager = create_mock_manager()

    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # Mock SSE stream
    async def fake_aiter_bytes():
        yield b"data: {\"choices\": [{\"delta\": {\"content\": \"chunk1\"}}]}\n\n"
        yield b"data: [DONE]\n\n"

    mock_stream_resp = MagicMock(spec=httpx.Response)
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"content-type": "text/event-stream"}
    mock_stream_resp.aiter_bytes = fake_aiter_bytes
    mock_stream_resp.aclose = AsyncMock()

    mock_client.send.return_value = mock_stream_resp
    mock_client.build_request.return_value = MagicMock()

    app = create_app(config=config, token_manager=token_manager, http_client=mock_client)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "model": "google/gemini-1.5-pro",
            "messages": [{"role": "user", "content": "stream"}],
            "stream": True,
        }
        res = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer sk-test-key"},
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        body = await res.aread()
        assert b"chunk1" in body
        assert b"[DONE]" in body
