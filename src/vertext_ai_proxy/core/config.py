import logging
from pathlib import Path
import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 已知的示例或占位符 Key 集合
FORBIDDEN_PLACEHOLDER_KEYS: set[str] = {
    "sk-change-this-to-your-own-secret-key",
    "sk-vertex-local-test",
    "sk-change-me",
    "sk-example-key",
    "sk-my-vertex-proxy-key",
    "sk-placeholder",
}

DEFAULT_MODELS: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8766
    log_level: str = "info"


class ProxyConfig(BaseModel):
    api_keys: list[str] = Field(default_factory=list)
    auto_prefix_model: bool = True
    http_proxy: str | None = None
    # 可在 yaml 配置文件中自由增减模型，供 /v1/models 接口返回给客户端
    models: list[str] = Field(default_factory=lambda: list(DEFAULT_MODELS))


class GCPConfig(BaseModel):
    service_account_file: str = "service_account.json"
    project_id: str = ""
    location: str = "global"
    timeout_seconds: int = 120


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    gcp: GCPConfig = Field(default_factory=GCPConfig)


def validate_config(config: AppConfig) -> None:
    """启动前强制进行安全性校验：

    1. 校验 api_keys 是否非空
    2. 校验是否仍在使用示例/占位 Key
    """
    if not config.proxy.api_keys:
        raise ValueError(
            "[FATAL SECURITY ERROR] No API key configured in 'proxy.api_keys'. "
            "Please configure at least one custom secret key in config.local.yaml."
        )

    for raw_key in config.proxy.api_keys:
        key = raw_key.strip()
        if not key:
            continue
        if key in FORBIDDEN_PLACEHOLDER_KEYS or key.startswith("sk-change-this"):
            raise ValueError(
                f"[FATAL SECURITY ERROR] Detected placeholder API Key: '{key}'. "
                "The server refuses to start with default/example keys. "
                "Please copy config.example.yaml to config.local.yaml and set your own secure API key."
            )


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """加载配置文件：

    优先级：指定路径 -> config.local.yaml -> config.yaml -> 默认配置
    """
    target_path: Path | None = None

    if config_path:
        target_path = Path(config_path)
    else:
        local_config = Path("config.local.yaml")
        standard_config = Path("config.yaml")

        if local_config.exists():
            target_path = local_config
        elif standard_config.exists():
            target_path = standard_config

    if target_path and target_path.exists():
        logger.info(f"Loading configuration from {target_path}")
        with open(target_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return AppConfig(**data)

    logger.info("No configuration file found; initializing with default configuration.")
    return AppConfig()
