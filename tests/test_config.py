from pathlib import Path
import pytest
from vertext_ai_proxy.config import (
    AppConfig,
    ServerConfig,
    ProxyConfig,
    GCPConfig,
    validate_config,
    load_config,
    FORBIDDEN_PLACEHOLDER_KEYS,
)


def test_default_config_values():
    config = AppConfig()
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8766
    assert config.gcp.service_account_file == "service_account.json"
    assert config.gcp.location == "global"
    assert config.proxy.auto_prefix_model is True


def test_validate_config_rejects_empty_keys():
    config = AppConfig(proxy=ProxyConfig(api_keys=[]))
    with pytest.raises(ValueError, match="No API key configured"):
        validate_config(config)


@pytest.mark.parametrize("placeholder", list(FORBIDDEN_PLACEHOLDER_KEYS))
def test_validate_config_rejects_placeholder_keys(placeholder: str):
    config = AppConfig(proxy=ProxyConfig(api_keys=[placeholder]))
    with pytest.raises(ValueError, match="Detected placeholder API Key"):
        validate_config(config)


def test_validate_config_accepts_valid_key():
    config = AppConfig(proxy=ProxyConfig(api_keys=["sk-my-custom-production-key-12345"]))
    # Should not raise any error
    validate_config(config)


def test_load_config_from_explicit_path(tmp_path: Path):
    yaml_content = """
server:
  host: "0.0.0.0"
  port: 9000
proxy:
  api_keys:
    - "sk-custom-from-yaml"
"""
    config_file = tmp_path / "custom.yaml"
    config_file.write_text(yaml_content, encoding="utf-8")

    loaded = load_config(config_file)
    assert loaded.server.host == "0.0.0.0"
    assert loaded.server.port == 9000
    assert loaded.proxy.api_keys == ["sk-custom-from-yaml"]


def test_load_config_local_priority(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 8001\n", encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("server:\n  port: 8002\n", encoding="utf-8")

    loaded = load_config()
    assert loaded.server.port == 8002
