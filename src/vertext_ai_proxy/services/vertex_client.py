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

    def get_generate_content_url(
        self,
        model: str,
        stream: bool = False,
        location: str | None = None,
    ) -> str:
        """构建 Vertex AI 原生 Gemini 生成端点 URL（:generateContent 或 :streamGenerateContent?alt=sse）。"""
        loc = location or self.location
        domain = self.get_domain(loc)
        clean_model = model.split("/")[-1]
        if stream:
            return (
                f"https://{domain}/v1/projects/{self.project_id}"
                f"/locations/{loc}/publishers/google/models/{clean_model}:streamGenerateContent?alt=sse"
            )
        return (
            f"https://{domain}/v1/projects/{self.project_id}"
            f"/locations/{loc}/publishers/google/models/{clean_model}:generateContent"
        )

    def get_publisher_models_url(self, location: str | None = None) -> str:
        domain = self.get_domain(location)
        return f"https://{domain}/v1beta1/publishers/google/models"

    def get_interactions_url(self, location: str | None = None) -> str:
        """构建 Vertex AI 原生 Interactions API 根端点 URL。"""
        loc = location or self.location
        domain = self.get_domain(loc)
        return f"https://{domain}/v1beta1/projects/{self.project_id}/locations/{loc}/interactions"

    def get_interaction_url(
        self,
        interaction_id: str,
        location: str | None = None,
    ) -> str:
        """构建单条 Interaction 资源 URL（GET / DELETE）。"""
        return f"{self.get_interactions_url(location)}/{interaction_id}"

    def get_interaction_cancel_url(
        self,
        interaction_id: str,
        location: str | None = None,
    ) -> str:
        """构建 Interaction 取消端点 URL（POST /cancel）。"""
        return f"{self.get_interaction_url(interaction_id, location)}/cancel"

    @staticmethod
    def extract_upstream_error_message(resp: httpx.Response) -> str:
        """从上游 Vertex AI 响应中提取可读错误消息。"""
        err_msg = resp.text
        try:
            err_json = resp.json()
            if isinstance(err_json, dict) and isinstance(err_json.get("error"), dict):
                err_msg = err_json["error"].get("message", err_msg)
            elif isinstance(err_json, list) and err_json:
                first = err_json[0]
                if isinstance(first, dict) and isinstance(first.get("error"), dict):
                    err_msg = first["error"].get("message", err_msg)
        except Exception:
            pass
        return err_msg

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
