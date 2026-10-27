# Vertex AI OpenAI Proxy

轻量级 Google Cloud Vertex AI（Gemini Enterprise Agent Platform）OpenAI 协议反向代理网关。

将 Google Cloud 的服务账号（Service Account）OAuth 2.0 认证转换为通用的 OpenAI API Key 鉴权模式，下游客户端（如 Cursor、NextChat、Cherry Studio、OpenAI SDK 等）只需配置标准的 `Base URL` 和静态 `API Key`，即可无缝调用 Vertex AI 模型。

---

## 核心特性

- **协议原生兼容**：直接对接 Google 官方 `endpoints/openapi`，无需进行复杂的数据结构重写与字段转换。
- **自动化 Token 管理**：读取 `service_account.json` 自动解析 `project_id`，在内存中管理并自动异步续签 Google OAuth Bearer Token（有效时长 1 小时，过期前 5 分钟自动刷新）。
- **零缓冲流式输出**：基于 `httpx` 实现 Server-Sent Events（SSE）无缓冲实时打字机输出，长文本生成更稳定。
- **安全优先（Secure by Default）**：
  - 默认仅监听本地回环地址（`127.0.0.1:8766`），杜绝局域网或公网非预期暴露。
  - **启动期强校验**：启动时检测到示例/占位 Key（如 `sk-change-this-...`）或空 Key 时，立即终止运行并报错提示。
  - `.gitignore` 默认严格忽略 `config.local.yaml` 与 `service_account.json`。
- **模型名智能兼容**：客户端请求 `gemini-2.5-flash` 时，代理可自动重写为 `google/gemini-2.5-flash`。
- **多 Key 与网络代理**：支持多客户端 API Key 白名单配置，支持配置本地网络代理端口（`http_proxy`）。

---

## 快速上手

### 1. 放置 GCP 服务账号密钥

从 Google Cloud 控制台下载服务账号密钥 JSON，重命名为 `service_account.json` 放置在项目根目录：

```text
Vertext-AI-Proxy/
├── service_account.json    <-- 放置在此
├── config.example.yaml
└── ...
```

### 2. 创建私有配置文件

复制示例配置文件为私有覆盖配置 `config.local.yaml`（此文件已被 `.gitignore` 自动忽略）：

```powershell
Copy-Item config.example.yaml config.local.yaml
```

打开 `config.local.yaml`，**将示例 Key 修改为您自己的私密 Key**：

```yaml
server:
  host: "127.0.0.1"    # 默认仅允许本地访问
  port: 8766           # 默认端口

proxy:
  api_keys:
    - "sk-my-secret-token-123456"   # ⚠️ 请修改为您自己的密码，勿使用示例 Key

  auto_prefix_model: true

  # 可选：如果在国内需要配置代理访问 Google，取消注释并填入代理端口：
  # http_proxy: "http://127.0.0.1:7890"

gcp:
  service_account_file: "service_account.json"
  project_id: ""       # 留空将自动从 service_account.json 读取
  location: "global"   # 推荐 global 或对应部署区域
```

### 3. 启动服务

使用 `uv` 启动服务：

```powershell
$env:PYTHONUTF8=1; uv run python -m vertext_ai_proxy.app
```

启动成功后，终端将输出类似如下信息：
```text
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Vertex AI OpenAI Proxy started on http://127.0.0.1:8766 (GCP Project: your-project-id, Location: global)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8766 (Press CTRL+C to quit)
```

---

## 客户端接入方式

### 1. Cursor / NextChat / Cherry Studio 配置

- **API 接口地址（Base URL）**：`http://127.0.0.1:8766/v1`
- **API Key**：您在 `config.local.yaml` 中设置的 Key（如 `sk-my-secret-token-123456`）
- **模型名称**：`gemini-2.5-flash`、`gemini-2.5-pro`、`gemini-1.5-pro`（代理会自动补全 `google/` 前缀）

### 2. Python OpenAI SDK

```python
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:8766/v1",
    api_key="sk-my-secret-token-123456",
)

response = client.chat.completions.create(
    model="gemini-2.5-flash",
    messages=[
        {"role": "user", "content": "你好，请做个自我介绍！"},
    ],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

### 3. cURL 命令行测试

```bash
curl http://127.0.0.1:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-my-secret-token-123456" \
  -d '{
    "model": "gemini-2.5-flash",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 自动化测试

运行全部 30 个单元与集成测试：

```powershell
$env:PYTHONUTF8=1; uv run pytest -v
```
