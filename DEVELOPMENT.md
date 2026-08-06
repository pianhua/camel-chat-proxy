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
| 配置/数据 | dataclass（`app/core/config.py`） |
| 自动登录 | Playwright (Chromium) |
| 前端面板 | 原生 `web/index.html` + 少量 JS |
| 进程管理 | `start.sh` / `start.bat` |
| 测试 | pytest + pytest-asyncio（29 个单元测试） |

## 3. 目录结构

```
camel_chat_proxy/
├── main.py                  # 入口：FastAPI 装配、lifespan 启动登录/关闭
├── config.json              # 本地配置（含密码，不提交 Git）
├── config.example.json      # 配置模板
├── requirements.txt
├── start.sh                 # Linux/macOS/MSYS 启动脚本
├── start.bat                # Windows 启动脚本
├── proxy.log                # 运行日志（默认）
├── app/
│   ├── core/                # 基础设施层（无任何业务依赖）
│   │   ├── config.py        # ConfigManager：多账号、API Key、线程安全、轮询
│   │   ├── auth.py          # 管理面板密码校验
│   │   ├── http.py          # 共享 httpx AsyncClient（http2，超时 600s）
│   │   └── logger.py        # 统一日志（CAMEL_LOG_LEVEL 环境变量控制级别）
│   ├── camel/               # 上游协议层（只懂 CaMeL，不懂 OpenAI/Anthropic）
│   │   ├── client.py        # CamelClient：Cookie 生命周期 + 全部上游端点
│   │   ├── login.py         # Playwright 自动登录，返回 camel_session cookie
│   │   └── session_pool.py  # 会话池：每账号 1 活跃会话，N 句轮换
│   ├── convert/             # 协议转换层（OpenAI/Anthropic ↔ CaMeL payload）
│   │   ├── openai_conv.py   # OpenAI messages → CaMeL completion payload
│   │   ├── anthropic_conv.py# Anthropic → OpenAI 中间格式 + 模型名映射
│   │   └── attachments.py   # 附件管线：URL/base64/本地路径 → data URL 或文本
│   └── api/                 # HTTP 薄层（验权、收参、响应格式化）
│       ├── openai_routes.py     # /v1/models、/v1/chat/completions、/v1/images/generations
│       ├── anthropic_routes.py  # /v1/messages
│       ├── admin_routes.py      # /admin/* 管理端点 + Web 面板
│       └── deps.py              # check_api_key、pick_ready_account、client 注册表
├── tests/                   # pytest（29 个单元测试，覆盖 core/camel/convert/api）
└── web/
    └── index.html           # 管理面板
```

依赖方向（单向，禁止反向）：

```
api → convert → camel → core
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

环境变量 `PORT` 可覆盖端口，默认 5050。日志级别可用 `CAMEL_LOG_LEVEL` 覆盖（默认 INFO）。

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
  "refresh_interval": 21600,
  "session_rotate_turns": 10
}
```

| 字段 | 含义 |
|------|------|
| `api_keys` | 逗号分隔，客户端填任意一个即可 |
| `admin_password` | Web 面板 / 管理 API 密码 |
| `web_search` | 全局联网搜索开关（可在请求中覆盖） |
| `refresh_interval` | Cookie 本地缓存 TTL（秒），默认 6h；**运行时修改立即生效**（`CamelClient.ensure_cookie` 读取此值） |
| `session_rotate_turns` | 每个 CaMeL 会话承载的用户消息数上限，到达后自动换新会话（默认 10） |

> `config.json` 已加入 `.gitignore`，**切勿提交到 Git**。

## 6. 核心模块详解

### 6.1 `app/core/config.py`

`ConfigManager`：线程安全配置管理器（单例 `config_manager`）。

- `validate_api_key(key)`：允许逗号分隔多个 key。
- `get_next_account(require_valid=True)`：轮询负载均衡。若 require_valid=True，会跳过 `is_valid=False` 的账号（最多一圈）。
- `update_config(new_config)`：合并式更新配置并落盘，面板改配置走这里。
- `save()` / `load()`：写/读 `config.json`；`to_dict()` 输出时密码会 mask。

### 6.2 `app/core/http.py` / `auth.py` / `logger.py`

- `get_http_client()`：全局共享 httpx AsyncClient（HTTP/2、超时 600s、连接池 50），**所有上游请求都用它**，不要各自 new。
- `verify_admin()`：管理端点鉴权（`x-admin-password` 头）。
- `get_logger(name)`：统一日志入口，格式 `时间 级别 [模块] 消息`。

### 6.3 `app/camel/login.py`

`playwright_login(email, password)`：无头 Chromium 登录 `https://chat.camel-hub.com/login`，填表提交后从 cookie 里取 `camel_session` 值返回；任何失败返回 `None` 并记录 traceback。

**注意**：登录可能遇到验证码/风控，失败后 `CamelClient` 会把账号标记 `is_valid=False`，后续请求跳过它。

### 6.4 `app/camel/client.py`

`CamelClient`：一个账号对应一个实例（`deps.py` 里按 email 缓存），缓存 `camel_session` Cookie。

