# Backward-compatibility re-exports
from vertext_ai_proxy.core.exceptions import (  # noqa: F401
    OpenAIAPIException,
    openai_exception_handler,
    raise_unauthorized,
)
from vertext_ai_proxy.core.security import APIKeyValidator  # noqa: F401

__all__ = [
    "APIKeyValidator",
    "OpenAIAPIException",
    "openai_exception_handler",
    "raise_unauthorized",
]
