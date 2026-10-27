# Backward-compatibility re-exports
from vertext_ai_proxy.core.auth import (  # noqa: F401
    CLOUD_PLATFORM_SCOPE,
    GCPTokenManager,
)

__all__ = [
    "CLOUD_PLATFORM_SCOPE",
    "GCPTokenManager",
]