- `ensure_cookie(http)`：Cookie 未超过 `refresh_interval` 秒则复用，否则重新登录。
- `create_session(http, title, model)`：`POST /api/chat/sessions`（201 返回 id）。
- `update_session_title(http, session_id, title)`：`PUT /api/chat/sessions/{id}`，失败静默。
- `chat_completion(http, payload)`：非流式 `POST /api/chat/completion`，返回原始文本。
- `stream_chat(http, payload)`：流式 `POST /api/chat/completion`，逐 chunk 产出。
- `generate_image(...)`：`POST /api/chat/image`，返回 data URL 列表。
- `parse_file(http, name, content, mime)`：`POST /api/chat/parse-file`（multipart）。
- `get_models(http)` / `get_usage(http)` / `get_search_limits(http)`：模型 / 用量 / 联网额度。
- `test_connection(http)`：拉模型列表验证账号可用。

**关键约定**：所有方法第一个参数都是共享 `http` 客户端（来自 `get_http_client()`），不要自行创建。这是 2026-07-27 修复会话清理 bug 的教训（旧版曾漏传客户端导致静默失败）。

### 6.5 `app/camel/session_pool.py`

`SessionPool`（单例 `session_pool`）：**每账号 1 个活跃会话**，聊满 `session_rotate_turns` 句自动换新，旧会话直接遗弃不删除（隐蔽优先，模拟真人堆积）。

- `get_session(http, client, model, email)`：取当前活跃会话，没有则创建；模型切换无需换会话（payload 自带 model）。
- `record_turn(email)`：每发一句用户消息计数，达阈值后作废该会话。
- `sync_title(http, client, email, first_user_text)`：每会话仅同步一次标题（取首句前 20 字）。

> `session_pool.rotate_turns` 在配置更新（`POST /admin/config`）时同步刷新。

### 6.6 `app/convert/openai_conv.py`

`build_payload(http, camel, messages, model, session_id, web_search)`：OpenAI messages → CaMeL completion payload。

- `extract_system()`：汇总所有 system 消息，作为 CaMeL `system` 消息插到最前。
- `_convert_one()`：逐条转换 user/assistant 消息；`content` 数组拆分为 text / image_url / file_url；图片经附件管线转 data URL 并以 `![本地图片](data:...)` 拼入文本；文件经 `parse-file` 解析成文本拼入。
- payload 里 `userMessageId` / `assistantMessageId` 用标准 UUID（CaMeL 要求 36 位）。
- 返回对象带 `userText`（纯文本，供标题同步用）与 `hasAttachments`。

### 6.7 `app/convert/anthropic_conv.py`

- `ANTHROPIC_MODEL_MAP`：官方模型名 → CaMeL 内部名（如 `claude-sonnet-4-20250514` → `claude-sonnet-4-6`）。新增/改名需同步更新。
- `to_openai_messages(messages, system)`：Anthropic block 数组（text / image base64|url）→ OpenAI 中间格式，system 参数并入首条。

### 6.8 `app/convert/attachments.py`

统一附件管线，三种来源：data URL（直通）/ http(s) URL（下载转 base64）/ 本地路径（`C:\...`、`/tmp/...`、`file:///C:/...`）。

- `resolve_image_to_data_url(http, url)`：任意来源 → `data:image/...;base64,...`。
- `resolve_file_to_text(http, camel, url)`：文档类文件 → 经 `parse-file` 提取文本；图片返回 `None`（走前者）。

### 6.9 `app/api/`（HTTP 薄层）

| 文件 | 端点 |
|------|------|
| `openai_routes.py` | `GET /v1/models`（1h 缓存）、`POST /v1/chat/completions`（流式/非流式/多模态）、`POST /v1/images/generations` |
| `anthropic_routes.py` | `POST /v1/messages`（流式 SSE 完整事件序列） |
| `admin_routes.py` | `GET/POST /admin/config`、`POST /admin/login`、`/admin/refresh-cookies`、`/admin/test-accounts`、`/admin/refresh-models`、`GET /admin/usage`（30min 缓存）、`GET /health`、`GET /` |
| `deps.py` | `check_api_key`、`pick_ready_account`（取号 + 确保 cookie 就绪）、`get_camel_client`（按 email 缓存实例） |

**OpenAI 流式**：每 chunk 包装成 `chat.completion.chunk`，错误以 error chunk + `[DONE]` 结束。

**Anthropic 流式**：完整事件序列 `message_start → content_block_start → content_block_delta* → content_block_stop → message_delta → message_stop`；任何异常发 `error` 事件而非中断连接。

## 7. 已踩过的坑（必读）

### 7.1 端点不要猜，要抓 curl

- 删除会话端点曾一度写成 `POST /api/chat/conversation/delete`，实际应为 `DELETE /api/chat/sessions/{id}`。
- v4.0 起**不再删除会话**（隐蔽优先），但此教训通用：任何 CaMeL 端点变更，先让浏览器访问一次真操作，从 DevTools 复制 curl 再改代码。

### 7.2 共享 http 客户端必须显式传递

