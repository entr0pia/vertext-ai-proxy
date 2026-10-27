# Backward-compatibility re-exports
from vertext_ai_proxy.core.config import (  # noqa: F401
    AppConfig,
    DEFAULT_MODELS,
    FORBIDDEN_PLACEHOLDER_KEYS,
    GCPConfig,
    ProxyConfig,
    ServerConfig,
    load_config,
    validate_config,
)

__all__ = [
    "AppConfig",
    "DEFAULT_MODELS",
    "FORBIDDEN_PLACEHOLDER_KEYS",
    "GCPConfig",
    "ProxyConfig",
    "ServerConfig",
    "load_config",
    "validate_config",
]
