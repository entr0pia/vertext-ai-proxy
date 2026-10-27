import logging
from collections.abc import AsyncGenerator
from typing import Any
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from vertext_ai_proxy.models import normalize_model_name
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)


class ChatService:
    """处理下游 Chat Completions 请求向 Vertex AI OpenAPI 兼容端点的转发与流式透传。"""

    def __init__(self, client: VertexClient) -> None:
        self.client = client

    async def forward_chat_completions(
        self,
        request: Request,
        payload: dict[str, Any],
    ) -> Response:
        # 1. 模型名规范化：自动补全 google/ 前缀
        if "model" in payload and isinstance(payload["model"], str):
            original_model = payload["model"]
            payload["model"] = normalize_model_name(
                original_model, auto_prefix=self.client.config.proxy.auto_prefix_model
            )
            if payload["model"] != original_model:
                logger.debug(
                    f"Normalized model: '{original_model}' -> '{payload['model']}'"
                )

        target_url = self.client.get_openapi_url("chat/completions")
        access_token = await self.client.token_manager.get_access_token()

        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }

        is_streaming = bool(payload.get("stream", False))
        logger.debug(
            f"Forwarding chat request to: {target_url} (streaming={is_streaming}, model={payload.get('model')})"
        )

        if is_streaming:
            return await self._handle_streaming(target_url, headers, payload)
        else:
            return await self._handle_non_streaming(target_url, headers, payload)

    async def _handle_streaming(
        self,
        target_url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> Response:
        req = self.client.http_client.build_request(
            "POST",
            target_url,
            headers=headers,
            json=payload,
            timeout=self.client.config.gcp.timeout_seconds,
        )
        upstream_response = await self.client.http_client.send(req, stream=True)

        if upstream_response.status_code != 200:
            error_body = await upstream_response.aread()
            await upstream_response.aclose()
            logger.error(
                f"Upstream returned error {upstream_response.status_code}: {error_body.decode('utf-8', errors='replace')}"
            )
            return Response(
                content=error_body,
                status_code=upstream_response.status_code,
                media_type="application/json",
            )

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk
            except Exception as exc:
                logger.error(f"Error during SSE stream forwarding: {exc}")
            finally:
                await upstream_response.aclose()

        return StreamingResponse(
            stream_generator(),
            status_code=200,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def _handle_non_streaming(
        self,
        target_url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> Response:
        resp = await self.client.http_client.post(
            target_url,
            headers=headers,
            json=payload,
            timeout=self.client.config.gcp.timeout_seconds,
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
