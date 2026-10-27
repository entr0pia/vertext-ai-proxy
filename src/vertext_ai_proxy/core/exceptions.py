from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


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
