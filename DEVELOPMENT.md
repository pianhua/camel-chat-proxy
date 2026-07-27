# CaMeL Chat Proxy — 开发交接文档

> 本文档供后续 AI/开发者继续维护本项目。阅读前请确认已掌握 `config.json` 中的账号配置，且本机可访问 `https://chat.camel-hub.com`。

## 1. 项目定位

将免费网页端 [CaMeL Chat](https://chat.camel-hub.com/) 逆向为 **OpenAI 兼容 + Anthropic Messages 兼容**的本地 API 代理，供 SillyTavern、RikkaHub 等客户端使用。

- **所有 CaMeL 能力都来自网页逆向**，随时可能因官方前端变更而失效。
- **核心原则**：不要猜测 CaMeL 端点或字段；任何 CaMeL 端点改动都需要用浏览器 DevTools 抓真实 curl 后再修改。

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| HTTP 客户端 | httpx（AsyncClient，启用 http2） |
| 配置/数据 | Pydantic v2 + dataclass |
| 自动登录 | Playwright (Chromium) |
| 前端面板 | 原生 `web/index.html` + 少量 JS |
| 进程管理 | `start.sh` / `start.bat`（nohup 后台） |

## 3. 目录结构

```
camel_chat_proxy/
├── main.py                  # 入口：FastAPI 装配、启动事件、后台清理线程
├── config.json              # 本地配置（含密码，不提交 Git）
├── config.example.json      # 配置模板
├── requirements.txt
├── start.sh                 # Linux/macOS/MSYS 启动脚本
├── start.bat                # Windows 启动脚本
├── proxy.log                # 运行日志（默认）
├── app/
│   ├── routes.py            # OpenAI 兼容路由 + 图片生成 + 管理 API + Web 面板
│   ├── anthropic_routes.py  # Anthropic /v1/messages 兼容
│   ├── camel_client.py      # CaMeL 网页 API 客户端：登录、会话、聊天、删会话、上传文件
│   ├── config.py            # ConfigManager：多账号、API Key、线程安全、轮询
│   ├── models.py            # Pydantic 模型（OpenAI/Anthropic 请求体）
│   ├── auth.py              # 管理面板密码校验
│   └── session_store.py     # 账号×模型 会话缓存 + TTL 过期追踪
└── web/
    └── index.html           # 管理面板
```

## 4. 启动方式

```bash
# 首次安装
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json
# 编辑 config.json 填入账号

# 前台运行（调试）
python main.py

# 后台运行（推荐）
bash start.sh        # MSYS / git-bash / Linux / macOS
start.bat            # Windows CMD
```

`start.sh` 会：
1. 杀掉 5050 端口旧进程
2. `nohup python main.py > proxy.log 2>&1 &`
3. 轮询 `curl /health` 直到 HTTP 200

环境变量 `PORT` 可覆盖端口，默认 5050。

## 5. 配置说明（config.json）

```json
{
  "api_keys": "sk-camel",
  "admin_password": "admin",
  "camel_accounts": [
    {
      "email": "...",
      "password": "...",
      "is_valid": false,
      "login_time": "",
      "last_test": ""
    }
  ],
  "web_search": false,
  "refresh_interval": 21600
}
```

| 字段 | 含义 |
|------|------|
| `api_keys` | 逗号分隔，客户端填任意一个即可 |
| `admin_password` | Web 面板 / 管理 API 密码 |
| `web_search` | 全局联网搜索开关（可在请求中覆盖） |
| `refresh_interval` | Cookie 本地缓存 TTL（秒），默认 6h |

> `config.json` 已加入 `.gitignore`，**切勿提交到 Git**。

## 6. 核心模块详解

### 6.1 `app/camel_client.py`

`CamelClient`：一个账号对应一个实例，缓存 `camel_session` Cookie。

- `CAMEL_BASE = "https://chat.camel-hub.com"`
- `refresh_cookie(http_client)`：Playwright 自动登录，获取 `camel_session` cookie。
- `ensure_cookie(http_client)`：若 Cookie 未过期则复用，否则调用 `refresh_cookie`。
- `create_session(http_client, title, model)`：**创建会话，端点 `POST /api/chat/sessions`**。
- `chat_completion(http_client, ...)`：非流式请求 `POST /api/chat/completion`。
- `stream_chat(http_client, ...)`：流式请求 `POST /api/chat/completion`。
- `delete_conversation(http_client, conversation_id)`：**删除会话，端点 `DELETE /api/chat/sessions/{conversation_id}`**。
- `get_models(http_client)`：获取模型列表，端点 `GET /api/chat/models`。
- `parse_file(http_client, ...)`：上传文件到 `POST /api/chat/parse-file`。

**关键约定**：`http_client` 必须显式传入，不要依赖全局变量。这是 2026-07-27 修复会话清理 bug 的教训。

**system prompt 处理**：当前实现把 system prompt 拼接到**第一条 user 消息**前面，形如 `[System instructions]\n{system}\n\n{content}`。这是 CaMeL 网页没有独立 system role 的折中方案。若官方以后支持 system 字段，应优先使用官方字段。

### 6.2 `app/session_store.py`

按 `(account_email, model)` 缓存 `session_id`，默认 TTL 1 小时（3600s）。

- `get(email, model)`：取未过期 session_id；过期会删除。
- `set(email, model, session_id)`：写入缓存。
- `get_all_expired()`：返回所有已过期条目 `(email, session_id)`，用于后台清理线程调用 CaMeL 删除接口。

### 6.3 `app/config.py`

`ConfigManager`：线程安全配置管理器。

- `validate_api_key(key)`：允许逗号分隔多个 key。
- `get_next_account(require_valid=True)`：轮询负载均衡。若 require_valid=True，会跳过 `is_valid=False` 的账号（最多一圈）。
- `save()`：把配置写回 `config.json`，会 mask 密码（但文件里仍明文存 password，只是 `to_dict()` 输出 mask）。

### 6.4 `app/routes.py`

OpenAI 兼容接口：

| 端点 | 说明 |
|------|------|
| `GET /v1/models` | 模型列表（缓存 1h） |
| `POST /v1/chat/completions` | 聊天补全，支持流式/非流式、system prompt、多模态、文件 URL、本地文件 |
| `POST /v1/images/generations` | 生图（映射到 gpt-image-2 / gemini-3-pro-image-preview） |
| `GET /health` | 健康检查 + 账号状态 |
| 管理接口 `/admin/*` | 配置/登录/刷新 Cookie/测试账号/刷新模型 |
| `GET /` | 返回 Web 面板 |

**多模态处理**：`_parse_multimodal` 支持 OpenAI 标准格式 `content` 为数组：`type=text`、`type=image_url`、`type=file_url`。图片 URL 会直接拼进文本；本地文件会读取并上传到 `parse-file`；图片文件会转 base64 内联。

**图片生成**：`images_generations` 为每张图创建**临时 session**，生成完成后立即 `delete_conversation` 清理，避免侧边栏堆积。

**会话复用**：`chat/completions` 会先看请求体 `session_id`，再看 `session_store.get()`，最后才创建新 session。命中缓存可让同一对话保持上下文，但 CaMeL 服务端通常会把同一会话里的历史也带进去，所以实际效果取决于 CaMeL 行为。

### 6.5 `app/anthropic_routes.py`

Anthropic Messages API 适配。

- 将 Anthropic 模型名映射到 CaMeL 内部名，例如 `claude-sonnet-4-20250514` → `claude-sonnet-4-6`。
- 每次请求都新建 session，不清理（清理工作由后台线程或后续改进处理）。

## 7. 已踩过的坑（必读）

### 7.1 端点不要猜，要抓 curl

- 删除会话端点曾一度写成 `POST /api/chat/conversation/delete`，实际应为 `DELETE /api/chat/sessions/{id}`。
- 任何 CaMeL 端点变更，先让浏览器访问一次真操作，从 DevTools 复制 curl 再改代码。

### 7.2 `http_client` 必须显式传递

`main.py` 的后台清理线程曾写成 `camel.delete_conversation(session_id)`，漏传 `http_client`，导致删除失败且异常被吞。修复后：

```python
await camel.delete_conversation(http_client, session_id)
```

所有 `CamelClient` 的异步方法都应以 `http_client` 为第一个参数。

### 7.3 `start.sh` 健康检查

旧版用 `curl ... | python -m json.tool`，在 MSYS/git-bash 下常因找不到 `python` 误报失败。已改为只检查 HTTP 状态码：

```bash
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/health)
```

### 7.4 Cookie 与多账号

- `CamelClient` 实例按 email 复用，Cookie 缓存 6 小时（`refresh_interval`）。
- 多账号轮询时，如果某个账号失败，会被标记 `is_valid=False`，后续请求会跳过它（最多一圈后仍会尝试）。
- 首次登录必须用 Playwright，成功后 cookie 才进入 `camel_session`。

### 7.5 模型名映射

- OpenAI 客户端通常传 `claude-sonnet-4-6`、`gpt-image-2` 等。
- Anthropic 客户端传的是 `claude-sonnet-4-20250514` 等官方名，由 `anthropic_routes.py` 映射。
- 若 CaMeL 新增模型或改名，需要更新 `ANTHROPIC_MODEL_MAP` 和 `IMAGE_MODEL_MAP`。

### 7.6 文件上传

- 支持 `http://`、`https://`、`file:///` 和 Windows 绝对路径。
- 图片文件会直接内联 base64；其他文件走 `parse-file` 提取文本后拼到 user 消息里。
- 上传失败仅打印日志，不会中断请求。

## 8. 调试与测试

### 8.1 快速验证代理是否健康

```bash
curl -s http://localhost:5050/health | head
```

应返回 JSON：`{"status":"ok", "accounts": [...], ...}`。

### 8.2 测试聊天

```bash
curl -X POST http://localhost:5050/v1/chat/completions \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"hello"}]}'
```

### 8.3 测试生图

```bash
curl -X POST http://localhost:5050/v1/images/generations \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"a cat","n":1,"size":"1024x1024"}'
```

### 8.4 查看日志

```bash
tail -f proxy.log
```

### 8.5 测试会话清理

临时脚本示例：

```python
import asyncio
from app.session_store import session_store
from app.routes import _get_http_client, _get_camel_client
from app.config import config_manager
from app.camel_client import CamelClient

async def test():
    store = SessionStore(ttl=0)
    store.set("a@example.com", "claude-sonnet-4-6", "session_id_to_delete")
    # 手动把 created_at 改成很久以前
    store._sessions[store._make_key("a@example.com", "claude-sonnet-4-6")]["created_at"] = 0

    expired = store.get_all_expired()
    print(expired)

    if expired:
        http_client = await _get_http_client()
        for email, sid in expired:
            for acc in config_manager.config.camel_accounts:
                if acc.email == email:
                    camel = _get_camel_client(acc)
                    await camel.ensure_cookie(http_client)
                    ok = await camel.delete_conversation(http_client, sid)
                    print("deleted:", ok)

asyncio.run(test())
```

## 9. 常见 CaMeL 端点清单（当前已知，需持续验证）

| 操作 | 方法 | 端点 |
|------|------|------|
| 登录 | Playwright 模拟 | `https://chat.camel-hub.com/login` |
| 模型列表 | GET | `/api/chat/models` |
| 创建会话 | POST | `/api/chat/sessions` |
| 聊天补全 | POST | `/api/chat/completion` |
| 删除会话 | DELETE | `/api/chat/sessions/{id}` |
| 解析文件 | POST | `/api/chat/parse-file` |

> 所有请求都需要 `camel_session` cookie 和正确 `origin/referer` 头。

## 10. 未来 TODO / 可扩展方向

1. **日志等级**：当前大量使用 `print()`，建议接入 `logging` 并按级别输出。
2. **更稳定的 Cookie 刷新**：CaMeL 登录可能遇到验证码、风控。可考虑增加 retry、失败通知、手动登录 URL。
3. **会话复用策略**：当前按 `(email, model)` 缓存。若 CaMeL 支持长期对话，可考虑按 `conversation_id` 或客户端 `session_id` 缓存。
4. **Token 用量**：目前返回固定 `prompt_tokens: 0`，CaMeL 若返回真实用量可接入。
5. **Anthropic 消息流**：已实现，但 `message_stop` 等事件格式需根据 RikkaHub 最新要求更新。
6. **图片 URL 持久化**：当前 `response_format=url` 也返回 base64，因为 CaMeL 没有持久图片 URL。可接入外部图床或本地文件服务。
7. **速率限制 / 并发控制**：目前无队列，高并发可能触发 CaMeL 限流。可加 semaphore。
8. **账号健康监控**：后台可定期轮询 `/api/chat/models` 并把结果写到 `/health`。
9. **Web 面板增强**：目前 `index.html` 是单文件，可拆分为 Vue/React 或增加日志实时查看。
10. **测试套件**：建议添加 `pytest` 单元测试，覆盖 key 校验、模型映射、session store TTL、多账号轮询。

## 11. 版本与提交历史

- `v3.0.0`：当前版本。
- 最近提交：
  - 修复会话清理端点与 `http_client` 传递问题。
  - 修复 `start.sh` 健康检查误报。
  - 生图后自动删除临时会话。

---

**维护规则**：凡涉及 CaMeL 端点、请求体、响应体、模型名的改动，先抓真实浏览器 curl，再改代码；改完务必运行 `curl /health` 和至少一次真实聊天/生图请求验证。
