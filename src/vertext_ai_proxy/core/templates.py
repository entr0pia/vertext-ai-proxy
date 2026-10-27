"""内嵌配置文件模板与脚手架资源。"""

DEFAULT_CONFIG_TEMPLATE = """# ==========================================
# Vertex AI OpenAI Proxy 配置文件
# ==========================================

server:
  # 默认仅监听本地回环地址（127.0.0.1），杜绝局域网意外暴露
  host: "127.0.0.1"
  # 默认端口 8766
  port: 8766
  log_level: "info" # debug | info | warning | error

proxy:
  # 客户端鉴权 API Key 列表
  # ⚠️ 安全警告：启动时系统会强制校验！
  # 如果包含以下示例 Key 或为空，服务将拒绝启动。
  # 请复制为 config.local.yaml 并修改为您自己的私密 Key。
  api_keys:
    - "sk-change-this-to-your-own-secret-key"

  # 模型名自动补全（将 gemini-2.5-flash 补全为 google/gemini-2.5-flash）
  auto_prefix_model: true

  # 可选：连接 Google 时的本地出口代理（如本地 Clash 代理），留空则直连
  # http_proxy: "http://127.0.0.1:7890"

gcp:
  # 服务账号凭据 JSON 文件路径（放置在当前目录即可）
  service_account_file: "service_account.json"

  # GCP 项目 ID（留空则自动从 service_account.json 解析）
  project_id: ""

  # 区域位置（推荐 global）
  location: "global"

  # 上游调用超时时间（秒）
  timeout_seconds: 120
"""
