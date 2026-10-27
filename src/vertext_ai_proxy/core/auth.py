import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from vertext_ai_proxy.core.config import GCPConfig

logger = logging.getLogger(__name__)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class GCPTokenManager:
    """管理 Google Cloud Service Account 凭据与 OAuth 2.0 Access Token 的生命周期。

    - 支持原生 GCP 服务账号 JSON，以及带有外层封装（如 service_account 嵌套结构）的 JSON
    - 自动提取 project_id（若未手动指定）
    - 内存中缓存 Access Token，并在过期前通过线程池异步安全刷新
    - 使用 asyncio.Lock 避免高并发下的重复刷新击穿
    """

    def __init__(
        self,
        config: GCPConfig,
        credentials: service_account.Credentials | None = None,
    ) -> None:
        self.config = config
        self._lock = asyncio.Lock()
        self._request = Request()

        if credentials is not None:
            self.credentials = credentials
        else:
            sa_path = Path(config.service_account_file)
            if not sa_path.exists():
                raise FileNotFoundError(
                    f"Google Cloud Service Account file not found at: {sa_path.resolve()}"
                )
            with open(sa_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            # 兼容两种常见格式：
            # 1. 纯 Google 官方下载的 Service Account JSON
            # 2. 外部工具封装格式（如 cli-proxy-api 格式，嵌套在 'service_account' 键下）
            if (
                isinstance(raw_data, dict)
                and "service_account" in raw_data
                and isinstance(raw_data["service_account"], dict)
            ):
                logger.info("Detected wrapped service_account format, extracting inner credentials.")
                sa_info = raw_data["service_account"]
                # 若外层显式声明了 project_id 或 location，智能回退继承
                if not self.config.project_id and raw_data.get("project_id"):
                    self.config.project_id = str(raw_data["project_id"])
                if (not self.config.location or self.config.location == "global") and raw_data.get("location"):
                    self.config.location = str(raw_data["location"])
            else:
                sa_info = raw_data

            self.credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=[CLOUD_PLATFORM_SCOPE],
            )

        # 优先使用配置中显式指定的 project_id，否则从 Service Account JSON 中提取
        self.project_id: str = (
            self.config.project_id.strip()
            if self.config.project_id and self.config.project_id.strip()
            else (getattr(self.credentials, "project_id", "") or "")
        )

        if not self.project_id:
            logger.warning(
                "No project_id could be determined from GCPConfig or credentials."
            )

    @property
    def location(self) -> str:
        return self.config.location

    @property
    def base_upstream_url(self) -> str:
        """根据 location 和 project_id 构造 Vertex AI OpenAPI 的目标 Base URL。"""
        # Google 统一端点路由
        return (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project_id}"
            f"/locations/{self.location}/endpoints/openapi"
        )

    def _is_token_expiring(self, buffer_seconds: int = 300) -> bool:
        """检查 Token 是否已失效或即将失效（默认预留 5 分钟缓冲）。"""
        if not self.credentials.token or not self.credentials.expiry:
            return True

        now = datetime.now(timezone.utc)
        expiry = self.credentials.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        return (expiry - now).total_seconds() < buffer_seconds

    def _refresh_sync(self) -> None:
        """同步刷新 Google OAuth Token（在线程池中调用）。"""
        self.credentials.refresh(self._request)

    async def get_access_token(self) -> str:
        """异步获取当前有效的 Access Token，必要时执行刷新。"""
        if not self._is_token_expiring():
            return self.credentials.token

        async with self._lock:
            # 双重检查锁（DCL）
            if not self._is_token_expiring():
                return self.credentials.token

            logger.info("Google Cloud OAuth token expiring or absent; refreshing...")
            await asyncio.to_thread(self._refresh_sync)
            logger.info("Google Cloud OAuth token successfully refreshed.")
            return self.credentials.token
