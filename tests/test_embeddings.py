from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
import httpx
import pytest
from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.auth import GCPTokenManager
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig, ServerConfig


@pytest.fixture
def mock_token_manager():
    tm = MagicMock(spec=GCPTokenManager)
    tm.project_id = "test-project"
    tm.location = "global"
    tm.get_access_token = AsyncMock(return_value="mock-oauth-token")
    return tm


@pytest.fixture
def app_with_mock_client(mock_token_manager):
    config = AppConfig(
        server=ServerConfig(host="127.0.0.1", port=8766),
        proxy=ProxyConfig(api_keys=["sk-test-secret-key"]),
        gcp=GCPConfig(project_id="test-project", location="global"),
    )
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    app = create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=mock_http_client,
    )
    return app, mock_http_client


def test_embeddings_unauthorized(app_with_mock_client):
    app, _ = app_with_mock_client
    client = TestClient(app)
    response = client.post("/v1/embeddings", json={"model": "text-embedding-004", "input": "test"})
    assert response.status_code == 401
    assert "error" in response.json()


def test_embeddings_single_string_success(app_with_mock_client):
    app, mock_client = app_with_mock_client
    client = TestClient(app)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "predictions": [
            {
                "embeddings": {
                    "values": [0.0123, -0.0456, 0.0789],
                    "statistics": {"token_count": 5, "truncated": False},
                }
            }
        ]
    }
    mock_client.post.return_value = mock_resp

    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={"model": "text-embedding-004", "input": "Hello world"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert data["model"] == "text-embedding-004"
    assert len(data["data"]) == 1
    assert data["data"][0]["object"] == "embedding"
    assert data["data"][0]["index"] == 0
    assert data["data"][0]["embedding"] == [0.0123, -0.0456, 0.0789]
    assert data["usage"]["prompt_tokens"] == 5
    assert data["usage"]["total_tokens"] == 5

    # 验证上游请求入参结构
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "text-embedding-004:predict" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["instances"] == [{"content": "Hello world"}]
    assert "parameters" not in payload


def test_embeddings_multiple_inputs_with_dimensions(app_with_mock_client):
    app, mock_client = app_with_mock_client
    client = TestClient(app)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "predictions": [
            {
                "embeddings": {
                    "values": [0.1, 0.2],
                    "statistics": {"token_count": 3, "truncated": False},
                }
            },
            {
                "embeddings": {
                    "values": [0.3, 0.4],
                    "statistics": {"token_count": 4, "truncated": False},
                }
            },
        ]
    }
    mock_client.post.return_value = mock_resp

    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={
            "model": "google/gemini-embedding-001",
            "input": ["First document", "Second document"],
            "dimensions": 256,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["data"][0]["index"] == 0
    assert data["data"][1]["index"] == 1
    assert data["usage"]["total_tokens"] == 7

    call_args = mock_client.post.call_args
    assert "gemini-embedding-001:predict" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["instances"] == [
        {"content": "First document"},
        {"content": "Second document"},
    ]
    assert payload["parameters"] == {"outputDimensionality": 256}


def test_embeddings_empty_input_rejected(app_with_mock_client):
    app, _ = app_with_mock_client
    client = TestClient(app)

    # 空字符串校验
    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={"model": "text-embedding-004", "input": "   "},
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"

    # 空数组校验
    response2 = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={"model": "text-embedding-004", "input": []},
    )
    assert response2.status_code == 400


def test_embeddings_upstream_error_handling(app_with_mock_client):
    app, mock_client = app_with_mock_client
    client = TestClient(app)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_resp.text = '{"error": {"message": "Model not found"}}'
    mock_resp.json.return_value = {"error": {"message": "Model not found"}}
    mock_client.post.return_value = mock_resp

    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={"model": "non-existent-model", "input": "Hello"},
    )

    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "Model not found" in data["error"]["message"]


def test_gemini_embedding_2_single_string_success(app_with_mock_client):
    app, mock_client = app_with_mock_client
    client = TestClient(app)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "embedding": {
            "values": [0.05, -0.02, 0.08, 0.12],
        }
    }
    mock_client.post.return_value = mock_resp

    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={"model": "gemini-embedding-2", "input": "Multimodal text query"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert data["model"] == "gemini-embedding-2"
    assert len(data["data"]) == 1
    assert data["data"][0]["index"] == 0
    assert data["data"][0]["embedding"] == [0.05, -0.02, 0.08, 0.12]
    assert data["usage"]["prompt_tokens"] > 0
    assert data["usage"]["total_tokens"] > 0

    # 验证 URL 与请求体结构
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "gemini-embedding-2:embedContent" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["content"]["parts"] == [{"text": "Multimodal text query"}]
    assert "outputDimensionality" not in payload


def test_gemini_embedding_2_batch_with_dimensions(app_with_mock_client):
    app, mock_client = app_with_mock_client
    client = TestClient(app)

    mock_resp1 = MagicMock(spec=httpx.Response)
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {"embedding": {"values": [0.1, 0.2]}}

    mock_resp2 = MagicMock(spec=httpx.Response)
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {"embedding": {"values": [0.3, 0.4]}}

    mock_client.post.side_effect = [mock_resp1, mock_resp2]

    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={
            "model": "google/gemini-embedding-2",
            "input": ["First sentence", "Second sentence"],
            "dimensions": 768,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2
    assert data["data"][0]["index"] == 0
    assert data["data"][0]["embedding"] == [0.1, 0.2]
    assert data["data"][1]["index"] == 1
    assert data["data"][1]["embedding"] == [0.3, 0.4]

    assert mock_client.post.call_count == 2
    call_args_1 = mock_client.post.call_args_list[0]
    assert "gemini-embedding-2:embedContent" in call_args_1[0][0]
    payload1 = call_args_1[1]["json"]
    assert payload1["content"]["parts"] == [{"text": "First sentence"}]
    assert payload1["outputDimensionality"] == 768


def test_gemini_embedding_2_upstream_error_handling(app_with_mock_client):
    app, mock_client = app_with_mock_client
    client = TestClient(app)

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 400
    mock_resp.text = '{"error": {"message": "Invalid input format"}}'
    mock_resp.json.return_value = {"error": {"message": "Invalid input format"}}
    mock_client.post.return_value = mock_resp

    response = client.post(
        "/v1/embeddings",
        headers={"Authorization": "Bearer sk-test-secret-key"},
        json={"model": "gemini-embedding-2", "input": "Faulty request"},
    )

    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert "Invalid input format" in data["error"]["message"]
