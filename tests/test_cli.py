import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from vertext_ai_proxy.cli import build_parser, main
from vertext_ai_proxy.core.templates import DEFAULT_CONFIG_TEMPLATE


def test_cli_version_flag(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "vap" in captured.out or "vap" in captured.err


def test_cli_no_args_prints_help(capsys):
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "usage: vap" in captured.out


def test_vap_init_success(tmp_path: Path):
    target_file = tmp_path / "config.local.yaml"
    exit_code = main(["init", "-o", str(target_file)])
    assert exit_code == 0
    assert target_file.exists()
    content = target_file.read_text(encoding="utf-8")
    assert content == DEFAULT_CONFIG_TEMPLATE


def test_vap_init_overwrite_protection(tmp_path: Path):
    target_file = tmp_path / "config.local.yaml"
    target_file.write_text("custom: content", encoding="utf-8")

    # 未加 --force 应拒绝覆盖
    exit_code = main(["init", "-o", str(target_file)])
    assert exit_code == 1
    assert target_file.read_text(encoding="utf-8") == "custom: content"


def test_vap_init_force_overwrite(tmp_path: Path):
    target_file = tmp_path / "config.local.yaml"
    target_file.write_text("custom: content", encoding="utf-8")

    # 加了 --force 应成功覆盖
    exit_code = main(["init", "-o", str(target_file), "--force"])
    assert exit_code == 0
    assert target_file.read_text(encoding="utf-8") == DEFAULT_CONFIG_TEMPLATE


def test_vap_run_argument_parsing():
    parser = build_parser()
    args = parser.parse_args(
        ["run", "-c", "my_config.yaml", "-H", "0.0.0.0", "-p", "9999", "--log-level", "debug"]
    )
    assert args.subcommand == "run"
    assert args.config == "my_config.yaml"
    assert args.host == "0.0.0.0"
    assert args.port == 9999
    assert args.log_level == "debug"


@patch("uvicorn.run")
@patch("vertext_ai_proxy.app.GCPTokenManager")
def test_vap_run_execution(mock_tm_cls, mock_uvicorn_run, tmp_path: Path):
    mock_tm = MagicMock()
    mock_tm.project_id = "test-proj"
    mock_tm.location = "global"
    mock_tm_cls.return_value = mock_tm

    cfg_file = tmp_path / "test_config.yaml"
    cfg_file.write_text(
        """
server:
  host: 127.0.0.1
  port: 8766
proxy:
  api_keys:
    - sk-test-custom-key-1234
gcp:
  project_id: test-proj
""",
        encoding="utf-8",
    )

    exit_code = main(["run", "-c", str(cfg_file), "-p", "8888"])
    assert exit_code == 0
    mock_uvicorn_run.assert_called_once()
    call_kwargs = mock_uvicorn_run.call_args.kwargs
    assert call_kwargs["port"] == 8888
    assert call_kwargs["host"] == "127.0.0.1"
