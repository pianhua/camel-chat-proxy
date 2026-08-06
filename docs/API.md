# CaMeL Chat Proxy 调用文档

> 版本：v4.0.0 · 默认地址：`http://localhost:5050` · 默认 API Key：`sk-camel`

本文档列出代理全部对外端点的调用方式、请求/响应样例与错误码。

---

## 目录

- [1. 认证方式](#1-认证方式)
- [2. OpenAI 兼容端点](#2-openai-兼容端点)
  - [2.1 列出模型 GET /v1/models](#21-列出模型-get-v1models)
  - [2.2 聊天补全 POST /v1/chat/completions](#22-聊天补全-post-v1chatcompletions)
  - [2.3 多模态（图片/文件）](#23-多模态图片文件)
  - [2.4 图片生成 POST /v1/images/generations](#24-图片生成-post-v1imagesgenerations)
- [3. Anthropic 兼容端点 POST /v1/messages](#3-anthropic-兼容端点-post-v1messages)
- [4. 管理端点](#4-管理端点)
- [5. 错误码](#5-错误码)
- [6. 客户端示例](#6-客户端示例)
- [7. 行为说明（必读）](#7-行为说明必读)

---

## 1. 认证方式

所有 `/v1/*` 端点支持两种鉴权头（二选一）：

```http
Authorization: Bearer sk-camel
```

```http
x-api-key: sk-camel
```

管理端点 `/admin/*` 使用管理密码（默认 `admin`）：

```http
x-admin-password: admin
```

失败返回 `401`：

```json
{"detail": {"error": {"message": "invalid api key"}}}
```

---

## 2. OpenAI 兼容端点

### 2.1 列出模型 `GET /v1/models`

从 CaMeL 实时发现（1 小时缓存），返回 chat + image 类型模型。

```bash
curl http://localhost:5050/v1/models -H "Authorization: Bearer sk-camel"
```

响应：

```json
{
  "object": "list",
  "data": [
    {"id": "claude-opus-4-7", "object": "model", "created": 0, "owned_by": "CaMeL"},
    {"id": "gpt-image-2", "object": "model", "created": 0, "owned_by": "CaMeL"}
  ]
}
```

### 2.2 聊天补全 `POST /v1/chat/completions`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 模型 ID，如 `claude-opus-4-7`、`claude-haiku-4-5` |
| `messages` | array | 是 | OpenAI 消息数组（system/user/assistant） |
| `stream` | bool | 否 | 默认 `false` |
| `web_search` | bool | 否 | 联网搜索，默认取全局配置 |
| `temperature` / `top_p` / `max_tokens` / `frequency_penalty` / `presence_penalty` | number | 否 | 采样参数，透传上游 `sampling`（CaMeL 专家模式支持） |
| `message_mode` | string | 否 | `auto`（默认，按模型实测折叠）/ `native` / `compact` |

> **消息折叠说明**：`claude-sonnet-4-6` / `claude-haiku-4-5` 实测不读取 messages 数组历史（只认最后一条 user + system）。`auto` 模式下代理自动把 system + 历史折叠进最后一条 user（DS2API 风格角色标记），其余模型保持原生数组。

> **usage 说明**：上游不返回 token 统计，代理按完整 prompt/输出字符数粗估（约 4 字符/token）。

```bash
curl -X POST http://localhost:5050/v1/chat/completions \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-haiku-4-5",
    "messages": [
      {"role": "system", "content": "你是助手"},
      {"role": "user", "content": "用一句话介绍骆驼"}
    ]
  }'
```

**非流式响应：**

```json
{
  "id": "chatcmpl-2e032f10-...",
  "object": "chat.completion",
  "created": 0,
  "model": "claude-haiku-4-5",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "骆驼是适应沙漠环境的大型哺乳动物……"},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

> `usage` 恒为 0（上游不返回 token 统计）；`created` 恒为 0。

**流式响应**（`"stream": true`）：标准 SSE，每个 chunk：

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":0,"model":"...","choices":[{"index":0,"delta":{"content":"骆驼"},"finish_reason":null}]}

data: [DONE]
```

上游出错时流内返回错误 chunk 后以 `[DONE]` 结束：

```
data: {"error": {"message": "CaMeL API error: ..."}}
data: [DONE]
```

### 2.3 多模态（图片/文件）

`content` 数组中支持 `text`、`image_url`、`file_url` 三种 part：

```json
{
  "model": "claude-opus-4-7",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "描述这张图"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0K..."}},
      {"type": "file_url", "file_url": {"url": "https://example.com/report.pdf"}}
    ]
  }]
}
```

**`image_url` 支持的来源：**

| 形式 | 示例 |
|------|------|
| data URL（推荐） | `data:image/png;base64,iVBOR...` |
| http(s) URL | `https://example.com/a.png`（代理下载后转 base64） |
| 本地路径 | `C:\pic\a.png`、`/tmp/a.png`、`file:///C:/pic/a.png` |

**`file_url`**（文档类附件）：支持 http URL / 本地路径，经 CaMeL `/api/chat/parse-file` 解析为文本后拼入消息。`.txt .md .json .py .js .html .css .csv .xml .pdf .doc .docx` 均有 MIME 映射；图片类型请用 `image_url`。

字符串形式的消息也支持 Markdown 语法附件：

```
看看这个 ![img](https://example.com/a.png) 和 [📎file](https://example.com/a.txt)
```

### 2.4 图片生成 `POST /v1/images/generations`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 提示词 |
| `model` | string | 否 | `gpt-image-2`（默认）、`gemini-3-pro-image-preview`，别名 `dall-e-3`/`dall-e-2` → `gpt-image-2` |
| `size` | string | 否 | `1024x1024`→1:1（默认）、`1792x1024`→16:9、`1024x1792`→9:16，其他→auto |
| `n` | int | 否 | 1-4，默认 1 |
| `prior_images` | array | 否 | 参考图（图生图），元素支持 data URL / http URL / 本地路径 |

```bash
curl -X POST http://localhost:5050/v1/images/generations \
  -H "Authorization: Bearer sk-camel" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"一只骆驼","size":"1024x1024","n":1}'
```

**响应**（恒为 b64_json）：

```json
{
  "created": 1785126660,
  "data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUg..."}]
}
```

**图生图示例：**

```json
{
  "model": "gpt-image-2",
  "prompt": "高清修复一下，其他内容不变",
  "prior_images": ["data:image/png;base64,iVBORw0K..."]
}
```

---

## 3. Anthropic 兼容端点 `POST /v1/messages`

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 模型 ID；旧名自动映射（见下表） |
| `messages` | array | 是 | Anthropic 消息数组，content 支持字符串或 block 数组 |
| `system` | string/array | 否 | 系统提示 |
| `max_tokens` | int | 否 | 透传上游（专家模式采样） |
| `temperature` / `top_p` / `top_k` / `frequency_penalty` / `presence_penalty` | number | 否 | 透传上游 `sampling` |
| `stream` | bool | 否 | 默认 `false` |
| `web_search` | bool | 否 | 默认取全局配置 |
| `message_mode` | string | 否 | `auto`（默认）/ `native` / `compact` |

**模型名映射：**

| 传入 | 实际 |
|------|------|
| `claude-sonnet-4-20250514` / `claude-sonnet-4-5` / `claude-3-5-sonnet-20241022` | `claude-sonnet-4-6` |
| `claude-opus-4-20250514` / `claude-opus-4-5` / `claude-opus-4-1` | `claude-opus-4-7` |
| `claude-haiku-4-5-20251001` / `claude-3-5-haiku-20241022` | `claude-haiku-4-5` |

```bash
curl -X POST http://localhost:5050/v1/messages \
  -H "x-api-key: sk-camel" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "system": "你是助手",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

**图片 block：**

```json
{"role": "user", "content": [
  {"type": "text", "text": "看图"},
  {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR..."}}
]}
```

**非流式响应：**

```json
{
  "id": "msg_2e032f10-...",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Hello! ..."}],
  "model": "claude-sonnet-4-6",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

**流式响应**：完整 SSE 事件序列 `message_start` → `content_block_start` → `content_block_delta`（文本增量）→ `content_block_stop` → `message_delta` → `message_stop`；出错时发 `error` 事件。

---

## 4. 管理端点

均需 `x-admin-password` 头。

### `GET /admin/config` / `POST /admin/config`

获取 / 合并式更新配置（只传要改的字段即可，运行时生效）：

```bash
curl -X POST http://localhost:5050/admin/config \
  -H "x-admin-password: admin" -H "Content-Type: application/json" \
  -d '{"session_rotate_turns": 20, "web_search": true}'
```

### `POST /admin/login`

触发账号（重新）登录。不带 body 则轮询一个账号：

```bash
curl -X POST http://localhost:5050/admin/login \
  -H "x-admin-password: admin" -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'
```

### `POST /admin/refresh-cookies` · `POST /admin/test-accounts` · `POST /admin/refresh-models`

刷新全部账号 Cookie / 测试全部账号 / 刷新模型缓存，均为空 body POST。

### `GET /admin/usage`

聚合全部账号用量与联网搜索额度。**仅手动调用**，结果缓存 30 分钟：

```bash
curl http://localhost:5050/admin/usage -H "x-admin-password: admin"
```

```json
{
  "status": "ok",
  "cached": false,
  "accounts": [
    {
      "email": "user@example.com",
      "usage": {"...": "上游原始用量数据"},
      "search": {"used": 0, "limit": 5, "remaining": 5}
    }
  ]
}
```

某账号失败时对应行返回 `{"email": "...", "error": "..."}`。

### `GET /admin/logs`

返回最近日志（内存环形缓冲，最多 300 条），供管理面板实时查看：

```bash
curl http://localhost:5050/admin/logs -H "x-admin-password: admin"
```

```json
{
  "status": "ok",
  "logs": ["17:16:01 INFO [main] [Startup] Models pre-loaded", "..."]
}
```

### `GET /health`（无需鉴权）

```json
{
  "status": "ok",
  "accounts": [{"email": "user@example.com", "cookie_ready": true, "is_valid": true, "login_time": "07-27 12:00"}],
  "model_count": 20,
  "models_cached": true
}
```

---

## 5. 错误码

| 状态码 | 含义 |
|--------|------|
| 400 | 请求体缺字段（`messages required` / `prompt is required`） |
| 401 | API Key 或管理密码错误 |
| 403 | 管理密码错误 |
| 404 | 资源不存在（如指定邮箱的账号未配置） |
| 503 | 无可用账号 / 账号登录失败 / Cookie 未就绪 |
| 5xx | 上游 CaMeL 错误透传（`CaMeL API error <status>: <body>`） |

---

## 6. 客户端示例

### Python（openai SDK）

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5050/v1", api_key="sk-camel")

resp = client.chat.completions.create(
    model="claude-opus-4-7",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

### Python（anthropic SDK）

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://localhost:5050", api_key="sk-camel")

msg = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=100,
    messages=[{"role": "user", "content": "Hello"}],
)
print(msg.content[0].text)
```

官方 SDK 的流式模式同样可用（事件序列完整）。

### JavaScript（fetch 流式）

```js
const resp = await fetch("http://localhost:5050/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer sk-camel",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "claude-haiku-4-5",
    messages: [{ role: "user", content: "你好" }],
    stream: true,
  }),
});

const reader = resp.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  for (const line of decoder.decode(value).split("\n")) {
    if (line.startsWith("data: ") && line !== "data: [DONE]") {
      process.stdout.write(JSON.parse(line.slice(6)).choices[0].delta.content ?? "");
    }
  }
}
```

### SillyTavern

| 配置项 | 值 |
|--------|-----|
| API 来源 | Custom (OpenAI-compatible) |
| API URL | `http://localhost:5050/v1` |
| API Key | `sk-camel` |

### RikkaHub

选 Anthropic 协议，Base URL `http://localhost:5050`，API Key `sk-camel`。

---

## 7. 行为说明（必读）

1. **会话轮换**：代理按账号维护一个 CaMeL 会话，每 `session_rotate_turns`（默认 10）句用户消息自动换新。CaMeL 网页端会看到会话堆积，属正常现象（与真人使用一致）。会话标题会取首句前 20 字自动同步。
2. **多账号轮询**：每个请求轮询选取下一个有效账号；某账号登录失败会标记失效并跳过。
3. **Cookie 生命周期**：有效期 6 小时，到期自动用 Playwright 重新登录，无需人工干预。
4. **采样参数**：`temperature` / `top_p` / `max_tokens` / `frequency_penalty` / `presence_penalty` 会透传上游 `sampling` 对象（CaMeL 专家模式支持）；`top_k` 等未声明参数仍被忽略。
5. **附件安全**：`image_url` / `file_url` 允许本地路径，意味着任何持有 API Key 的客户端都能让服务端读取本地文件——**请勿将代理无鉴权暴露到公网**。
6. **不轮询上游**：除聊天/生图请求本身外，代理不会主动请求 CaMeL（模型列表 1h 缓存、用量 30min 缓存且仅手动触发），以降低封号风险。
