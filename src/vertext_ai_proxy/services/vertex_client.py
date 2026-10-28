import logging
from typing import Any
import httpx
from vertext_ai_proxy.core.auth import GCPTokenManager
from vertext_ai_proxy.core.config import AppConfig

logger = logging.getLogger(__name__)


class VertexClient:
    """封装与 Google Cloud Vertex AI 底层交互的 HTTP 客户端。"""

    def __init__(
        self,
        config: AppConfig,
        token_manager: GCPTokenManager,
        http_client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.token_manager = token_manager
        self.http_client = http_client

    @property
    def project_id(self) -> str:
        return self.token_manager.project_id

    @property
    def location(self) -> str:
        return self.token_manager.location

    def get_domain(self, location: str | None = None) -> str:
        loc = location or self.location
        if loc in ("global", "us", "eu"):
            return "aiplatform.googleapis.com"
        return f"{loc}-aiplatform.googleapis.com"

    def get_openapi_url(self, subpath: str) -> str:
        domain = self.get_domain()
        clean_subpath = subpath.lstrip("/")
        return (
            f"https://{domain}/v1/projects/{self.project_id}"
            f"/locations/{self.location}/endpoints/openapi/{clean_subpath}"
        )

    def get_predict_url(self, model: str, location: str | None = None) -> str:
        loc = location or self.location
        domain = self.get_domain(loc)
        # 剥离前缀 google/
        clean_model = model.split("/")[-1]
        return (
            f"https://{domain}/v1/projects/{self.project_id}"
            f"/locations/{loc}/publishers/google/models/{clean_model}:predict"
        )

    def get_gemini_embed_url(
        self,
        model: str,
        action: str = "embedContent",
        location: str | None = None,
    ) -> str:
        """构建 Gemini 专属嵌入端点 URL（:embedContent 或 :batchEmbedContents）。"""
        loc = location or self.location
        domain = self.get_domain(loc)
        clean_model = model.split("/")[-1]
        clean_action = action.lstrip(":")
        return (
            f"https://{domain}/v1/projects/{self.project_id}"
            f"/locations/{loc}/publishers/google/models/{clean_model}:{clean_action}"
        )

    def get_publisher_models_url(self) -> str:
        return "https://aiplatform.googleapis.com/v1beta1/publishers/google/models"

    async def get_auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        access_token = await self.token_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers
