import secrets
from fastapi import Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from vertext_ai_proxy.config import AppConfig


class OpenAIAPIException(HTTPException):
    """标准的 OpenAI 格式 API 异常。"""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str = "invalid_api_key",
        param: str | None = None,
        err_type: str = "invalid_request_error",
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.status_code = status_code
        self.error_dict = {
            "error": {
                "message": message,
                "type": err_type,
                "param": param,
                "code": code,
            }
        }


async def openai_exception_handler(
    request: Request, exc: OpenAIAPIException
) -> JSONResponse:
    """FastAPI 异常处理器：确保直接返回根级 {"error": {...}} 结构而非嵌套在 {"detail": ...} 中。"""
    return JSONResponse(status_code=exc.status_code, content=exc.error_dict)


def raise_unauthorized(message: str = "Incorrect API key provided.") -> None:
    raise OpenAIAPIException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        message=message,
        code="invalid_api_key",
    )


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
            elif len(parts) == 1:
                token = parts[0].strip()

        if not token and api_key_header:
            token = api_key_header.strip()

        if not token:
            raise_unauthorized("Missing API key in Authorization or api-key header.")

        allowed_keys = self.config.proxy.api_keys
        # 使用常量时间比较防御时序攻击
        is_valid = any(
            secrets.compare_digest(token, allowed_key) for allowed_key in allowed_keys
        )

        if not is_valid:
            raise_unauthorized("Invalid API key provided.")

        return token
