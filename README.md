# CaMeL Chat Proxy

高性能、低风控的 CaMeL Chat 逆向代理服务，将 Web 端能力转换为标准的 OpenAI 与 Anthropic 兼容 API。

内置 **Mihomo 代理桥（一号一 IP 防连坐）**、**模型别名映射**、**多模态附件解析**、**并发槽位调度** 与 **在线 API 调试器 (Playground)**。

---

## 核心架构特性

- **双协议标准化兼容**：
  - OpenAI 格式：`/v1/chat/completions`（流式/非流式）、`/v1/models`、`/v1/images/generations`
  - Anthropic 格式：`/v1/messages`（SSE 事件流）
- **Mihomo 代理桥（一号一 IP 防连坐）**：
  - 支持解析 Clash YAML、Base64 订阅及 `vmess://`、`ss://`、`trojan://`、`vless://` 节点链接；
  - 自动管理本地 Mihomo 子进程，为每个节点分配独占本地入站端口（`127.0.0.1:10801+`）；
  - 支持节点延迟批量测速与一键分配，确保不同账号严格使用独立出口 IP。
- **双通道认证保障**：
  - **手动 Cookie（推荐）**：支持在配置或 Web 控制台直接录入 `camel_session` Cookie，零风控运行；
  - **自动登录**：适配主站 OAuth 2.0 PKCE 认证流程，支持账号专属 Proxy。
- **最新 17 款模型支持**：
  - 覆盖 `claude-fable-5`（Mythos 旗舰）、`claude-sonnet-5`、`claude-opus-4-8`、`gpt-5.6-sol`（长思考）、`kimi-k3`、`deepseek-v4-pro`、`gemini-3.1-pro`、`gpt-image-2` 等。
- **模型别名映射 (`model_aliases`)**：
  - 外部客户端请求通用模型名（如 `gpt-4o`、`claude-3-7-sonnet`）时，系统自动透明路由至目标真实模型。
- **账号池调度与隐蔽机制**：
  - 每账号并发槽位控制 + 排队等待 + 失败自动冷却恢复；
  - 会话智能轮换与标题自动同步，原生直通多轮消息数组。
- **现代化 Glassmorphism Web 控制台**：
  - 包含 7 大功能页签：运行总览、账号集群、代理桥管理、在线 API 调试器 (Playground)、模型广场、全局配置与实时终端日志。

---

## 快速开始

### 1. 安装环境与依赖

```bash
git clone https://github.com/pianhua/camel-chat-proxy.git
cd camel-chat-proxy
pip install -r requirements.txt
playwright install chromium   # 若使用纯手动 Cookie 模式可跳过浏览器内核安装
```

### 2. 配置与启动

复制示例配置文件：
```bash
cp config.example.json config.json
```

编辑 `config.json` 填入账号或 Cookie，然后启动：
```bash
python main.py
```

服务默认运行在 `http://127.0.0.1:5050`（API 端口为 5050，可在 `main.py` 中自定义）。打开浏览器即可访问管理控制台。

---

## 配置说明

支持通过 `config.json` 或环境变量进行配置：

### `config.json` 示例

```json
{
  "api_keys": "sk-camel",
  "admin_password": "admin",
  "web_search": false,
  "session_rotate_turns": 10,
  "message_mode": "native",
  "max_inflight_per_account": 1,
  "account_cooldown_seconds": 120,
  "account_acquire_timeout": 30,
  "model_aliases": {
    "gpt-4o": "claude-opus-4-7",
    "gpt-4o-mini": "claude-haiku-4-5",
    "gpt-5": "gpt-5.5",
    "claude-3-7-sonnet": "claude-sonnet-5",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "deepseek-r1": "deepseek-v4-pro"
  },
  "camel_accounts": [
    {
      "email": "user1@example.com",
      "cookie": "your-camel-session-cookie",
      "proxy": "socks5://127.0.0.1:10801",
      "enabled": true
    },
    {
      "email": "user2@example.com",
      "password": "your-password",
      "proxy": "http://127.0.0.1:7890",
      "enabled": true
    }
  ],
  "mihomo": {
    "enabled": false,
    "binary_path": "",
    "base_port": 10801,
    "api_port": 19090,
    "auto_bind": false
  }
}
```

### 环境变量支持

| 环境变量 | 说明 |
| :--- | :--- |
| `CAMEL_API_KEYS` / `API_KEYS` | API 鉴权密钥（逗号分隔多个） |
| `CAMEL_ADMIN_PASSWORD` | 管理控制台密码 |
| `CAMEL_ACCOUNTS` | 账号列表（支持 JSON 数组或 `email:pwd:cookie` 格式） |
| `CAMEL_WEB_SEARCH` | 全局联网搜索开关（`true` / `false`） |
| `CAMEL_MAX_INFLIGHT_PER_ACCOUNT` | 单账号并发槽位上限（默认 `1`） |
| `CAMEL_ACCOUNT_COOLDOWN_SECONDS` | 账号失败冷却秒数（默认 `120`） |
| `CAMEL_MESSAGE_MODE` | 消息格式模式（`native` 原生数组 / `compact` 折叠） |

---

## 客户端接入示例

### 1. cURL (OpenAI 格式)

```bash
curl http://127.0.0.1:5050/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-camel" \
  -d '{
    "model": "claude-fable-5",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 2. Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:5050/v1",
    api_key="sk-camel"
)

response = client.chat.completions.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": "请写一首关于人工智能的短诗"}],
    stream=True
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

### 3. SillyTavern / NextChat / CherryStudio 配置

- **API 类型**：`OpenAI`（或 `Anthropic`）
- **API Base URL**：`http://127.0.0.1:5050/v1`
- **API Key**：`sk-camel`（或您在配置中设置的密钥）
- **模型名称**：直接选择或输入 `claude-fable-5`、`gpt-5.6-sol`、`gpt-4o` 等

---

## 测试与验证

项目内置完整的自动化测试套件：

```bash
pytest -v
```

包含 Mihomo 代理桥解析、模型别名解析、多模态附件、账号池并发与故障转移等 60 项测试用例。

---

## 免责声明

1. 本项目仅供学习、技术交流与研究用途，请勿用于非法用途或商业牟利。
2. 使用本项目时请严格遵守目标平台的服务条款与法律法规。
