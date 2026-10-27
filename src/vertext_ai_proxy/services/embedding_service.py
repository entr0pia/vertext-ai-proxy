import logging
from typing import Any
from vertext_ai_proxy.core.exceptions import OpenAIAPIException
from vertext_ai_proxy.schemas.embeddings import (
    EmbeddingObject,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUsage,
)
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)


class EmbeddingService:
    """处理 OpenAI 向量请求向 Vertex AI 原生 :predict 端点的转换与代理。"""

    def __init__(self, client: VertexClient) -> None:
        self.client = client

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResponse:
        texts = [request.input] if isinstance(request.input, str) else request.input
        instances: list[dict[str, Any]] = [{"content": text} for text in texts]

        payload: dict[str, Any] = {"instances": instances}
        if request.dimensions:
            payload["parameters"] = {"outputDimensionality": request.dimensions}

        predict_url = self.client.get_predict_url(request.model)
        headers = await self.client.get_auth_headers()

        logger.debug(
            f"Forwarding embedding request for model '{request.model}' "
            f"({len(texts)} instances) to {predict_url}"
        )

        try:
            resp = await self.client.http_client.post(
                predict_url,
                headers=headers,
                json=payload,
                timeout=self.client.config.gcp.timeout_seconds,
            )
        except Exception as e:
            logger.error(f"Failed to connect to upstream Vertex AI prediction endpoint: {e}")
            raise OpenAIAPIException(
                status_code=502,
                message=f"Failed to connect to Vertex AI: {str(e)}",
                code="upstream_connect_error",
                err_type="api_error",
            )

        if resp.status_code != 200:
            logger.error(
                f"Vertex AI prediction endpoint returned error {resp.status_code}: {resp.text}"
            )
            # 提取上游错误信息
            err_msg = resp.text
            try:
                err_json = resp.json()
                if "error" in err_json and isinstance(err_json["error"], dict):
                    err_msg = err_json["error"].get("message", err_msg)
            except Exception:
                pass

            raise OpenAIAPIException(
                status_code=resp.status_code,
                message=f"Vertex AI prediction error: {err_msg}",
                code="upstream_error",
                err_type="api_error",
            )

        resp_data = resp.json()
        predictions = resp_data.get("predictions", [])
        data_objects: list[EmbeddingObject] = []
        total_tokens = 0

        for idx, pred in enumerate(predictions):
            emb_info = pred.get("embeddings", {})
            values = emb_info.get("values", [])
            stats = emb_info.get("statistics", {})
            total_tokens += stats.get("token_count", 0)

            data_objects.append(
                EmbeddingObject(
                    index=idx,
                    embedding=values,
                )
            )

        return EmbeddingResponse(
            model=request.model,
            data=data_objects,
            usage=EmbeddingUsage(
                prompt_tokens=total_tokens,
                total_tokens=total_tokens,
            ),
        )
