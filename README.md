# 🐪 CaMeL Chat Proxy v4

将免费网页端 [CaMeL Chat](https://chat.camel-hub.com/) 逆向为 **OpenAI + Anthropic 兼容 API**，供 SillyTavern、RikkaHub 等客户端使用。

## ✨ 特性

- **OpenAI 完全兼容** — `/v1/models`、`/v1/chat/completions`（流式/非流式）
- **Anthropic Messages API** — `/v1/messages`，可直接对接 RikkaHub
- **全自动 Cookie** — Playwright 自动登录，定时刷新，无需手动操作
- **Web 管理面板** — 网页添加/删除账号、测试连接、刷新 Cookie
- **多账号轮询** — 轮询负载均衡，自动故障转移
- **会话智能轮换** — 每 10 句自动轮换会话（可配置），规避上游风控
- **会话标题自动同步** — 对话标题实时同步至 CaMeL 会话列表
- **图生图/参考图支持** — `/v1/images/generations` 支持 `prior_images` 参考图生图
- **用量监控面板** — `/admin/usage` 实时查看账号用量统计
- **隐蔽优先设计** — 行为贴近真实浏览器用户，降低封号风险
- **动态模型发现** — 从 CaMeL API 实时拉取可用模型列表
- **模块化架构** — 清晰分离 core / camel / convert / api 四层
- **CORS 全开** — 允许任意来源跨域

## 🚀 快速开始

### 一键部署

```bash
git clone https://github.com/pianhua/camel-chat-proxy.git
cd camel-chat-proxy
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json
python main.py
```

### 编辑配置

编辑 `config.json` 填入你的 CaMeL 账号：

```json
{
  "api_keys": "sk-camel",
  "session_rotate_turns": 10,
  "camel_accounts": [
    {
      "email": "your-email@example.com",
      "password": "your-password"
    }
  ]
}
```

> ⚠️ **`config.json` 包含敏感信息，切勿提交到 Git！** 已在 `.gitignore` 中排除。

### 后台运行

```bash
bash start.sh          # Linux/macOS
start.bat              # Windows
```

首次启动会自动打开 Playwright 浏览器登录 CaMeL，获取 Cookie 后进程转为后台运行。

## 📡 API 端点

| 端点 | 方法 | 协议 | 说明 |
|------|------|------|------|
| `/v1/models` | GET | OpenAI | 列出可用模型 |
| `/v1/chat/completions` | POST | OpenAI | 聊天补全（流式/非流式） |
| `/v1/messages` | POST | Anthropic | Anthropic 消息（RikkaHub） |
| `/v1/images/generations` | POST | OpenAI | 文生图/图生图（支持 `prior_images` 参考图） |
| `/admin/usage` | GET | 管理 | 账号用量监控 |
| `/health` | GET | - | 健康检查 + Cookie 状态 |
| `/` | GET | - | Web 管理面板 |

### 认证

API 请求需携带 `Authorization: Bearer <api_key>` 头（默认 `sk-camel`）：

```bash
curl http://localhost:5050/v1/models \
  -H "Authorization: Bearer sk-camel"
```

Anthropic 协议可使用 `x-api-key` 头：

```bash
curl http://localhost:5050/v1/messages \
  -H "x-api-key: sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Hello"}],"max_tokens":100}'
```

## 🎮 SillyTavern 配置

| 配置项 | 值 |
|--------|-----|
| API 来源 | Custom (OpenAI-compatible) |
| API URL | `http://localhost:5050/v1` |
| API Key | `sk-camel` |

## 🌐 Web 管理面板

打开 `http://localhost:5050/`：

- 添加/删除 CaMeL 账号
- 一键刷新 Cookie / 测试连接
- 修改 API Key 和管理密码
- 查看实时账号状态

管理接口需要 `x-admin-password: <password>` 头（默认 `admin`）。

## 📁 项目结构

```
camel_chat_proxy/
├── main.py
├── config.json
├── app/
│   ├── core/      # config / auth / http
│   ├── camel/     # client / login / session_pool（纯上游协议）
│   ├── convert/   # openai_conv / anthropic_conv / attachments
│   └── api/       # openai_routes / anthropic_routes / admin_routes / deps
├── tests/         # pytest 测试套件
└── web/index.html # 管理面板（暖米色主题）
```

## 🧰 管理 API

| 端点 | 说明 |
|------|------|
| `GET /admin/config` | 获取配置 |
| `POST /admin/config` | 更新配置 |
| `POST /admin/login` | 单账号登录（可指定 `{"email":"..."}`） |
| `POST /admin/refresh-cookies` | 刷新所有账号 Cookie |
| `POST /admin/test-accounts` | 测试所有账号连接 |
| `POST /admin/refresh-models` | 刷新模型缓存 |

## 📋 可用模型

CaMeL 提供 20 个模型，启动时自动发现。包括 Claude / GPT / Gemini / DeepSeek / Kimi / 生图模型。

## ⚠️ 注意事项

- 本项目仅供学习研究使用
- 请遵守 CaMeL 服务条款，不要滥用免费额度
- 会话不再按 TTL 清理，改为每 10 句（可配置）轮换并遗弃旧会话
- `config.json` 明文存密码，不要分享
- Free 用户仅限 Haiku 模型，付费解锁全部

## 📄 License

MIT
