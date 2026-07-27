# CaMeL Chat Proxy 全面重构设计文档

日期：2026-07-27
状态：已获主人批准

## 背景

当前代理（v3）因最初端点未确认，存在多个致命协议错误（UUID 格式、生图端点、多模态丢弃等），导致大量功能不可用。Gemini 通过 Chrome DevTools 逆向调研产出了权威 API 文档（`camel_chat_api_documentation.md`），本设计以该文档为准绳进行全面重构。

## 设计原则

1. **隐蔽优先**：所有行为模拟真实浏览器用户，不轮询、不频繁增删会话、不暴露反代特征
2. **功能只增不减**：自动 Cookie、Web 管理面板、多账号轮询、会话管理、生图、文件图片上传全部保留
3. **分层单向依赖**：`api → convert → camel`，禁止反向依赖，每文件 <300 行

## 确认的需求决策

| 决策点 | 结论 |
|---|---|
| WebUI 技术 | 单文件原生 HTML/CSS/JS，无框架无构建 |
| 配色主题 | Claude 式暖米色浅色主题（仅浅色），布局按管理台自行设计 |
| 新功能 | 用量监控、会话标题同步、图生图/参考图、联网搜索额度 |
| 会话策略 | 新会话聊满 10 句用户消息即换新会话（阈值可配置），旧会话遗弃不删除 |
| 会话内切模型 | 支持（completion payload 每次自带 model 字段） |
| 代码结构 | 全面拆分 |

## 目标文件结构

```
camel_chat_proxy/
├── main.py                    # 入口：装配路由、启动登录、后台任务
├── config.json                # 配置（不提交）
├── app/
│   ├── core/                  # 基础设施
│   │   ├── config.py          # 配置管理（新增 session_rotate_turns 等字段）
│   │   ├── auth.py            # 管理密码验证
│   │   └── http.py            # 共享 httpx.AsyncClient
│   ├── camel/                 # CaMeL 上游层（纯协议）
│   │   ├── client.py          # completion / image / parse-file / sessions CRUD / usage / search
│   │   ├── login.py           # Playwright 自动登录、Cookie 生命周期
│   │   └── session_pool.py    # 会话池：按账号 1 活跃会话，10 句轮换
│   ├── convert/               # 协议转换层
│   │   ├── openai_conv.py     # OpenAI messages → CaMeL payload
│   │   ├── anthropic_conv.py  # Anthropic content blocks → CaMeL payload
│   │   └── attachments.py     # 图片/文件统一处理（base64 内联、parse-file）
│   ├── api/                   # HTTP 薄层（验权、收参、格式化响应）
│   │   ├── openai_routes.py   # /v1/models /v1/chat/completions /v1/images/generations
│   │   ├── anthropic_routes.py# /v1/messages
│   │   ├── admin_routes.py    # /admin/* 全部管理接口
│   │   └── deps.py            # API key 验证、账号选取共享依赖
│   └── web/
│       └── index.html         # 管理面板（单文件，Claude 暖米色主题）
└── tests/                     # pytest：converter 单测 + 端点集成测试
```

## 核心修复清单

| 现有问题 | 修复方案 |
|---|---|
| UUID 用 `uuid4().hex[:32]`（32位无连字符） | 全部改 `str(uuid.uuid4())` 标准 36 位（文档硬性要求） |
| 生图走 `/api/chat/completion` + 正则抠 base64 | 改走专用 `/api/chat/image`，payload `{prompt,model,providerId,sessionId,size,n,priorImages}`；size 映射 `1024x1024→1:1`、`1792x1024→16:9`、`1024x1792→9:16`、其他→`auto`；返回 `images[].url`（data URL）转 OpenAI `b64_json` |
| OpenAI `image_url` 图片被收集后丢弃 | 统一附件管线：http URL 下载 / data URL 直用 → 按文档格式拼入 content（`[file: 文件名]` + `![本地图片](data:...)`），`hasAttachments=true` |
| `routes.py` 双取账号导致轮询错乱 | 每次请求只取一次账号贯穿始终 |
| `"\\n".join(...)` 字面量错误 | 修正为 `"\n".join(...)` |
| Anthropic content blocks 数组透传报错 | 支持 text / image block 数组解析，image block（base64 source）走统一附件管线 |
| system prompt 注入首条 user 消息且原地改列表 | 不污染 user 消息，按 `hasCustomSystemPrompt` 语义处理（若抓包确认有独立字段则用独立字段） |
| `ensure_cookie` 失败变裸 500 | 返回干净的 503 JSON 错误 |
| Anthropic 端点每请求新建会话不删除 | 统一走会话池 |

## 会话池设计（隐蔽优先）

- 每账号维护 1 个活跃会话 + 用户消息计数器
- 满 `session_rotate_turns`（默认 10，配置可调）句用户消息 → 创建新会话替换
- 换下的会话**直接遗弃，不调用 DELETE**（真实用户也会堆积会话）
- 每会话首条助手回复后，异步 `PUT /api/chat/sessions/{id}` 同步标题（取首句用户消息前 20 字，模拟真人）
- 取消后台定时删除任务（减少请求 churn）
- 会话池为内存态，进程重启后懒重建

## 新功能

1. **用量监控**：接入 `GET /api/chat/usage`、`GET /api/chat/search`；新增 `GET /admin/usage` 聚合全部账号。**仅手动点击时拉取 + 30 分钟缓存**，绝不自动轮询
2. **图生图/参考图**：`/v1/images/generations` 请求体扩展可选 `prior_images` 字段（base64 或 URL 数组）→ 映射 CaMeL `priorImages`
3. **会话标题同步**：见会话池设计

## WebUI 设计

- 单文件 `app/web/index.html`，全手写 CSS，零依赖
- 配色：米白底 `#f7f5f0`、卡片 `#fffdf9`、暖深灰文字 `#3d3929`、珊瑚橙强调 `#d97757`、成功绿 `#6b9e78`
- 布局：顶部标题栏 → 统计卡片行（账号在线数 / 模型数 / 今日请求数）→ 账号管理表 → 用量面板 → 设置区
- 圆角卡片、柔和阴影、状态圆点、悬停过渡
- 保留全部现有功能：增删账号、刷 Cookie、测连接、改 API Key/管理密码、刷模型；新增用量查看按钮

## 验证方案

- converter 层 pytest 单测：payload 结构逐字段对照调研文档样例
- 每端点完成后 curl 实测：`/v1/models` → 文本对话（流式/非流式）→ 图片对话 → 生图（文生图/图生图）→ `/v1/messages`（流式/非流式）
- 回归确认：WebUI 全部按钮功能正常

## 明确不做（YAGNI）

- 深色模式
- 前端框架 / 构建工具
- 按客户端对话映射 CaMeL 会话
- 用量数据的历史持久化与图表
