from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
from typing import Any
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
import httpx
import uvicorn

from vertext_ai_proxy.auth import GCPTokenManager
from vertext_ai_proxy.config import AppConfig, load_config, validate_config
from vertext_ai_proxy.proxy import VertexAIProxy
from vertext_ai_proxy.security import (
    APIKeyValidator,
    OpenAIAPIException,
    openai_exception_handler,
)

logger = logging.getLogger(__name__)


def create_app(
    config: AppConfig | None = None,
    token_manager: GCPTokenManager | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """FastAPI 应用工厂函数，支持依赖注入以便利单元测试。"""
    active_config = config or load_config()

    # 启动前强制执行配置安全校验（阻断示例占位 Key 和空 Key）
    validate_config(active_config)

    # 初始化 Token 管理器
    active_token_manager = token_manager or GCPTokenManager(active_config.gcp)

    created_client = False
    effective_http_client = http_client
    if effective_http_client is None:
        proxy_url = active_config.proxy.http_proxy
        effective_http_client = httpx.AsyncClient(
            proxy=proxy_url if proxy_url else None,
            follow_redirects=True,
        )
        created_client = True

    proxy_instance = VertexAIProxy(
        config=active_config,
        token_manager=active_token_manager,
        http_client=effective_http_client,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info(
            f"Vertex AI OpenAI Proxy started on http://{active_config.server.host}:{active_config.server.port} "
            f"(GCP Project: {active_token_manager.project_id}, Location: {active_token_manager.location})"
        )
        try:
            yield
        finally:
            if created_client and effective_http_client:
                await effective_http_client.aclose()
                logger.info("HTTP client closed.")

    app = FastAPI(
        title="Vertex AI OpenAI Proxy",
        description="Lightweight reverse proxy translating Google Cloud Vertex AI OpenAPI to OpenAI format",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 将 proxy 实例绑定到 app.state
    app.state.proxy = proxy_instance

    # 注册符合 OpenAI 标准的异常拦截器
    app.add_exception_handler(OpenAIAPIException, openai_exception_handler)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(f"Unhandled error processing {request.method} {request.url}: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"Internal proxy error: {str(exc)}",
                    "type": "api_error",
                    "param": None,
                    "code": "internal_error",
                }
            },
        )

    # 鉴权依赖注入
    api_key_validator = APIKeyValidator(active_config)

    # 1. 健康检查探针（免鉴权）
    @app.get("/healthz")
    async def health_check() -> dict[str, Any]:
        return {
            "status": "healthy",
            "project_id": active_token_manager.project_id,
            "location": active_token_manager.location,
        }

    # 2. 模型列表接口（OpenAI 兼容，实时向上游 Vertex AI 请求获取）
    @app.get("/v1/models")
    async def list_models(
        request: Request,
        _: str = Depends(api_key_validator),
    ) -> Response:
        proxy: VertexAIProxy = app.state.proxy
        return await proxy.forward_models(request)

    # 3. 对话补全反代接口（OpenAI 兼容）
    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        _: str = Depends(api_key_validator),
    ) -> Response:
        payload = await request.json()
        proxy: VertexAIProxy = app.state.proxy
        return await proxy.forward_chat_completions(request, payload)

    return app


def main() -> None:
    """服务主入口命令行。"""
    config = load_config()
    app = create_app(config=config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
