# Vertex AI OpenAI Proxy

[![PyPI version](https://img.shields.io/pypi/v/vertext-ai-proxy.svg)](https://pypi.org/project/vertext-ai-proxy/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/vertext-ai-proxy/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://opensource.org/licenses/MIT)

轻量级 Google Cloud Vertex AI（Gemini Enterprise Agent Platform）OpenAI 协议反向代理网关。

将 Google Cloud 的服务账号（Service Account）OAuth 2.0 认证转换为通用的 OpenAI API Key 鉴权模式，下游客户端（如 Cursor、NextChat、Cherry Studio、OpenAI SDK 等）只需配置标准的 `Base URL` 和静态 `API Key`，即可无缝调用 Vertex AI 模型。

---

## 快速上手

### 前置准备：GCP 服务账号与 IAM 权限

使用本代理前，需在 Google Cloud 控制台中创建一个服务账号（Service Account）、下载 JSON 密钥文件（如 `service_account.json`），并为其授予对应的 IAM 角色：

| IAM 角色（控制台显示名 / 角色 ID） | 支持能力 | 说明 |
| --- | --- | --- |
| **Vertex AI User**（新版控制台显示为 **Agent Platform User**）<br>`roles/aiplatform.user` | **全功能支持（推荐）** | 支持 Chat、Models、Embeddings、Images、Audio，以及 `/v1/responses` 原生 **Vertex AI Interactions API**（服务端持久化会话状态与 `previous_response_id` 续接）。 |
| **Agent Platform Express User (Beta)** | 基础生成与嵌入 | 缺少 `aiplatform.interactions.*` 权限；默认 `responses_backend: "auto"` 模式下，`/v1/responses` 遇到 403 会自动降级到本地内存态（也可手动配置 `responses_backend: "local"`）。 |

**授权步骤（GCP 控制台）**：
1. 打开 **Google Cloud Console → IAM 与管理（IAM & Admin）→ IAM**。
2. 找到您的服务账号（如 `xxx@<project-id>.iam.gserviceaccount.com`），点击右侧 **编辑主账号（铅笔图标）**。
3. 点击 **添加其他角色**，搜索并选择 **Vertex AI User**（或 **Agent Platform User**，对应 `roles/aiplatform.user`），保存即可（权限通常 1~2 分钟内生效）。

也可通过 `gcloud` CLI 一键授权：
```bash
gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
  --member="serviceAccount:<SA_EMAIL>" \
  --role="roles/aiplatform.user"
```

---

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

  # 可选：/v1/responses 后端模式（auto | interactions | local，默认 auto）
  # auto 优先使用 Vertex AI 原生 Interactions API（需服务账号具备 roles/aiplatform.user），遇 403 自动降级到本地内存态
  # responses_backend: "auto"

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
INFO:     Vertex AI OpenAI Proxy started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8766 (Press CTRL+C to quit)
```

---

## 核心特性

- **协议原生兼容**：`/v1/chat/completions` 直接对接 Google 官方 `endpoints/openapi`，支持流式、函数调用、结构化输出与多模态输入。
- **OpenAI Responses API (`/v1/responses`)**：支持非流式与 SSE 语义事件流、工具调用、推理参数与结构化输出，支持 `previous_response_id` 多轮续接、`store: false`、`GET`/`DELETE` 与 `POST .../cancel`。通过 `proxy.responses_backend` 可配置后端模式：
  - `auto`（默认）：优先路由至 Vertex AI 原生 **Gemini Interactions API**（服务端持久化会话状态与思考签名，多轮仅上传增量输入；需为服务账号授予 GCP IAM 角色 `roles/aiplatform.user` / *Agent Platform User*）；若上游返回 HTTP 403 权限不足，自动降级至 `:generateContent` + 本地有界 LRU+TTL 内存态（冷却 5 分钟）。
  - `interactions`：强制走 Vertex AI 原生 Interactions API，遇 403 直接报错不降级。
  - `local`：始终走 `:generateContent` + 本地内存态存储（零数据库依赖，适合仅具 `Agent Platform Express User` 权限的环境）。
- **图像生成与编辑 (`/v1/images/generations` & `/v1/images/edits`)**：按显式 Google 模型名自动路由——`imagen-*` 路由至 Vertex AI `:predict`（支持 `mask` 局部重绘），`gemini-*` 图像模型路由至 `:generateContent`，不做格式拦截，以模型实际返回为准。
- **语音合成与转写翻译 (`/v1/audio/speech`、`/v1/audio/transcriptions`、`/v1/audio/translations`)**：基于 Gemini 多模态与原生 TTS 能力，无外部 `ffmpeg` 依赖，不做 `response_format` 拦截（以模型返回为准，显式传 `wav`/`pcm` 时支持标准库 `wave` 无损封装/解包）。
- **单模型查询与动态模型目录 (`/v1/models` & `/v1/models/{model}`)**：动态拉取 Vertex AI Publisher 模型列表并缓存 1 小时，支持按无前缀或 `google/` 前缀查询单个模型详情。
- **全能向量生成（Embeddings）**：提供标准 OpenAI `/v1/embeddings` 端点，透明路由桥接 Google Vertex AI 原生能力，支持 `gemini-embedding-2`、`text-embedding-004`、`gemini-embedding-001` 等模型。
- **自动化 Token 管理**：读取 `service_account.json` 自动解析 `project_id`，在内存中管理并自动异步续签 Google OAuth Bearer Token（有效时长 1 小时，过期前 5 分钟自动刷新）。
- **零缓冲流式输出**：基于 `httpx` 实现 Server-Sent Events（SSE）无缓冲实时打字机输出，长文本生成更稳定。
- **安全优先（Secure by Default）**：
  - 默认仅监听本地回环地址（`127.0.0.1:8766`），杜绝局域网或公网非预期暴露。
  - **启动期强校验**：启动时检测到示例/占位 Key（如 `sk-change-this-...`）或空 Key 时，立即终止运行并报错提示。
  - `.gitignore` 默认严格忽略 `config.local.yaml` 与 `service_account.json`。
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

### 4. Python OpenAI SDK 调用 Responses / Images / Audio

> 注意：所有扩展端点均要求显式传入 Google 模型名（不做 `whisper-1`/`tts-1`/`dall-e-3` 别名映射）。

```python
import base64
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:8766/v1",
    api_key="sk-my-secret-token-123456",
)

# 1. Responses API（支持 previous_response_id 内存多轮续接与流式 SSE）
r1 = client.responses.create(model="gemini-3.8-flash", input="记住我的代号是 Nova。")
r2 = client.responses.create(
    model="gemini-3.8-flash",
    previous_response_id=r1.id,
    input="我的代号是什么？",
)
print(r2.output_text)

# 2. 图像生成（imagen-* 走 :predict，gemini-* 图像模型走 :generateContent）
img = client.images.generate(
    model="gemini-nano-banana-2.1",
    prompt="A minimal flat icon of a cloud gateway",
    size="1024x1024",
)
with open("out.png", "wb") as f:
    f.write(base64.b64decode(img.data[0].b64_json))

# 3. 语音合成 TTS（以模型实际返回为准，显式传 wav/pcm 支持标准库无损转换）
speech = client.audio.speech.create(
    model="gemini-3.8-flash-tts",
    voice="Kore",
    input="你好，欢迎使用 Vertex AI Proxy。",
    response_format="wav",
)
speech.write_to_file("hello.wav")

# 4. 音频转写 STT & 翻译为英文
with open("hello.wav", "rb") as audio_file:
    transcript = client.audio.transcriptions.create(
        model="gemini-3.8-flash",
        file=audio_file,
    )
    print("Transcription:", transcript.text)
```

### 5. cURL 命令行测试

```bash
curl http://127.0.0.1:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-my-secret-token-123456" \
  -d '{
    "model": "gemini-3.8-flash",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 自动化测试

运行全部单元与集成测试：

```powershell
$env:PYTHONUTF8=1; uv run pytest -v
```

