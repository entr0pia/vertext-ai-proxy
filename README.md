# Vertex AI OpenAI Proxy

[![PyPI version](https://img.shields.io/pypi/v/vertext-ai-proxy.svg)](https://pypi.org/project/vertext-ai-proxy/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/vertext-ai-proxy/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)

轻量级 Google Cloud Vertex AI（Gemini Enterprise Agent Platform）OpenAI 协议反向代理网关。

将 Google Cloud 的服务账号（Service Account）OAuth 2.0 认证转换为通用的 OpenAI API Key 鉴权模式，下游客户端（如 Cursor、NextChat、Cherry Studio、OpenAI SDK 等）只需配置标准的 `Base URL` 和静态 `API Key`，即可无缝调用 Vertex AI 模型。

---

## 快速上手

### 方式 A：通过 PyPI 全局安装（推荐）

本工具已正式发布至 PyPI，支持通过 `uv tool`、`uvx` 或 `pip` 全局安装与运行：

```powershell
# 1. 全局安装 CLI 工具（推荐使用 uv）
uv tool install vertext-ai-proxy

# 或者使用 pip 全局安装：
# pip install vertext-ai-proxy

# 2. 在任意工作目录下一键初始化配置文件
vap init

# 3. 按提示编辑当前目录下的 config.local.yaml：
#    - 设置您的自定义 API Key (例如 sk-my-secret-token)
#    - 将 GCP 服务账号 service_account.json 放在该目录（或配置绝对路径）

# 4. 启动反向代理服务
vap run
```

> [!TIP]
> **免安装运行（uvx）**：如果您不想全局安装，也可以通过 `uvx` 直接执行：
> ```powershell
> uvx vertext-ai-proxy vap init
> uvx vertext-ai-proxy vap run
> ```
> **版本升级**：
> ```powershell
> uv tool upgrade vertext-ai-proxy
> # 或 pip install --upgrade vertext-ai-proxy
> ```

支持的 CLI 参数：
- `vap init [-f/--force]`：在当前目录创建 `config.local.yaml`（带防误覆盖保护）。
- `vap run [-c/--config <path>] [-H/--host <host>] [-p/--port <port>]`：启动服务。
- `vap -v/--version`：查看当前工具版本。

---

### 方式 B：源码本地开发运行

#### 1. 放置 GCP 服务账号密钥

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

## 核心特性

- **协议原生兼容**：直接对接 Google 官方 `endpoints/openapi`，无需进行复杂的数据结构重写与字段转换。
- **全能向量生成（Embeddings）**：提供标准 OpenAI `/v1/embeddings` 端点，透明路由桥接 Google Vertex AI 原生能力，原生支持最新多模态模型 `gemini-embedding-2`（自动适配 `:embedContent`/`:batchEmbedContents`）、`text-embedding-004`、`gemini-embedding-001` 和 `text-multilingual-embedding-002`，支持自定义向量维度与批量生成。
- **自动化 Token 管理**：读取 `service_account.json` 自动解析 `project_id`，在内存中管理并自动异步续签 Google OAuth Bearer Token（有效时长 1 小时，过期前 5 分钟自动刷新）。
- **零缓冲流式输出**：基于 `httpx` 实现 Server-Sent Events（SSE）无缓冲实时打字机输出，长文本生成更稳定。
- **安全优先（Secure by Default）**：
  - 默认仅监听本地回环地址（`127.0.0.1:8766`），杜绝局域网或公网非预期暴露。
  - **启动期强校验**：启动时检测到示例/占位 Key（如 `sk-change-this-...`）或空 Key 时，立即终止运行并报错提示。
  - `.gitignore` 默认严格忽略 `config.local.yaml` 与 `service_account.json`。
- **模型名智能兼容**：客户端请求 `gemini-2.5-flash` 时，代理可自动重写为 `google/gemini-2.5-flash`。
- **多 Key 与网络代理**：支持多客户端 API Key 白名单配置，支持配置本地网络代理端口（`http_proxy`）。

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

### 3. Python OpenAI SDK 生成文本向量 (Embeddings)

```python
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:8766/v1",
    api_key="sk-my-secret-token-123456",
)

# 1. 单文本嵌入
embedding_res = client.embeddings.create(
    model="text-embedding-004",
    input="The quick brown fox jumps over the lazy dog",
)
print("Vector dims:", len(embedding_res.data[0].embedding))

# 2. 多文本批量嵌入并自定义维度
batch_res = client.embeddings.create(
    model="gemini-embedding-001",
    input=["First document", "Second document"],
    dimensions=512,
)
print(f"Batch count: {len(batch_res.data)}, Dims: {len(batch_res.data[0].embedding)}")

# 3. 使用最新 gemini-embedding-2 多模态嵌入模型
gemini2_res = client.embeddings.create(
    model="gemini-embedding-2",
    input=["Multimodal query document 1", "Multimodal query document 2"],
    dimensions=768,
)
print(f"Gemini-Embedding-2 dims: {len(gemini2_res.data[0].embedding)}")
```

### 4. cURL 命令行测试

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

运行全部 50 个单元与集成测试：

```powershell
$env:PYTHONUTF8=1; uv run pytest -v
```

