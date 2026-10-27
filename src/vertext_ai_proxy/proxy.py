import logging
from collections.abc import AsyncGenerator
import time
from typing import Any
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from vertext_ai_proxy.auth import GCPTokenManager
from vertext_ai_proxy.config import AppConfig
from vertext_ai_proxy.models import normalize_model_name

logger = logging.getLogger(__name__)


class VertexAIProxy:
    """处理下游请求向上游 Vertex AI OpenAPI 兼容端点的转发与流式透传。"""

    def __init__(
        self,
        config: AppConfig,
        token_manager: GCPTokenManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.token_manager = token_manager
        self.http_client = http_client
        self._cached_models_response: dict[str, Any] | None = None
        self._models_cache_expiry: float = 0.0

    async def forward_models(self, request: Request) -> Response:
        """从 Google Cloud Vertex AI PublisherModel API 动态拉取模型列表。

        通过 Google Cloud Vertex AI 的 v1beta1/publishers/google/models 端点，
        100% 动态实时获取当前 Google 官方开放的模型，并在内存中缓存 1 小时以优化响应性能。
        发生异常时优雅回退至本地预设列表，保障客户端永不 404。
        """
        now = time.time()
        if self._cached_models_response and now < self._models_cache_expiry:
            return JSONResponse(status_code=200, content=self._cached_models_response)

        try:
            access_token = await self.token_manager.get_access_token()
            publisher_url = "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            logger.debug(f"Fetching dynamic publisher models from: {publisher_url}")
            resp = await self.http_client.get(
                publisher_url,
                headers=headers,
                timeout=self.config.gcp.timeout_seconds,
            )

            if resp.status_code == 200:
                raw_json = resp.json()
                models_data = []
                created_ts = int(now)
                seen_ids: set[str] = set()

                for item in raw_json.get("publisherModels", []):
                    full_name = item.get("name", "")
                    if not full_name:
                        continue
                    # 提取短模型名称，如 "gemini-2.5-flash"
                    short_id = full_name.split("/")[-1]
                    canonical_id = f"google/{short_id}"

                    if short_id not in seen_ids:
                        seen_ids.add(short_id)
                        models_data.append(
                            {
                                "id": short_id,
                                "object": "model",
                                "created": created_ts,
                                "owned_by": "google",
                                "permission": [],
                                "root": canonical_id,
                                "parent": None,
                            }
                        )
                    if canonical_id not in seen_ids:
                        seen_ids.add(canonical_id)
                        models_data.append(
                            {
                                "id": canonical_id,
                                "object": "model",
                                "created": created_ts,
                                "owned_by": "google",
                                "permission": [],
                                "root": canonical_id,
                                "parent": None,
                            }
                        )

                # 合并配置中可能额外声明的模型
                for custom_m in self.config.proxy.models:
                    clean_m = custom_m.strip()
                    if clean_m and clean_m not in seen_ids:
                        seen_ids.add(clean_m)
                        models_data.append(
                            {
                                "id": clean_m,
                                "object": "model",
                                "created": created_ts,
                                "owned_by": "google",
                                "permission": [],
                                "root": clean_m,
                                "parent": None,
                            }
                        )

                result = {"object": "list", "data": models_data}
                self._cached_models_response = result
                self._models_cache_expiry = now + 3600  # 缓存 1 小时
                return JSONResponse(status_code=200, content=result)
            else:
                logger.warning(
                    f"Upstream publisher models returned status {resp.status_code}, falling back to config models."
                )
        except Exception as e:
            logger.warning(
                f"Failed to fetch dynamic publisher models: {e}, falling back to config models."
            )

        # 优雅回退保障
        fallback_data = [
            {
                "id": m,
                "object": "model",
                "created": int(now),
                "owned_by": "google",
                "permission": [],
                "root": m,
                "parent": None,
            }
            for m in self.config.proxy.models
        ]
        return JSONResponse(
            status_code=200,
            content={"object": "list", "data": fallback_data},
        )

    async def forward_chat_completions(
        self,
        request: Request,
        payload: dict[str, Any],
    ) -> Response:
        # 1. 模型名规范化：自动补全 google/ 前缀
        if "model" in payload and isinstance(payload["model"], str):
            original_model = payload["model"]
            payload["model"] = normalize_model_name(
                original_model, auto_prefix=self.config.proxy.auto_prefix_model
            )
            if payload["model"] != original_model:
                logger.debug(
                    f"Normalized model: '{original_model}' -> '{payload['model']}'"
                )

        # 2. 获取当前有效的 GCP Access Token
        access_token = await self.token_manager.get_access_token()

        # 3. 构造上游目标端点 URL
        target_url = f"{self.token_manager.base_upstream_url}/chat/completions"

        is_stream = bool(payload.get("stream", False))

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if is_stream else "application/json",
            # 禁用上游 gzip 压缩，确保 SSE 分块实时解包直达下游
            "Accept-Encoding": "identity",
        }

        if is_stream:
            # 4a. SSE 流式响应透传
            logger.debug(f"Streaming chat completions to: {target_url}")
            req = self.http_client.build_request(
                "POST",
                target_url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=None,  # 禁用读超时以支持超长流式输出
                    write=30.0,
                    pool=None,
                ),
            )
            upstream_response = await self.http_client.send(req, stream=True)

            # 如果上游返回非 2xx 状态码（如 400/404/429），先读取完整错误响应再返回
            if upstream_response.status_code >= 400:
                error_body = await upstream_response.aread()
                await upstream_response.aclose()
                return Response(
                    content=error_body,
                    status_code=upstream_response.status_code,
                    media_type=upstream_response.headers.get(
                        "content-type", "application/json"
                    ),
                )

            async def stream_iterator() -> AsyncGenerator[bytes, None]:
                try:
                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk
                finally:
                    await upstream_response.aclose()

            return StreamingResponse(
                stream_iterator(),
                status_code=upstream_response.status_code,
                media_type="text/event-stream; charset=utf-8",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # 4b. 普通非流式响应
            logger.debug(f"Non-streaming chat completions to: {target_url}")
            upstream_response = await self.http_client.post(
                target_url,
                json=payload,
                headers=headers,
                timeout=self.config.gcp.timeout_seconds,
            )
            return Response(
                content=upstream_response.content,
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get(
                    "content-type", "application/json"
                ),
            )
