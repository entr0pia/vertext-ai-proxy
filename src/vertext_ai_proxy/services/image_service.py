import base64
import logging
import time
from typing import Any

from vertext_ai_proxy.core.exceptions import OpenAIAPIException
from vertext_ai_proxy.schemas.images import (
    ImageDataObject,
    ImageGenerationRequest,
    ImageResponse,
)
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)

_STANDARD_RATIOS: list[tuple[str, float]] = [
    ("1:1", 1.0),
    ("4:3", 4.0 / 3.0),
    ("3:4", 3.0 / 4.0),
    ("16:9", 16.0 / 9.0),
    ("9:16", 9.0 / 16.0),
]

_DIRECT_SIZE_MAP: dict[str, str] = {
    "256x256": "1:1",
    "512x512": "1:1",
    "1024x1024": "1:1",
    "1536x1024": "16:9",
    "1792x1024": "16:9",
    "1024x1536": "9:16",
    "1024x1792": "9:16",
    "1024x768": "4:3",
    "768x1024": "3:4",
}


class ImageService:
    """将 OpenAI 图像生成与编辑请求路由至 Vertex AI Imagen (:predict) 或 Gemini (:generateContent)。"""

    def __init__(self, client: VertexClient) -> None:
        self.client = client

    async def generate_images(self, request: ImageGenerationRequest) -> ImageResponse:
        aspect_ratio = self._map_size_to_aspect_ratio(request.size)

        clean_model = request.model.split("/")[-1].lower()
        if clean_model.startswith("imagen-"):
            return await self._generate_with_imagen(request, aspect_ratio)
        return await self._generate_with_gemini(request, aspect_ratio)

    async def edit_images(
        self,
        model: str,
        prompt: str,
        images: list[tuple[bytes, str]],
        mask: tuple[bytes, str] | None = None,
        n: int = 1,
        size: str | None = None,
        response_format: str | None = None,
    ) -> ImageResponse:
        clean_model_input = (model or "").strip()
        clean_prompt = (prompt or "").strip()
        if not clean_model_input:
            raise OpenAIAPIException(
                status_code=400,
                message="model must not be empty.",
                code="invalid_parameter",
                param="model",
            )
        if not clean_prompt:
            raise OpenAIAPIException(
                status_code=400,
                message="prompt must not be empty.",
                code="invalid_parameter",
                param="prompt",
            )
        if not images or all(len(img_bytes) == 0 for img_bytes, _ in images):
            raise OpenAIAPIException(
                status_code=400,
                message="At least one non-empty image file is required.",
                code="invalid_parameter",
                param="image",
            )
        aspect_ratio = self._map_size_to_aspect_ratio(size)

        clean_model = clean_model_input.split("/")[-1].lower()
        if clean_model.startswith("imagen-"):
            return await self._edit_with_imagen(
                model=clean_model_input,
                prompt=clean_prompt,
                images=images,
                mask=mask,
                n=n,
                aspect_ratio=aspect_ratio,
            )
        return await self._edit_with_gemini(
            model=clean_model_input,
            prompt=clean_prompt,
            images=images,
            mask=mask,
            n=n,
            aspect_ratio=aspect_ratio,
        )

    @staticmethod
    def _map_size_to_aspect_ratio(size: str | None) -> str | None:
        if not size or not size.strip():
            return None
        raw = size.strip().lower()
        if raw in _DIRECT_SIZE_MAP:
            return _DIRECT_SIZE_MAP[raw]
        if ":" in raw:
            parts = raw.split(":", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                return raw
        if "x" in raw:
            parts = raw.split("x", 1)
            if parts[0].isdigit() and parts[1].isdigit():
                w, h = int(parts[0]), int(parts[1])
                if w > 0 and h > 0:
                    ratio = w / h
                    nearest = min(_STANDARD_RATIOS, key=lambda r: abs(r[1] - ratio))
                    return nearest[0]
        return None

    async def _generate_with_imagen(
        self,
        request: ImageGenerationRequest,
        aspect_ratio: str | None,
    ) -> ImageResponse:
        predict_url = self.client.get_predict_url(request.model)
        headers = await self.client.get_auth_headers()
        parameters: dict[str, Any] = {"sampleCount": request.n}
        if aspect_ratio:
            parameters["aspectRatio"] = aspect_ratio
        payload: dict[str, Any] = {
            "instances": [{"prompt": request.prompt}],
            "parameters": parameters,
        }
        return await self._dispatch_imagen_predict(predict_url, headers, payload)

    async def _edit_with_imagen(
        self,
        model: str,
        prompt: str,
        images: list[tuple[bytes, str]],
        mask: tuple[bytes, str] | None,
        n: int,
        aspect_ratio: str | None,
    ) -> ImageResponse:
        predict_url = self.client.get_predict_url(model)
        headers = await self.client.get_auth_headers()

        raw_bytes, _ = images[0]
        reference_images: list[dict[str, Any]] = [
            {
                "referenceType": "REFERENCE_TYPE_RAW",
                "referenceId": 1,
                "referenceImage": {
                    "bytesBase64Encoded": base64.b64encode(raw_bytes).decode("ascii")
                },
            }
        ]
        if mask and len(mask[0]) > 0:
            reference_images.append(
                {
                    "referenceType": "REFERENCE_TYPE_MASK",
                    "referenceId": 2,
                    "referenceImage": {
                        "bytesBase64Encoded": base64.b64encode(mask[0]).decode("ascii")
                    },
                    "maskImageConfig": {
                        "maskMode": "MASK_MODE_USER_PROVIDED",
                        "dilation": 0.01,
                    },
                }
            )

        parameters: dict[str, Any] = {
            "sampleCount": n,
            "editMode": "EDIT_MODE_INPAINT_INSERTION",
        }
        if aspect_ratio:
            parameters["aspectRatio"] = aspect_ratio

        payload: dict[str, Any] = {
            "instances": [
                {
                    "prompt": prompt,
                    "referenceImages": reference_images,
                }
            ],
            "parameters": parameters,
        }
        return await self._dispatch_imagen_predict(predict_url, headers, payload)

    async def _dispatch_imagen_predict(
        self,
        predict_url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> ImageResponse:
        try:
            resp = await self.client.http_client.post(
                predict_url,
                headers=headers,
                json=payload,
                timeout=self.client.config.gcp.timeout_seconds,
            )
        except Exception as e:
            logger.error(f"Failed to connect to Vertex AI Imagen endpoint: {e}")
            raise OpenAIAPIException(
                status_code=502,
                message=f"Failed to connect to Vertex AI: {str(e)}",
                code="upstream_connect_error",
                err_type="api_error",
            )

        if resp.status_code != 200:
            err_msg = self.client.extract_upstream_error_message(resp)
            logger.error(
                f"Vertex AI Imagen endpoint returned error {resp.status_code}: {err_msg}"
            )
            raise OpenAIAPIException(
                status_code=resp.status_code,
                message=f"Vertex AI image error: {err_msg}",
                code="upstream_error",
                err_type="api_error",
            )

        resp_json = resp.json()
        predictions = resp_json.get("predictions") or []
        data_objects: list[ImageDataObject] = []
        for pred in predictions:
            if not isinstance(pred, dict):
                continue
            b64 = pred.get("bytesBase64Encoded")
            if isinstance(b64, str) and b64:
                data_objects.append(
                    ImageDataObject(
                        b64_json=b64,
                        revised_prompt=pred.get("prompt"),
                    )
                )

        if not data_objects:
            raise OpenAIAPIException(
                status_code=502,
                message="Upstream Vertex AI Imagen model returned no image data.",
                code="upstream_error",
                err_type="api_error",
            )

        return ImageResponse(created=int(time.time()), data=data_objects)

    async def _generate_with_gemini(
        self,
        request: ImageGenerationRequest,
        aspect_ratio: str | None,
    ) -> ImageResponse:
        url = self.client.get_generate_content_url(request.model, stream=False)
        headers = await self.client.get_auth_headers()
        gen_config: dict[str, Any] = {
            "responseModalities": ["IMAGE", "TEXT"],
        }
        if request.n > 1:
            gen_config["candidateCount"] = request.n
        if aspect_ratio:
            gen_config["imageConfig"] = {"aspectRatio": aspect_ratio}

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": request.prompt}],
                }
            ],
            "generationConfig": gen_config,
        }
        return await self._dispatch_gemini_image(url, headers, payload)

    async def _edit_with_gemini(
        self,
        model: str,
        prompt: str,
        images: list[tuple[bytes, str]],
        mask: tuple[bytes, str] | None,
        n: int,
        aspect_ratio: str | None,
    ) -> ImageResponse:
        url = self.client.get_generate_content_url(model, stream=False)
        headers = await self.client.get_auth_headers()

        parts: list[dict[str, Any]] = [{"text": prompt}]
        for raw_bytes, mime_type in images:
            if not raw_bytes:
                continue
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type or "image/png",
                        "data": base64.b64encode(raw_bytes).decode("ascii"),
                    }
                }
            )
        if mask and len(mask[0]) > 0:
            parts.append(
                {"text": "Apply the edit strictly to the masked region shown in the following mask image:"}
            )
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mask[1] or "image/png",
                        "data": base64.b64encode(mask[0]).decode("ascii"),
                    }
                }
            )

        gen_config: dict[str, Any] = {
            "responseModalities": ["IMAGE", "TEXT"],
        }
        if n > 1:
            gen_config["candidateCount"] = n
        if aspect_ratio:
            gen_config["imageConfig"] = {"aspectRatio": aspect_ratio}

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_config,
        }
        return await self._dispatch_gemini_image(url, headers, payload)

    async def _dispatch_gemini_image(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> ImageResponse:
        try:
            resp = await self.client.http_client.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.client.config.gcp.timeout_seconds,
            )
        except Exception as e:
            logger.error(f"Failed to connect to Vertex AI Gemini image endpoint: {e}")
            raise OpenAIAPIException(
                status_code=502,
                message=f"Failed to connect to Vertex AI: {str(e)}",
                code="upstream_connect_error",
                err_type="api_error",
            )

        if resp.status_code != 200:
            err_msg = self.client.extract_upstream_error_message(resp)
            logger.error(
                f"Vertex AI Gemini image endpoint returned error {resp.status_code}: {err_msg}"
            )
            raise OpenAIAPIException(
                status_code=resp.status_code,
                message=f"Vertex AI image error: {err_msg}",
                code="upstream_error",
                err_type="api_error",
            )

        resp_json = resp.json()
        data_objects: list[ImageDataObject] = []
        for candidate in resp_json.get("candidates") or []:
            parts = (candidate.get("content") or {}).get("parts") or []
            for part in parts:
                inline = part.get("inlineData") or part.get("inline_data")
                if not isinstance(inline, dict):
                    continue
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
                b64 = inline.get("data")
                if mime.startswith("image/") and isinstance(b64, str) and b64:
                    data_objects.append(ImageDataObject(b64_json=b64))

        if not data_objects:
            raise OpenAIAPIException(
                status_code=502,
                message="Upstream Vertex AI Gemini model returned no image data.",
                code="upstream_error",
                err_type="api_error",
            )

        return ImageResponse(created=int(time.time()), data=data_objects)
