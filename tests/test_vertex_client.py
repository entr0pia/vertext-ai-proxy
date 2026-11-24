from unittest.mock import MagicMock
import httpx

from vertext_ai_proxy.core.config import AppConfig, GCPConfig
from vertext_ai_proxy.services.vertex_client import VertexClient


def _make_client(location: str = "global", project_id: str = "test-proj") -> VertexClient:
    config = AppConfig(gcp=GCPConfig(project_id=project_id, location=location))
    token_manager = MagicMock()
    token_manager.project_id = project_id
    token_manager.location = location
    http_client = MagicMock(spec=httpx.AsyncClient)
    return VertexClient(config=config, token_manager=token_manager, http_client=http_client)


def test_get_generate_content_url_global_and_strip_prefix():
    client = _make_client(location="global", project_id="demo-proj")
    assert client.get_generate_content_url("google/gemini-3.8-flash") == (
        "https://aiplatform.googleapis.com/v1/projects/demo-proj"
        "/locations/global/publishers/google/models/gemini-3.8-flash:generateContent"
    )
    assert client.get_generate_content_url("gemini-3.8-flash", stream=True) == (
        "https://aiplatform.googleapis.com/v1/projects/demo-proj"
        "/locations/global/publishers/google/models/gemini-3.8-flash:streamGenerateContent?alt=sse"
    )


def test_get_generate_content_url_regional_override():
    client = _make_client(location="global", project_id="demo-proj")
    assert client.get_generate_content_url("google/gemini-3.8-flash", location="us-central1") == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/demo-proj"
        "/locations/us-central1/publishers/google/models/gemini-3.8-flash:generateContent"
    )


def test_get_publisher_models_url_respects_location():
    global_client = _make_client(location="global", project_id="demo-proj")
    assert global_client.get_publisher_models_url() == (
        "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"
    )
    assert global_client.get_publisher_models_url(location="us-central1") == (
        "https://us-central1-aiplatform.googleapis.com/v1beta1/publishers/google/models"
    )

    regional_client = _make_client(location="us-central1", project_id="demo-proj")
    assert regional_client.get_publisher_models_url() == (
        "https://us-central1-aiplatform.googleapis.com/v1beta1/publishers/google/models"
    )


def test_extract_upstream_error_message_dict_list_and_plain():
    resp_dict = httpx.Response(
        400,
        json={"error": {"code": 400, "message": "Invalid argument provided"}},
    )
    assert VertexClient.extract_upstream_error_message(resp_dict) == "Invalid argument provided"

    resp_list = httpx.Response(
        404,
        json=[{"error": {"code": 404, "message": "Model not found"}}],
    )
    assert VertexClient.extract_upstream_error_message(resp_list) == "Model not found"

    resp_plain = httpx.Response(502, text="Bad Gateway")
    assert VertexClient.extract_upstream_error_message(resp_plain) == "Bad Gateway"