`CamelClient` 的方法都以共享 `http`（`get_http_client()` 返回的全局实例）为第一个参数。旧版后台清理线程曾漏传导致删除失败且异常被吞，修复后确立此约定。

### 7.3 `start.sh` 健康检查

旧版用 `curl ... | python -m json.tool`，在 MSYS/git-bash 下常因找不到 `python` 误报失败。已改为只检查 HTTP 状态码：

```bash
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/health)
```

### 7.4 Cookie 与多账号

- `CamelClient` 实例按 email 复用（`deps.py` 注册表），Cookie 缓存时长由 `refresh_interval` 控制（默认 6h）。
- 多账号轮询时，如果某个账号失败，会被标记 `is_valid=False`，后续请求会跳过它（最多一圈后仍会尝试）。
- 首次登录必须用 Playwright，成功后 cookie 才进入 `camel_session`。

### 7.5 模型名映射

- OpenAI 客户端通常传 `claude-sonnet-4-6`、`gpt-image-2` 等。
- Anthropic 客户端传的是 `claude-sonnet-4-20250514` 等官方名，由 `anthropic_conv.py` 映射；生图别名（`dall-e-3` → `gpt-image-2`）在 `openai_routes.py` 的 `IMAGE_MODEL_MAP`。
- 若 CaMeL 新增模型或改名，需要同步更新这两处。

### 7.6 文件上传

- 支持 `http://`、`https://`、`file:///` 和 Windows 绝对路径。
- 图片文件会直接内联 base64；其他文件走 `parse-file` 提取文本后拼到 user 消息里。
- 上传失败仅记日志，不会中断请求。

## 8. 调试与测试

### 8.1 快速验证代理是否健康

```bash
curl -s http://localhost:5050/health
```

应返回 JSON：`{"status":"ok", "accounts": [...], ...}`。

### 8.2 运行单元测试

```bash
python -m pytest -v     # 29 个测试，覆盖 core / camel / convert / api
```

### 8.3 测试聊天

```bash
curl -X POST http://localhost:5050/v1/chat/completions \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"hello"}]}'
```

### 8.4 测试生图

```bash
curl -X POST http://localhost:5050/v1/images/generations \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"a cat","n":1,"size":"1024x1024"}'
```

### 8.5 查看日志

```bash
tail -f proxy.log        # 日志格式：时间 级别 [模块] 消息
CAMEL_LOG_LEVEL=DEBUG python main.py   # 临时调高日志级别
```

## 9. 常见 CaMeL 端点清单（当前已知，需持续验证）

| 操作 | 方法 | 端点 |
|------|------|------|
| 登录 | Playwright 模拟 | `https://chat.camel-hub.com/login` |
| 模型列表 | GET | `/api/chat/models` |
| 创建会话 | POST | `/api/chat/sessions` |
| 更新会话标题 | PUT | `/api/chat/sessions/{id}` |
| 聊天补全 | POST | `/api/chat/completion` |
| 生图 | POST | `/api/chat/image` |
| 解析文件 | POST | `/api/chat/parse-file` |
| 用量 | GET | `/api/chat/usage` |
| 联网搜索额度 | GET | `/api/chat/search` |

> 所有请求都需要 `camel_session` cookie 和正确 `origin/referer` 头。

## 10. 未来 TODO / 可扩展方向

1. **更稳定的 Cookie 刷新**：CaMeL 登录可能遇到验证码、风控。可考虑增加 retry、失败通知、手动登录 URL。
2. **Token 用量**：目前返回固定 `prompt_tokens: 0`，CaMeL 若返回真实用量可接入。
3. **图片 URL 持久化**：当前生图返回 base64，因为 CaMeL 没有持久图片 URL。可接入外部图床或本地文件服务。
4. **速率限制 / 并发控制**：目前无队列，高并发可能触发 CaMeL 限流。可加 semaphore。
5. **账号健康监控**：后台可定期轮询 `/api/chat/models` 并把结果写到 `/health`。
6. **Web 面板增强**：目前 `index.html` 是单文件，可拆分为 Vue/React 或增加日志实时查看。
7. **会话策略**：当前每账号 1 活跃会话、按句数轮换。若 CaMeL 支持长期对话，可考虑按客户端 `session_id` 保持上下文。

## 11. 版本与提交历史

- `v4.0.0`：当前版本。四层架构（core/camel/convert/api）、会话智能轮换、Anthropic 流式完整事件、统一日志。
- 近期维护项：
  - Cookie TTL 读取 `refresh_interval` 配置（不再硬编码）。
  - Anthropic 流式补全 `message_start` / `content_block_start` / `content_block_stop` / `message_delta` 事件。
  - 弃用 `@app.on_event`，改用 lifespan。
  - 裸 `print` 全面替换为 `logging`（`app/core/logger.py`）。
  - `start.bat` 入口修正为 `main.py`。

---

**维护规则**：凡涉及 CaMeL 端点、请求体、响应体、模型名的改动，先抓真实浏览器 curl，再改代码；改完务必运行 `python -m pytest -v`、`curl /health` 和至少一次真实聊天/生图请求验证。
