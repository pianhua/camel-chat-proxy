# CaMeL Chat OpenAI Proxy

将免费网页端 [CaMeL Chat](https://chat.camel-hub.com) 逆向为 OpenAI 兼容 API，供 SillyTavern 等工具使用。

## 功能

- OpenAI 兼容端点：`/v1/models`、`/v1/chat/completions`
- 自动登录刷新 Cookie，无需手动复制
- 多账号轮询，分摊请求负载
- 支持 System Prompt 透传、Assistant Prefill
- SSE 流式输出模拟

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/你的用户名/camel-chat-proxy.git
cd camel-chat-proxy
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置账号

复制示例配置并编辑：

```bash
cp accounts.example.json accounts.json
```

编辑 `accounts.json`，填入 CaMeL 账号：

```json
{
  "accounts": [
    {
      "name": "主账号",
      "email": "your@email.com",
      "password": "yourpassword",
      "camel_session": "",
      "enabled": true,
      "last_refresh": null,
      "request_count": 0
    }
  ]
}
```

> 首次启动自动登录填充 `camel_session`。也可手动从浏览器 DevTools → Application → Cookies 复制。

### 4. 启动

```bash
python proxy.py
```

默认监听 `http://0.0.0.0:5000`

### 5. SillyTavern 配置

| 配置项 | 值 |
|--------|-----|
| API 来源 | Custom (OpenAI-compatible) |
| API URL | `http://你的服务器IP:5000/v1` |
| API Key | 任意值 |
| 模型 | `claude-opus-4-7` |

## 服务器部署

### 方式一：直接运行

```bash
git clone https://github.com/你的用户名/camel-chat-proxy.git
cd camel-chat-proxy
pip install -r requirements.txt
cp accounts.example.json accounts.json
# 编辑 accounts.json 填入账号
python proxy.py
```

### 方式二：后台守护（Linux systemd）

```bash
# /etc/systemd/system/camel-proxy.service
[Unit]
Description=CaMeL Chat Proxy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/camel-chat-proxy
ExecStart=/usr/bin/python3 proxy.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable camel-proxy
systemctl start camel-proxy
```

### 方式三：Docker（可选）

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "proxy.py"]
```

```bash
docker build -t camel-proxy .
docker run -d -p 5000:5000 -v $(pwd)/accounts.json:/app/accounts.json camel-proxy
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 列出可用模型 |
| `/v1/chat/completions` | POST | 聊天补全（支持 `stream=true`） |
| `/health` | GET | 健康检查 + 账号状态 |
| `/admin/refresh` | POST | 手动刷新所有账号 Cookie |

## 多账号轮询

- 每次请求轮询选择有效账号
- Cookie 过期前自动刷新（约 8 小时有效期）
- 连续失败 3 次自动禁用账号
- 通过 `/health` 查看各账号状态

## 可用模型

- `claude-opus-4-7` / `claude-opus-4-8` / `claude-opus-4-6`
- `claude-sonnet-4-6` / `claude-sonnet-5`
- `claude-haiku-4-5` / `claude-fable-5`
- `gpt-5.5-pro` / `gpt-5.5` / `gpt-5.4` / `gpt-4o` / `gpt-5.6-sol`
- `gemini-3.1-pro` / `gemini-3.5-flash`
- `deepseek-v3.2` / `deepseek-r1` / `deepseek-v4-pro`
- `kimi-k3`

## 安全提示

- `accounts.json` 包含明文密码，**不要提交到 Git**
- 服务器部署建议加防火墙或 Nginx 反向代理 + Basic Auth
- CaMeL 官方能看到所有发送内容，勿用于违法用途

## 文件说明

| 文件 | 说明 |
|------|------|
| `proxy.py` | FastAPI 主服务 |
| `cookie_manager.py` | 自动登录 + 多账号管理 |
| `accounts.example.json` | 账号配置示例 |
| `start.bat` | Windows 一键启动 |
