<div align="center">

# 🐪 CaMeL Chat Proxy

**将免费网页端 [CaMeL Chat](https://chat.camel-hub.com/) 逆向为 OpenAI + Anthropic 兼容 API**

SillyTavern · RikkaHub · 任意 OpenAI/Anthropic 客户端均可接入

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-4.0.0-d97757)

</div>

---

## ✨ 特性

| 能力 | 说明 |
|------|------|
| 🔌 **双协议兼容** | OpenAI `/v1/chat/completions` + Anthropic `/v1/messages`，流式/非流式全支持 |
| 🎨 **生图端点** | `/v1/images/generations` 文生图 + `prior_images` 图生图/参考图 |
| 👁️ **多模态视觉** | 图片/文件附件完整送达模型（http URL / base64 / 本地路径） |
| 🤖 **全自动 Cookie** | Playwright 自动登录、定时刷新，零手动操作 |
| 👥 **多账号轮询** | 负载均衡 + 自动故障转移 |
| 🔄 **会话智能轮换** | 每 10 句（可配置）自动换会话，标题自动同步，行为贴近真人 |
| 🥷 **隐蔽优先** | 不轮询上游、不删会话、标准 UUID，最大限度模拟真实浏览器用户 |
| 📊 **用量监控** | 管理面板一键查看账号额度与联网搜索剩余 |
| 🖥️ **暖色管理面板** | 单文件零依赖 WebUI，账号/配置/用量全可视化 |
| 🧱 **分层架构** | core / camel / convert / api 四层单向依赖，29 个单元测试护航 |

## 🚀 快速开始

```bash
git clone https://github.com/pianhua/camel-chat-proxy.git
cd camel-chat-proxy
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json   # 编辑填入账号
python main.py
```

打开 `http://localhost:5050/` 进入管理面板。

### 配置 `config.json`

```json
{
  "api_keys": "sk-camel",
  "admin_password": "admin",
  "session_rotate_turns": 10,
  "web_search": false,
  "camel_accounts": [
    { "email": "your-email@example.com", "password": "your-password" }
  ]
}
```

| 字段 | 默认 | 说明 |
|------|------|------|
| `api_keys` | `sk-camel` | API 鉴权 Key，逗号分隔多个 |
| `admin_password` | `admin` | 管理面板密码 |
| `session_rotate_turns` | `10` | 每个 CaMeL 会话承载的用户消息数上限，到达后自动换新会话 |
| `web_search` | `false` | 全局联网搜索开关（可被请求级 `web_search` 覆盖） |
| `refresh_interval` | `21600` | Cookie 有效期（秒），到期自动重新登录 |

> ⚠️ `config.json` 明文存密码，已在 `.gitignore` 排除，**切勿提交**。

### 后台运行

```bash
bash start.sh     # Linux / macOS
start.bat         # Windows
```

## 🎮 客户端接入

**SillyTavern**

| 配置项 | 值 |
|--------|-----|
| API 来源 | Custom (OpenAI-compatible) |
| API URL | `http://localhost:5050/v1` |
| API Key | `sk-camel` |

**RikkaHub**：选 Anthropic 协议，地址 `http://localhost:5050`，Key `sk-camel`。

## 📡 API 端点

| 端点 | 方法 | 协议 | 说明 |
|------|------|------|------|
| `/v1/models` | GET | OpenAI | 列出可用模型（自动发现，1h 缓存） |
| `/v1/chat/completions` | POST | OpenAI | 聊天补全（流式/非流式/多模态） |
| `/v1/messages` | POST | Anthropic | Anthropic Messages（流式/非流式/图片 block） |
| `/v1/images/generations` | POST | OpenAI | 文生图 / 图生图 |
| `/health` | GET | - | 健康检查 + 账号 Cookie 状态 |
| `/` | GET | - | Web 管理面板 |

### 对话（OpenAI）

