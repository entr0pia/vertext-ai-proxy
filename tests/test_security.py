import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from vertext_ai_proxy.config import AppConfig, ProxyConfig
from vertext_ai_proxy.security import (
    APIKeyValidator,
    OpenAIAPIException,
    openai_exception_handler,
)


@pytest.fixture
def test_app():
    config = AppConfig(
        proxy=ProxyConfig(
            api_keys=["sk-valid-key-1", "sk-valid-key-2"],
        )
    )
    validator = APIKeyValidator(config)

    app = FastAPI()
    app.add_exception_handler(OpenAIAPIException, openai_exception_handler)

    @app.get("/test-protected")
    def protected_route(token: str = Depends(validator)):
        return {"status": "ok", "authenticated_with": token}

    return app


def test_missing_auth_header(test_app):
    client = TestClient(test_app)
    response = client.get("/test-protected")
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "invalid_api_key"
    assert "Missing API key" in data["error"]["message"]


def test_invalid_auth_key(test_app):
    client = TestClient(test_app)
    response = client.get(
        "/test-protected", headers={"Authorization": "Bearer sk-wrong-key"}
    )
    assert response.status_code == 401
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "invalid_api_key"
    assert "Invalid API key" in data["error"]["message"]


def test_valid_bearer_token(test_app):
    client = TestClient(test_app)
    response = client.get(
        "/test-protected", headers={"Authorization": "Bearer sk-valid-key-1"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authenticated_with": "sk-valid-key-1"}


def test_valid_second_token(test_app):
    client = TestClient(test_app)
    response = client.get(
        "/test-protected", headers={"Authorization": "Bearer sk-valid-key-2"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authenticated_with": "sk-valid-key-2"}


def test_valid_api_key_header(test_app):
    client = TestClient(test_app)
    response = client.get(
        "/test-protected", headers={"api-key": "sk-valid-key-2"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authenticated_with": "sk-valid-key-2"}
