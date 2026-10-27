import secrets
from fastapi import Header
from vertext_ai_proxy.core.config import AppConfig
from vertext_ai_proxy.core.exceptions import OpenAIAPIException, raise_unauthorized


class APIKeyValidator:
    """验证客户端请求中的 API Key 是否在允许列表中。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def __call__(
        self,
        authorization: str | None = Header(None),
        api_key_header: str | None = Header(None, alias="api-key"),
    ) -> str:
        token: str | None = None

        if authorization:
            parts = authorization.strip().split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
            else:
                token = authorization.strip()

        if not token and api_key_header:
            token = api_key_header.strip()

        if not token:
            raise_unauthorized(
                "Missing API key. Provide authorization header: 'Authorization: Bearer <key>' or 'api-key: <key>'."
            )

        valid_keys = [k.strip() for k in self.config.proxy.api_keys if k.strip()]
        if not valid_keys:
            raise_unauthorized("No authorized API keys configured on proxy server.")

        # 使用 secrets.compare_digest 防止时序攻击
        matched = False
        for valid_key in valid_keys:
            if secrets.compare_digest(token, valid_key):
                matched = True
                break

        if not matched:
            raise_unauthorized("Invalid API key provided.")

        return token
