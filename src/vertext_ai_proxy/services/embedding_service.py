import asyncio
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
    """处理 OpenAI 向量请求向 Vertex AI 原生端点的转换与代理。

    支持两类底层端点：
    1. 传统/现有文本嵌入模型（text-embedding-004 等）-> Vertex AI :predict
    2. 新一代多模态嵌入模型（gemini-embedding-2 等）-> Gemini :embedContent / :batchEmbedContents
    """

    def __init__(self, client: VertexClient) -> None:
        self.client = client

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResponse:
        clean_model = request.model.split("/")[-1].lower()
        if clean_model.startswith("gemini-embedding-2"):
            return await self._create_gemini_embeddings(request, clean_model)
        return await self._create_predict_embeddings(request, clean_model)

    async def _create_predict_embeddings(
        self, request: EmbeddingRequest, clean_model: str
    ) -> EmbeddingResponse:
        """针对传统文本嵌入模型（如 text-embedding-004）的 :predict 端点代理。"""
        texts = [request.input] if isinstance(request.input, str) else request.input
        instances: list[dict[str, Any]] = [{"content": text} for text in texts]

        payload: dict[str, Any] = {"instances": instances}
        if request.dimensions:
            payload["parameters"] = {"outputDimensionality": request.dimensions}

        predict_url = self.client.get_predict_url(clean_model)
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

    async def _create_gemini_embeddings(
        self, request: EmbeddingRequest, clean_model: str
    ) -> EmbeddingResponse:
        """针对 Gemini 多模态嵌入模型（如 gemini-embedding-2）的端点代理。"""
        texts = [request.input] if isinstance(request.input, str) else request.input
        headers = await self.client.get_auth_headers()
        url = self.client.get_gemini_embed_url(clean_model, "embedContent")

        async def _embed_single(text: str) -> list[float]:
            payload: dict[str, Any] = {
                "content": {
                    "parts": [{"text": text}]
                }
            }
            if request.dimensions:
                payload["outputDimensionality"] = request.dimensions

            logger.debug(f"Forwarding Gemini embedContent request for '{clean_model}' to {url}")
            try:
                resp = await self.client.http_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.client.config.gcp.timeout_seconds,
                )
            except Exception as e:
                logger.error(f"Failed to connect to upstream Gemini embedding endpoint: {e}")
                raise OpenAIAPIException(
                    status_code=502,
                    message=f"Failed to connect to Vertex AI Gemini embeddings: {str(e)}",
                    code="upstream_connect_error",
                    err_type="api_error",
                )

            if resp.status_code != 200:
                logger.error(
                    f"Gemini embedding endpoint returned error {resp.status_code}: {resp.text}"
                )
                err_msg = resp.text
                try:
                    err_json = resp.json()
                    if "error" in err_json and isinstance(err_json["error"], dict):
                        err_msg = err_json["error"].get("message", err_msg)
                except Exception:
                    pass

                raise OpenAIAPIException(
                    status_code=resp.status_code,
                    message=f"Vertex AI Gemini embedding error: {err_msg}",
                    code="upstream_error",
                    err_type="api_error",
                )

            resp_data = resp.json()
            return resp_data.get("embedding", {}).get("values", [])

        if len(texts) == 1:
            vec = await _embed_single(texts[0])
            data_objects = [EmbeddingObject(index=0, embedding=vec)]
        else:
            vectors = await asyncio.gather(*[_embed_single(t) for t in texts])
            data_objects = [
                EmbeddingObject(index=idx, embedding=vec)
                for idx, vec in enumerate(vectors)
            ]

        # Token 统计计算（上游缺省时使用安全估算兜底，保证 OpenAI usage 整数非空）
        total_tokens = sum(max(1, len(t) // 4) for t in texts)

        return EmbeddingResponse(
            model=request.model,
            data=data_objects,
            usage=EmbeddingUsage(
                prompt_tokens=total_tokens,
                total_tokens=total_tokens,
            ),
        )
