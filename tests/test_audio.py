import base64
import io
from unittest.mock import AsyncMock, MagicMock
import wave

from fastapi.testclient import TestClient
import httpx
import pytest

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import AppConfig, GCPConfig, ProxyConfig


def _make_client(mock_http: MagicMock) -> TestClient:
    config = AppConfig(
        proxy=ProxyConfig(api_keys=["sk-audio-test-key"]),
        gcp=GCPConfig(project_id="test-proj", location="global"),
    )
    mock_token_manager = MagicMock()
    mock_token_manager.project_id = "test-proj"
    mock_token_manager.location = "global"
    mock_token_manager.get_access_token = AsyncMock(return_value="mock-gcp-token")

    app = create_app(
        config=config,
        token_manager=mock_token_manager,
        http_client=mock_http,
    )
    return TestClient(app)


def _make_valid_wav(pcm_frames: bytes, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_frames)
    return buf.getvalue()


def test_audio_speech_explicit_wav_wraps_pcm_and_default_passes_through():
    raw_pcm = b"\x00\x01\x02\x03" * 200
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
                                {
                                    "inlineData": {
                                        "mimeType": "audio/L16;codec=pcm;rate=24000",
                                        "data": base64.b64encode(raw_pcm).decode("ascii"),
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
    # 1. Omitted response_format passes through model's exact bytes & mimeType
    resp_default = client.post(
        "/v1/audio/speech",
        headers={"Authorization": "Bearer sk-audio-test-key"},
        json={
            "model": "gemini-3.8-flash-tts",
            "input": "Hello from Vertex AI!",
            "voice": "alloy",
        },
    )
    assert resp_default.status_code == 200
    assert resp_default.headers["content-type"].lower().startswith("audio/l16")
    assert resp_default.content == raw_pcm

    # 2. Explicit response_format="wav" wraps raw L16 PCM into valid RIFF WAV
    resp_wav = client.post(
        "/v1/audio/speech",
        headers={"Authorization": "Bearer sk-audio-test-key"},
        json={
            "model": "gemini-3.8-flash-tts",
            "input": "Hello from Vertex AI!",
            "voice": "alloy",
            "response_format": "wav",
        },
    )
    assert resp_wav.status_code == 200
    assert resp_wav.headers["content-type"].startswith("audio/wav")
    assert resp_wav.content[:4] == b"RIFF"

    with wave.open(io.BytesIO(resp_wav.content), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24000
        assert wf.readframes(wf.getnframes()) == raw_pcm

    voice_name = captured["json"]["generationConfig"]["speechConfig"]["voiceConfig"][
        "prebuiltVoiceConfig"
    ]["voiceName"]
    assert voice_name == "Puck"
    assert captured["url"].endswith("/models/gemini-3.8-flash-tts:generateContent")


def test_audio_speech_extracts_pcm_from_upstream_wav():
    raw_pcm = b"\x10\x20\x30\x40" * 100
    upstream_wav = _make_valid_wav(raw_pcm, sample_rate=24000)
    captured: dict = {}

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/wav",
                                        "data": base64.b64encode(upstream_wav).decode("ascii"),
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
    resp = client.post(
        "/v1/audio/speech",
        headers={"Authorization": "Bearer sk-audio-test-key"},
        json={
            "model": "gemini-3.8-flash-tts",
            "input": "Raw PCM test",
            "voice": "Kore",
            "response_format": "pcm",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/pcm")
    assert resp.content == raw_pcm
    voice_name = captured["json"]["generationConfig"]["speechConfig"]["voiceConfig"][
        "prebuiltVoiceConfig"
    ]["voiceName"]
    assert voice_name == "Kore"


@pytest.mark.parametrize("req_fmt", ["mp3", "opus", "aac", "flac"])
def test_audio_speech_passes_through_without_blocking_format(req_fmt: str):
    upstream_wav = _make_valid_wav(b"\x01\x02" * 50, sample_rate=24000)
    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/wav",
                                        "data": base64.b64encode(upstream_wav).decode("ascii"),
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )
    mock_http.aclose = AsyncMock()
    client = _make_client(mock_http)

    resp = client.post(
        "/v1/audio/speech",
        headers={"Authorization": "Bearer sk-audio-test-key"},
        json={
            "model": "gemini-3.8-flash-tts",
            "input": "Hello",
            "voice": "Kore",
            "response_format": req_fmt,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")
    assert resp.content == upstream_wav


def test_audio_transcription_formats_and_mime_inference():
    captured: list[dict] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured.append({"url": url, "json": json})
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "Hello world"}]}}]},
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = _make_client(mock_http)
    headers = {"Authorization": "Bearer sk-audio-test-key"}

    # JSON format + extension mime inference
    r_json = client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "gemini-3.8-flash", "language": "en", "response_format": "json"},
        files={"file": ("clip.mp3", b"fake-mp3-data", "application/octet-stream")},
    )
    assert r_json.status_code == 200
    assert r_json.json() == {"text": "Hello world"}
    inline_data = captured[0]["json"]["contents"][0]["parts"][1]["inlineData"]
    assert inline_data["mimeType"] == "audio/mpeg"

    # Plain text format
    r_text = client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "gemini-3.8-flash", "response_format": "text"},
        files={"file": ("clip.wav", b"fake-wav-data", "audio/wav")},
    )
    assert r_text.status_code == 200
    assert r_text.text == "Hello world"

    # Verbose JSON format
    r_verbose = client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "gemini-3.8-flash", "language": "en", "response_format": "verbose_json"},
        files={"file": ("clip.wav", b"fake-wav-data", "audio/wav")},
    )
    assert r_verbose.status_code == 200
    vb = r_verbose.json()
    assert vb["task"] == "transcribe"
    assert vb["language"] == "en"
    assert vb["text"] == "Hello world"


def test_audio_translation_and_empty_file_rejection():
    captured_urls: list[str] = []

    async def fake_post(url, headers=None, json=None, timeout=None):
        captured_urls.append(url)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "Good morning"}]}}]},
        )

    mock_http = MagicMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=fake_post)
    mock_http.aclose = AsyncMock()

    client = _make_client(mock_http)
    headers = {"Authorization": "Bearer sk-audio-test-key"}

    r_tr = client.post(
        "/v1/audio/translations",
        headers=headers,
        data={"model": "gemini-3.8-flash"},
        files={"file": ("cn.wav", b"fake-audio", "audio/wav")},
    )
    assert r_tr.status_code == 200
    assert r_tr.json() == {"text": "Good morning"}

    # Empty upload -> 400
    r_empty = client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "gemini-3.8-flash"},
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert r_empty.status_code == 400
    assert r_empty.json()["error"]["code"] == "invalid_parameter"

    # Explicit alias not rewritten
    r_alias = client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "whisper-1"},
        files={"file": ("clip.wav", b"fake-audio", "audio/wav")},
    )
    assert r_alias.status_code == 200
    assert "/models/whisper-1:generateContent" in captured_urls[-1]
