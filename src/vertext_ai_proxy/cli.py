import argparse
import logging
from pathlib import Path
import sys

from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import load_config, validate_config
from vertext_ai_proxy.core.templates import DEFAULT_CONFIG_TEMPLATE

__version__ = "0.2.0"
logger = logging.getLogger("vertext_ai_proxy.cli")


def handle_init(args: argparse.Namespace) -> int:
    """处理 'vap init' 命令：在当前目录创建 config.local.yaml。"""
    target = Path(args.output)
    if target.exists() and not args.force:
        print(
            f"[WARN] '{target.name}' already exists at: {target.resolve()}\n"
            f"       To overwrite the existing file, run: vap init --force",
            file=sys.stderr,
        )
        return 1

    try:
        target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        print(
            f"[SUCCESS] Initialized configuration file at:\n"
            f"          {target.resolve()}\n\n"
            f"Next steps:\n"
            f"  1. Edit '{target.name}':\n"
            f"     - proxy.api_keys: Replace placeholder with your secret key (e.g. sk-my-secret-key)\n"
            f"     - gcp.service_account_file: Path to your Google Cloud Service Account JSON file\n"
            f"  2. Start the proxy server:\n"
            f"     vap run\n"
        )
        return 0
    except Exception as e:
        print(f"[ERROR] Failed to write '{target}': {e}", file=sys.stderr)
        return 1


def handle_run(args: argparse.Namespace) -> int:
    """处理 'vap run' 命令：加载配置并启动 Uvicorn 反代服务。"""
    import uvicorn

    try:
        config = load_config(args.config)
        # 命令行参数覆盖
        if args.host:
            config.server.host = args.host
        if args.port:
            config.server.port = args.port
        if args.log_level:
            config.server.log_level = args.log_level

        # 启动安全校验
        validate_config(config)
    except Exception as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 1

    app = create_app(config=config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="vap",
        description="Vertex AI OpenAI Proxy (vap) - Lightweight OpenAI-compatible gateway for Google Vertex AI.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit.",
    )

    subparsers = parser.add_subparsers(dest="subcommand", title="Commands")

    # vap init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a new config.local.yaml in the current working directory.",
        description="Scaffold config.local.yaml from default template in current directory.",
    )
    init_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing config.local.yaml if it already exists.",
    )
    init_parser.add_argument(
        "-o",
        "--output",
        default="config.local.yaml",
        help="Target output filename (default: config.local.yaml).",
    )
    init_parser.set_defaults(func=handle_init)

    # vap run
    run_parser = subparsers.add_parser(
        "run",
        help="Start the reverse proxy server.",
        description="Run Vertex AI OpenAI Proxy server with Uvicorn.",
    )
    run_parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Path to YAML configuration file (default: searches config.local.yaml then config.yaml).",
    )
    run_parser.add_argument(
        "-H",
        "--host",
        default=None,
        help="Bind host (default: from config, e.g. 127.0.0.1).",
    )
    run_parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=None,
        help="Bind port (default: from config, e.g. 8766).",
    )
    run_parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error"],
        help="Uvicorn log level (default: from config, e.g. info).",
    )
    run_parser.set_defaults(func=handle_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
