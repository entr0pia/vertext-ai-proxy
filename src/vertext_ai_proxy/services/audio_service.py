import base64
import io
import logging
from pathlib import Path
import re
from typing import Any, Literal
import wave

from fastapi import Response
from fastapi.responses import JSONResponse, PlainTextResponse

from vertext_ai_proxy.core.exceptions import OpenAIAPIException
from vertext_ai_proxy.schemas.audio import (
    SpeechRequest,
    TranscriptionResponse,
    VerboseTranscriptionResponse,
)
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)

_OPENAI_VOICE_MAP: dict[str, str] = {
    "alloy": "Puck",
    "ash": "Orus",
    "ballad": "Puck",
    "coral": "Zephyr",
    "echo": "Charon",
    "fable": "Kore",
    "nova": "Aoede",
    "onyx": "Fenrir",
    "sage": "Aoede",
    "shimmer": "Leda",
    "verse": "Fenrir",
}

_EXT_MIME_MAP: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".aac": "audio/aac",
}

_RATE_RE = re.compile(r"rate=(\d+)", re.IGNORECASE)
_SUPPORTED_STT_FORMATS: set[str] = {"json", "text", "verbose_json", "srt", "vtt"}


class AudioService:
    """基于 Vertex AI Gemini 原生多模态能力适配 OpenAI Audio (TTS / STT / Translation) 接口。"""

    def __init__(self, client: VertexClient) -> None:
        self.client = client

    async def create_speech(self, request: SpeechRequest) -> Response:
        # 不做 response_format 拦截，以模型实际返回为准；仅在显式请求 wav/pcm 且需要封装或解包时做标准库转换
        requested_fmt = (
            request.response_format.strip().lower()
            if request.response_format and request.response_format.strip()
            else None
        )

        resolved_voice = self._resolve_voice_name(request.voice)
        prompt_text = (
            f"{request.instructions.strip()}\n\n{request.input}"
            if request.instructions and request.instructions.strip()
            else request.input
        )

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt_text}],
                }
            ],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": resolved_voice,
                        }
                    }
                },
            },
        }

        url = self.client.get_generate_content_url(request.model, stream=False)
        headers = await self.client.get_auth_headers()
        resp_json = await self._post_generate_content(url, headers, payload, "TTS")

        audio_bytes, mime_type = self._extract_audio_inline_data(resp_json)
        sample_rate = self._parse_sample_rate(mime_type, default=24000)
        is_wav = (
            mime_type.lower().startswith("audio/wav")
            or mime_type.lower().startswith("audio/x-wav")
            or audio_bytes[:4] == b"RIFF"
        )
        is_raw_pcm = (
            mime_type.lower().startswith("audio/l16")
            or mime_type.lower().startswith("audio/pcm")
        )

        if requested_fmt == "wav" and is_raw_pcm:
            out_bytes = self._pcm_to_wav(audio_bytes, sample_rate=sample_rate)
            return Response(content=out_bytes, status_code=200, media_type="audio/wav")

        if requested_fmt == "pcm" and is_wav:
            out_bytes = self._wav_to_pcm(audio_bytes)
            return Response(content=out_bytes, status_code=200, media_type="audio/pcm")

        # 其余情况直接透传模型返回的音频字节与 MIME 类型
        return Response(
            content=audio_bytes,
            status_code=200,
            media_type=mime_type or "application/octet-stream",
        )

    async def create_transcription(
        self,
        model: str,
        audio_bytes: bytes,
        filename: str | None = None,
        content_type: str | None = None,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
        temperature: float | None = None,
    ) -> Response:
        return await self._process_audio_to_text(
            task="transcribe",
            model=model,
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            language=language,
            prompt=prompt,
            response_format=response_format,
            temperature=temperature,
        )

    async def create_translation(
        self,
        model: str,
        audio_bytes: bytes,
        filename: str | None = None,
        content_type: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
        temperature: float | None = None,
    ) -> Response:
        return await self._process_audio_to_text(
            task="translate",
            model=model,
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            language="english",
            prompt=prompt,
            response_format=response_format,
            temperature=temperature,
        )

    async def _process_audio_to_text(
        self,
        task: Literal["transcribe", "translate"],
        model: str,
        audio_bytes: bytes,
        filename: str | None,
        content_type: str | None,
        language: str | None,
        prompt: str | None,
        response_format: str,
        temperature: float | None,
    ) -> Response:
        clean_model = (model or "").strip()
        if not clean_model:
            raise OpenAIAPIException(
                status_code=400,
                message="model must not be empty.",
                code="invalid_parameter",
                param="model",
            )
        if not audio_bytes:
            raise OpenAIAPIException(
                status_code=400,
                message="Uploaded audio file must not be empty.",
                code="invalid_parameter",
                param="file",
            )

        fmt = (response_format or "json").strip().lower()
        mime_type = self._infer_audio_mime(filename=filename, content_type=content_type)
        instruction = self._build_audio_instruction(
            task=task,
            language=language,
            prompt=prompt,
            response_format=fmt,
        )

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": instruction},
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
        }
        if temperature is not None:
            payload["generationConfig"] = {"temperature": temperature}

        url = self.client.get_generate_content_url(clean_model, stream=False)
        headers = await self.client.get_auth_headers()
        resp_json = await self._post_generate_content(url, headers, payload, task)
        text = self._extract_text(resp_json).strip()

        if fmt == "json":
            return JSONResponse(
                status_code=200,
                content=TranscriptionResponse(text=text).model_dump(),
            )
        if fmt == "verbose_json":
            return JSONResponse(
                status_code=200,
                content=VerboseTranscriptionResponse(
                    task=task,
                    language=language or ("english" if task == "translate" else "unknown"),
                    duration=0.0,
                    text=text,
                    segments=[],
                ).model_dump(),
            )
        # text / srt / vtt
        return PlainTextResponse(content=text, status_code=200)

    async def _post_generate_content(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        try:
            resp = await self.client.http_client.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.client.config.gcp.timeout_seconds,
            )
        except Exception as e:
            logger.error(f"Failed to connect to Vertex AI audio ({operation}) endpoint: {e}")
            raise OpenAIAPIException(
                status_code=502,
                message=f"Failed to connect to Vertex AI: {str(e)}",
                code="upstream_connect_error",
                err_type="api_error",
            )

        if resp.status_code != 200:
            err_msg = self.client.extract_upstream_error_message(resp)
            logger.error(
                f"Vertex AI audio ({operation}) endpoint returned error {resp.status_code}: {err_msg}"
            )
            raise OpenAIAPIException(
                status_code=resp.status_code,
                message=f"Vertex AI audio error: {err_msg}",
                code="upstream_error",
                err_type="api_error",
            )

        return resp.json()

    @staticmethod
    def _resolve_voice_name(voice: str) -> str:
        clean = voice.strip()
        return _OPENAI_VOICE_MAP.get(clean.lower(), clean)

    @staticmethod
    def _infer_audio_mime(filename: str | None, content_type: str | None) -> str:
        if content_type:
            ct = content_type.split(";")[0].strip().lower()
            if ct.startswith("audio/"):
                return ct
        if filename:
            ext = Path(filename).suffix.lower()
            if ext in _EXT_MIME_MAP:
                return _EXT_MIME_MAP[ext]
        return "audio/wav"

    @staticmethod
    def _build_audio_instruction(
        task: Literal["transcribe", "translate"],
        language: str | None,
        prompt: str | None,
        response_format: str,
    ) -> str:
        lines: list[str] = []
        if task == "transcribe":
            lines.append(
                "Transcribe the spoken content in the attached audio verbatim. "
                "Output only the transcription without commentary."
            )
            if language and language.strip():
                lines.append(f"The spoken language is: {language.strip()}.")
        else:
            lines.append(
                "Translate the spoken content in the attached audio directly into natural English. "
                "Output only the English translation without commentary."
            )

        if response_format == "srt":
            lines.append("Format the output strictly as valid SubRip (.srt) subtitles with timestamps.")
        elif response_format == "vtt":
            lines.append("Format the output strictly as valid WebVTT (.vtt) subtitles starting with 'WEBVTT'.")

        if prompt and prompt.strip():
            lines.append(f"Context / terminology guidance: {prompt.strip()}")

        return "\n".join(lines)

    @staticmethod
    def _extract_audio_inline_data(resp_json: dict[str, Any]) -> tuple[bytes, str]:
        for candidate in resp_json.get("candidates") or []:
            parts = (candidate.get("content") or {}).get("parts") or []
            for part in parts:
                inline = part.get("inlineData") or part.get("inline_data")
                if not isinstance(inline, dict):
                    continue
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
                b64 = inline.get("data")
                if mime.lower().startswith("audio/") and isinstance(b64, str) and b64:
                    return base64.b64decode(b64), mime
        raise OpenAIAPIException(
            status_code=502,
            message="Upstream Vertex AI TTS model returned no audio data.",
            code="upstream_error",
            err_type="api_error",
        )

    @staticmethod
    def _extract_text(resp_json: dict[str, Any]) -> str:
        chunks: list[str] = []
        for candidate in resp_json.get("candidates") or []:
            parts = (candidate.get("content") or {}).get("parts") or []
            for part in parts:
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def _parse_sample_rate(mime_type: str, default: int = 24000) -> int:
        match = _RATE_RE.search(mime_type)
        if match:
            try:
                rate = int(match.group(1))
                if rate > 0:
                    return rate
            except ValueError:
                pass
        return default

    @staticmethod
    def _pcm_to_wav(
        pcm_bytes: bytes,
        sample_rate: int = 24000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return buf.getvalue()

    @staticmethod
    def _wav_to_pcm(wav_bytes: bytes) -> bytes:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.readframes(wf.getnframes())
