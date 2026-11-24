from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
import httpx

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig
from vertext_ai_proxy.models import normalize_model_name


def _create_test_client_with_models(publisher_models: list[dict[str, str]]) -> TestClient:
    config = AppConfig(
        proxy=ProxyConfig(
            api_keys=["sk-test-models-key"],
            models=["gemini-3.8-flash"],
        ),
        gcp=GCPConfig(project_id="test-proj", location="global"),
    )
    mock_token_manager = MagicMock()
    mock_token_manager.project_id = "test-proj"
    mock_token_manager.location = "global"
    mock_token_manager.get_access_token = AsyncMock(return_value="mock-gcp-token")

    mock_http_client = MagicMock(spec=httpx.AsyncClient)
    mock_http_client.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"publisherModels": publisher_models},
        )
    )
    mock_http_client.aclose = AsyncMock()

    app = create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=mock_http_client,
    )
    return TestClient(app)


def test_normalize_model_name_auto_prefixes():
    assert normalize_model_name("gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert normalize_model_name("gemini-1.5-pro") == "google/gemini-1.5-pro"
    assert normalize_model_name("learnlm-1.5-pro") == "google/learnlm-1.5-pro"
    assert normalize_model_name("imagen-3.0-generate-002") == "google/imagen-3.0-generate-002"


def test_normalize_model_name_preserves_existing_prefix():
    assert normalize_model_name("google/gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert normalize_model_name("meta/llama-3-70b") == "meta/llama-3-70b"


def test_normalize_model_name_respects_flag():
    assert (
        normalize_model_name("gemini-2.5-flash", auto_prefix=False)
        == "gemini-2.5-flash"
    )


def test_retrieve_single_model_unprefixed_and_prefixed():
    client = _create_test_client_with_models(
        [{"name": "publishers/google/models/gemini-3.8-flash"}]
    )
    headers = {"Authorization": "Bearer sk-test-models-key"}

    resp_short = client.get("/v1/models/gemini-3.8-flash", headers=headers)
    assert resp_short.status_code == 200
    body_short = resp_short.json()
    assert body_short["id"] == "gemini-3.8-flash"
    assert body_short["object"] == "model"
    assert body_short["owned_by"] == "google"

    resp_prefixed = client.get("/v1/models/google/gemini-3.8-flash", headers=headers)
    assert resp_prefixed.status_code == 200
    body_prefixed = resp_prefixed.json()
    assert body_prefixed["id"] == "google/gemini-3.8-flash"
    assert body_prefixed["object"] == "model"


def test_retrieve_nonexistent_model_returns_404():
    client = _create_test_client_with_models(
        [{"name": "publishers/google/models/gemini-3.8-flash"}]
    )
    headers = {"Authorization": "Bearer sk-test-models-key"}

    resp = client.get("/v1/models/does-not-exist-999", headers=headers)
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "model_not_found"
    assert err["type"] == "invalid_request_error"
    assert "does-not-exist-999" in err["message"]


def test_retrieve_model_requires_auth():
    client = _create_test_client_with_models(
        [{"name": "publishers/google/models/gemini-3.8-flash"}]
    )
    resp = client.get("/v1/models/gemini-3.8-flash")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


def test_list_models_respects_regional_location_and_paginates():
    config = AppConfig(
        proxy=ProxyConfig(
            api_keys=["sk-test-models-key"],
            models=[],
        ),
        gcp=GCPConfig(project_id="test-proj", location="us-central1"),
    )
    mock_token_manager = MagicMock()
    mock_token_manager.project_id = "test-proj"
    mock_token_manager.location = "us-central1"
    mock_token_manager.get_access_token = AsyncMock(return_value="mock-gcp-token")

    mock_http_client = MagicMock(spec=httpx.AsyncClient)
    mock_http_client.get = AsyncMock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "publisherModels": [
                        {"name": "publishers/google/models/gemini-3.8-flash"},
                    ],
                    "nextPageToken": "page-2-token",
                },
            ),
            httpx.Response(
                200,
                json={
                    "publisherModels": [
                        {"name": "publishers/google/models/gemini-embedding-001"},
                    ],
                },
            ),
        ]
    )
    mock_http_client.aclose = AsyncMock()

    app = create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=mock_http_client,
    )
    client = TestClient(app)
    resp = client.get("/v1/models", headers={"Authorization": "Bearer sk-test-models-key"})
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["data"]}
    assert "gemini-3.8-flash" in ids
    assert "google/gemini-3.8-flash" in ids
    assert "gemini-embedding-001" in ids
    assert "google/gemini-embedding-001" in ids

    assert mock_http_client.get.call_count == 2
    first_call = mock_http_client.get.call_args_list[0]
    second_call = mock_http_client.get.call_args_list[1]
    assert first_call.args[0] == "https://us-central1-aiplatform.googleapis.com/v1beta1/publishers/google/models"
    assert first_call.kwargs["params"] == {"pageSize": "100"}
    assert second_call.args[0] == "https://us-central1-aiplatform.googleapis.com/v1beta1/publishers/google/models"
    assert second_call.kwargs["params"] == {"pageSize": "100", "pageToken": "page-2-token"}