```bash
curl http://localhost:5050/v1/chat/completions \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus-4-7",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 多模态（图片理解）

```json
{
  "model": "claude-opus-4-7",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "描述这张图"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]
  }]
}
```

`image_url` 支持 http URL / data URL / 本地路径；`file_url` 支持文档类附件（自动走 CaMeL 文件解析）。

### 生图

```bash
curl http://localhost:5050/v1/images/generations \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只骆驼",
    "size": "1024x1024",
    "n": 1
  }'
```

- `size`：`1024x1024`→1:1、`1792x1024`→16:9、`1024x1792`→9:16
- 图生图：加 `"prior_images": ["data:image/png;base64,..."]`（URL/本地路径亦可）
- 兼容别名：`dall-e-3` → `gpt-image-2`

### 对话（Anthropic）

```bash
curl http://localhost:5050/v1/messages \
  -H "x-api-key: sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Hello"}],"max_tokens":100}'
```

旧模型名自动映射（如 `claude-3-5-sonnet-20241022` → `claude-sonnet-4-6`）。

## 🖥️ 管理面板

`http://localhost:5050/`（暖米色主题，单文件零依赖）

- 📊 统计总览：在线账号 / 模型数 / 轮换阈值
- 👥 账号管理：添加、删除、单账号登录、连接测试、批量刷新 Cookie
- 📈 用量监控：一键拉取账号额度 + 联网搜索剩余（30 分钟缓存，不自动轮询上游）
- ⚙️ 设置：API Key、管理密码、联网开关、会话轮换句数、模型缓存刷新

管理接口统一用 `x-admin-password` 头鉴权：

| 端点 | 说明 |
|------|------|
| `GET /admin/config` | 获取配置 |
| `POST /admin/config` | 合并式更新配置（运行时生效） |
| `POST /admin/login` | 触发账号登录（可指定 `{"email":"..."}`） |
| `POST /admin/refresh-cookies` | 刷新全部账号 Cookie |
| `POST /admin/test-accounts` | 测试全部账号连接 |
| `POST /admin/refresh-models` | 刷新模型缓存 |
| `GET /admin/usage` | 用量聚合（手动调用 + 30min 缓存） |

## 🧱 架构

```
api（HTTP 薄层：验权、收参、响应格式化）
  └── convert（协议转换：OpenAI/Anthropic → CaMeL payload、附件管线）
        └── camel（纯上游协议：client / Playwright 登录 / 会话池）
              └── core（基础设施：config / auth / 共享 httpx）
```

```
camel_chat_proxy/
├── main.py                 # 入口：装配路由、启动登录
├── app/
│   ├── core/               # config.py / auth.py / http.py
│   ├── camel/              # client.py / login.py / session_pool.py
│   ├── convert/            # openai_conv.py / anthropic_conv.py / attachments.py
│   └── api/                # openai_routes.py / anthropic_routes.py / admin_routes.py / deps.py
├── tests/                  # pytest（29 个单元测试）
└── web/index.html          # 管理面板
```

**会话池策略（隐蔽优先）**：每账号 1 个活跃会话 → 聊满 `session_rotate_turns` 句自动换新 → 旧会话直接遗弃不删除（真实用户也会堆积会话）→ 首条回复后自动同步标题。全程零后台轮询。

## 🧪 开发

```bash
pip install -r requirements.txt
python -m pytest -v      # 29 个单元测试
```

## 📋 可用模型

启动时自动发现（约 20 个）：Claude / GPT / Gemini / DeepSeek / Kimi 系列 + `gpt-image-2`、`gemini-3-pro-image-preview` 生图模型。

> Free 用户仅限 Haiku 系列，付费账号解锁全部。

## ⚠️ 注意事项

- 本项目仅供学习研究使用，请遵守 CaMeL 服务条款，不要滥用免费额度
- 代理会读取客户端提供的本地文件路径/URL 作为附件，**请勿无鉴权暴露到公网**
- `config.json` 明文存密码，不要分享

## 📄 License

MIT
