from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
import uvicorn

from vertext_ai_proxy.core.auth import GCPTokenManager
from vertext_ai_proxy._version import __version__
from vertext_ai_proxy.core.config import AppConfig, load_config, validate_config
from vertext_ai_proxy.core.exceptions import (
    OpenAIAPIException,
    openai_exception_handler,
)
from vertext_ai_proxy.core.security import APIKeyValidator
from vertext_ai_proxy.proxy import VertexAIProxy
from vertext_ai_proxy.routers.health import router as health_router
from vertext_ai_proxy.routers.v1.chat import router as chat_router
from vertext_ai_proxy.routers.v1.embeddings import router as embeddings_router
from vertext_ai_proxy.routers.v1.models import router as models_router

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
        version=__version__,
        lifespan=lifespan,
    )

    # 注册依赖服务与组件至 app.state
    app.state.config = active_config
    app.state.proxy = proxy_instance
    app.state.client = proxy_instance.client
    app.state.chat_service = proxy_instance.chat_service
    app.state.embedding_service = proxy_instance.embedding_service
    app.state.model_service = proxy_instance.model_service
    app.state.api_key_validator = APIKeyValidator(active_config)

    # 注册符合 OpenAI 标准的异常拦截器
    app.add_exception_handler(OpenAIAPIException, openai_exception_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        err_msg = "; ".join([f"{err.get('loc', [])}: {err.get('msg')}" for err in errors])
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": err_msg,
                    "type": "invalid_request_error",
                    "param": str(errors[0].get("loc", [None])[-1]) if errors else None,
                    "code": "invalid_parameter",
                }
            },
        )

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

    # 挂载模块化路由
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(embeddings_router)

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
