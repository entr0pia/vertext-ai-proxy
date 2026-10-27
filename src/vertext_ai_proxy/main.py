import uvicorn
from vertext_ai_proxy.app import create_app
from vertext_ai_proxy.core.config import load_config


def main() -> None:
    """启动反向代理服务器。"""
    config = load_config()
    app = create_app(config=config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
