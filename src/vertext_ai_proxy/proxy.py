import logging
from typing import Any
from fastapi import Request, Response
from fastapi.responses import JSONResponse
import httpx

from vertext_ai_proxy.core.auth import GCPTokenManager
from vertext_ai_proxy.core.config import AppConfig
from vertext_ai_proxy.schemas.embeddings import EmbeddingRequest
from vertext_ai_proxy.services.chat_service import ChatService
from vertext_ai_proxy.services.embedding_service import EmbeddingService
from vertext_ai_proxy.services.model_service import ModelService
from vertext_ai_proxy.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)


class VertexAIProxy:
    """组合各个领域服务（Chat、Embedding、Models），提供向后兼容的外观接口（Facade）。"""

    def __init__(
        self,
        config: AppConfig,
        token_manager: GCPTokenManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.token_manager = token_manager
        self.http_client = http_client

        self.client = VertexClient(
            config=config,
            token_manager=token_manager,
            http_client=http_client,
        )
        self.chat_service = ChatService(self.client)
        self.model_service = ModelService(self.client)
        self.embedding_service = EmbeddingService(self.client)

    async def forward_models(self, request: Request | None = None) -> Response:
        return await self.model_service.get_models()

    async def forward_chat_completions(
        self,
        request: Request,
        payload: dict[str, Any],
    ) -> Response:
        return await self.chat_service.forward_chat_completions(request, payload)

    async def forward_embeddings(self, request_data: EmbeddingRequest) -> JSONResponse:
        result = await self.embedding_service.create_embeddings(request_data)
        return JSONResponse(status_code=200, content=result.model_dump())
