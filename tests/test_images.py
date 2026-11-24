import base64
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import httpx

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig


def _make_client(mock_http: MagicMock) -> TestClient:
    config = AppConfig(
        proxy=ProxyConfig(api_keys=["sk-img-test-key"]),
        gcp=GCPConfig(project_id="test-proj", location="us-central1"),
    )
    mock_token_manager = MagicMock()
    mock_token_manager.project_id = "test-proj"
    mock_token_manager.location = "us-central1"
    mock_token_manager.get_access_token = AsyncMock(return_value="mock-gcp-token")

    app = create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=mock_http,
    )
    return TestClient(app)


def test_images_generation_imagen_routes_to_predict():
    captured: dict = {}

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(
            200,
            json={
                "predictions": [
                    {"bytesBase64Encoded": base64.b64encode(b"fake-png-1").decode("ascii")},
                    {"bytesBase64Encoded": base64.b64encode(b"fake-png-2").decode("ascii")},
                ]
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = _make_client(mock_http)
    resp = client.post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer sk-img-test-key"},
        json={
            "model": "imagen-3.0-generate-002",
            "prompt": "A serene watercolor mountain",
            "n": 2,
            "size": "1536x1024",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert base64.b64decode(body["data"][0]["b64_json"]) == b"fake-png-1"
    assert captured["url"].endswith("/models/imagen-3.0-generate-002:predict")
    assert captured["json"]["parameters"]["sampleCount"] == 2
    assert captured["json"]["parameters"]["aspectRatio"] == "16:9"


def test_images_generation_gemini_routes_to_generate_content():
    captured: dict = {}

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Here is your image."},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(b"gemini-img").decode("ascii"),
                                    }
                                },
                            ]
                        }
                    }
                ]
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = _make_client(mock_http)
    resp = client.post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer sk-img-test-key"},
        json={
            "model": "google/gemini-nano-banana-2.1",
            "prompt": "A minimalist desk icon",
            "size": "1024x1024",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert base64.b64decode(body["data"][0]["b64_json"]) == b"gemini-img"
    assert captured["url"].endswith("/models/gemini-nano-banana-2.1:generateContent")
    assert "IMAGE" in captured["json"]["generationConfig"]["responseModalities"]
    assert captured["json"]["generationConfig"]["imageConfig"]["aspectRatio"] == "1:1"


def test_images_edits_imagen_with_mask_and_gemini_multipart():
    calls: list[tuple[str, dict]] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        if url.endswith(":predict"):
            return httpx.Response(
                200,
                json={
                    "predictions": [
                        {"bytesBase64Encoded": base64.b64encode(b"edited-imagen").decode("ascii")}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(b"edited-gemini").decode("ascii"),
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = _make_client(mock_http)
    headers = {"Authorization": "Bearer sk-img-test-key"}

    # 1. Imagen edit with mask
    r_imagen = client.post(
        "/v1/images/edits",
        headers=headers,
        data={"model": "imagen-3.0-capability-001", "prompt": "Add a red hat"},
        files={
            "image": ("src.png", b"raw-img-bytes", "image/png"),
            "mask": ("mask.png", b"mask-img-bytes", "image/png"),
        },
    )
    assert r_imagen.status_code == 200
    assert base64.b64decode(r_imagen.json()["data"][0]["b64_json"]) == b"edited-imagen"
    assert calls[0][0].endswith("/models/imagen-3.0-capability-001:predict")
    ref_imgs = calls[0][1]["instances"][0]["referenceImages"]
    assert len(ref_imgs) == 2
    assert ref_imgs[0]["referenceType"] == "REFERENCE_TYPE_RAW"
    assert ref_imgs[1]["referenceType"] == "REFERENCE_TYPE_MASK"

    # 2. Gemini edit
    r_gemini = client.post(
        "/v1/images/edits",
        headers=headers,
        data={"model": "gemini-nano-banana-2.1", "prompt": "Make background sunny"},
        files={"image": ("src.png", b"raw-img-bytes", "image/png")},
    )
    assert r_gemini.status_code == 200
    assert base64.b64decode(r_gemini.json()["data"][0]["b64_json"]) == b"edited-gemini"
    assert calls[1][0].endswith("/models/gemini-nano-banana-2.1:generateContent")
    parts = calls[1][1]["contents"][0]["parts"]
    assert parts[0]["text"] == "Make background sunny"
    assert parts[1]["inlineData"]["mimeType"] == "image/png"


def test_images_does_not_block_response_format_url():
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "predictions": [
                    {"bytesBase64Encoded": base64.b64encode(b"model-img").decode("ascii")}
                ]
            },
        )
    )
    mock_http.aclose = AsyncMock()
    client = _make_client(mock_http)

    resp = client.post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer sk-img-test-key"},
        json={
            "model": "imagen-3.0-generate-002",
            "prompt": "A cat",
            "response_format": "url",
        },
    )
    assert resp.status_code == 200
    assert base64.b64decode(resp.json()["data"][0]["b64_json"]) == b"model-img"


def test_images_does_not_rewrite_openai_alias():
    captured_urls: list[str] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured_urls.append(url)
        return httpx.Response(404, json={"error": {"message": "Model dall-e-3 not found"}})

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()
    client = _make_client(mock_http)

    resp = client.post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer sk-img-test-key"},
        json={"model": "dall-e-3", "prompt": "A cat"},
    )
    assert resp.status_code == 404
    assert "/models/dall-e-3:generateContent" in captured_urls[0]
