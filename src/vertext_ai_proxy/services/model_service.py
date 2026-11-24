import logging
import time
from typing import Any
from fastapi.responses import JSONResponse
from vertext_ai_proxy.core.exceptions import OpenAIAPIException
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)


class ModelService:
    """管理模型列表的拉取与缓存。"""

    def __init__(self, client: VertexClient) -> None:
        self.client = client
        self._cached_models_response: dict[str, Any] | None = None
        self._models_cache_expiry: float = 0.0

    async def get_models(self) -> JSONResponse:
        """从 Google Cloud Vertex AI PublisherModel API 动态拉取模型列表。

        100% 动态实时获取当前 Google 官方开放的模型，并在内存中缓存 1 小时。
        发生异常时优雅回退至本地预设列表，保障客户端永不 404。
        """
        now = time.time()
        if self._cached_models_response and now < self._models_cache_expiry:
            return JSONResponse(status_code=200, content=self._cached_models_response)

        try:
            access_token = await self.client.token_manager.get_access_token()
            publisher_url = self.client.get_publisher_models_url()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            logger.debug(f"Fetching dynamic publisher models from: {publisher_url}")
            publisher_models: list[dict[str, Any]] = []
            page_token: str | None = None
            max_pages = 20
            fetched_ok = False

            for page_idx in range(max_pages):
                params: dict[str, str] = {"pageSize": "100"}
                if page_token:
                    params["pageToken"] = page_token
                resp = await self.client.http_client.get(
                    publisher_url,
                    headers=headers,
                    params=params,
                    timeout=self.client.config.gcp.timeout_seconds,
                )

                if resp.status_code != 200:
                    if page_idx == 0:
                        logger.warning(
                            f"Upstream publisher models returned status {resp.status_code}, falling back to config models."
                        )
                    else:
                        logger.warning(
                            f"Upstream publisher models page {page_idx + 1} returned status {resp.status_code}, stopping pagination."
                        )
                    break

                fetched_ok = True
                raw_json = resp.json()
                publisher_models.extend(raw_json.get("publisherModels", []))
                page_token = raw_json.get("nextPageToken")
                if not page_token:
                    break

            if fetched_ok:
                models_data = []
                created_ts = int(now)
                seen_ids: set[str] = set()

                for item in publisher_models:
                    full_name = item.get("name", "")
                    if not full_name:
                        continue
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
                for custom_m in self.client.config.proxy.models:
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
            for m in self.client.config.proxy.models
        ]
        return JSONResponse(
            status_code=200,
            content={"object": "list", "data": fallback_data},
        )

    async def get_model(self, model_id: str) -> JSONResponse:
        """按模型 ID 查询单个模型详情，支持无前缀与 google/ 前缀两种形式。"""
        target = model_id.strip()
        if not target:
            raise OpenAIAPIException(
                status_code=404,
                message="The model '' does not exist",
                code="model_not_found",
                param="model",
                err_type="invalid_request_error",
            )

        models_resp = await self.get_models()
        # 从缓存或最新拉取的列表中检索
        if self._cached_models_response:
            models_data = self._cached_models_response.get("data", [])
        else:
            import json
            payload = json.loads(models_resp.body.decode("utf-8"))
            models_data = payload.get("data", [])

        # 1. 优先精确匹配
        for item in models_data:
            if item.get("id") == target:
                return JSONResponse(status_code=200, content=item)

        # 2. 次选规范化前缀/去前缀匹配
        alt_candidates: set[str] = set()
        if "/" in target:
            alt_candidates.add(target.split("/")[-1])
        else:
            alt_candidates.add(f"google/{target}")

        for item in models_data:
            if item.get("id") in alt_candidates or item.get("root") in alt_candidates:
                return JSONResponse(status_code=200, content=item)

        raise OpenAIAPIException(
            status_code=404,
            message=f"The model '{target}' does not exist",
            code="model_not_found",
            param="model",
            err_type="invalid_request_error",
        )
