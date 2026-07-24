# CaMeL Chat OpenAI Proxy

将免费网页端 [CaMeL Chat](https://chat.camel-hub.com) 逆向为 OpenAI 兼容 API，供 SillyTavern 使用。

## 特点

- **单文件部署**：所有逻辑在 `proxy.py` 一个文件里
- **硬编码账号**：改配置区即可，无需外部配置文件
- **全自动 Cookie**：Playwright 自动登录，每 6 小时自动刷新
- **一次部署，永久运行**：无需维护，无需手动复制 Cookie
- **跨平台**：Windows / Linux / macOS 均可运行

---

## 系统要求

| 平台 | 要求 |
|------|------|
| Windows | Windows 10/11，Python 3.9+ |
| Linux | Ubuntu 20.04+ / Debian 11+ / CentOS 8+，Python 3.9+ |
| macOS | macOS 11+，Python 3.9+ |

**Linux 额外依赖**（Playwright 需要）：

```bash
# Ubuntu/Debian
sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2

# CentOS/RHEL
sudo yum install -y nss nspr atk at-spi2-atk cups-libs drm libxkbcommon libXcomposite libXdamage libXfixes libXrandr mesa-libgbm alsa-lib
```

---

## 部署方式

### 方式一：本地使用（最简单）

适合：只在自己电脑上用 SillyTavern。

```bash
git clone https://github.com/pianhua/camel-chat-proxy.git
cd camel-chat-proxy
pip install -r requirements.txt
playwright install chromium
python proxy.py
```

SillyTavern 填：`http://localhost:5000/v1`

---

### 方式二：局域网共享

适合：手机/平板/其他电脑连同一 WiFi 使用。

```bash
# 启动时绑定到局域网 IP（假设本机 IP 是 192.168.1.100）
python proxy.py
```

默认已绑定 `0.0.0.0`，局域网内其他设备直接访问：

```
http://192.168.1.100:5000/v1
```

**Windows 查看本机 IP**：`ipconfig` 找 IPv4 地址
**Linux 查看本机 IP**：`ip addr` 或 `hostname -I`

---

### 方式三：无服务器公网访问（内网穿透）

适合：没有云服务器，但想在外面（4G/其他网络）使用。

#### 方案 A：Cloudflare Tunnel（推荐，免费稳定）

```bash
# 1. 下载 cloudflared
# Windows: https://github.com/cloudflare/cloudflared/releases
# Linux: 
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# 2. 登录 Cloudflare
cloudflared tunnel login

# 3. 创建隧道
cloudflared tunnel create camel-proxy

# 4. 启动隧道（会生成一个 https://xxx.trycloudflare.com 域名）
cloudflared tunnel --url http://localhost:5000
```

SillyTavern 填：`https://xxx.trycloudflare.com/v1`

#### 方案 B：ngrok（简单，有免费额度）

```bash
# 1. 下载 ngrok: https://ngrok.com/download
# 2. 注册获取 authtoken
ngrok config add-authtoken 你的token

# 3. 启动穿透
ngrok http 5000
```

SillyTavern 填：`https://xxx.ngrok.io/v1`

#### 方案 C：frp（自有服务器辅助）

如果有一台低价 VPS 做跳板：

```ini
# frpc.ini（本地运行）
[common]
server_addr = 你的VPS_IP
server_port = 7000

[camel-proxy]
type = tcp
local_ip = 127.0.0.1
local_port = 5000
remote_port = 5000
```

---

### 方式四：云服务器部署（生产环境）

适合：长期稳定运行，多设备使用。

#### 火山引擎（字节跳动）专用步骤

**1. 安全组放行端口**

火山引擎控制台 → 云服务器 ECS → 安全组 → 配置规则 → 添加安全组规则：

| 方向 | 协议 | 端口范围 | 源地址 | 策略 |
|------|------|----------|--------|------|
| 入方向 | TCP | 5000 | 0.0.0.0/0 | 允许 |

> 如果只允许特定 IP 访问，把 `0.0.0.0/0` 改成你的 IP 段。

**2. 服务器上部署**

```bash
# SSH 登录火山引擎服务器（用公网 IP）
ssh root@你的公网IP

# 克隆项目
git clone https://github.com/pianhua/camel-chat-proxy.git
cd camel-chat-proxy

# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动（默认已绑定 0.0.0.0，公网可访问）
python proxy.py
```

**3. 验证**

```bash
# 在服务器上测试
curl http://localhost:5000/health

# 在你电脑上测试（换成公网 IP）
curl http://你的公网IP:5000/health
```

**4. SillyTavern 配置**

| 配置项 | 值 |
|--------|-----|
| API URL | `http://你的公网IP:5000/v1` |

---

#### 通用云服务器步骤（阿里云/腾讯云/AWS 等）

```bash
# 1. 服务器上克隆
git clone https://github.com/pianhua/camel-chat-proxy.git
cd camel-chat-proxy

# 2. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 3. 改配置（可选，用环境变量覆盖端口）
export PORT=5000

# 4. 后台运行（推荐 tmux/screen 或 systemd）
python proxy.py
```

#### Linux systemd 守护

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
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable camel-proxy
sudo systemctl start camel-proxy
```

#### Docker 部署

```dockerfile
FROM python:3.11-slim

# 安装 Playwright 依赖
RUN apt-get update && apt-get install -y \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium

COPY . .
EXPOSE 5000
CMD ["python", "proxy.py"]
```

```bash
docker build -t camel-proxy .
docker run -d -p 5000:5000 --name camel camel-proxy
```

---

## 配置说明

编辑 `proxy.py` 顶部配置区：

```python
# ============ 配置区 ============
CAMEL_EMAIL = "piannly@163.com"      # 你的 CaMeL 邮箱
CAMEL_PASSWORD = "lyy050710"          # 你的 CaMeL 密码
CAMEL_BASE = "https://chat.camel-hub.com"
PORT = int(os.environ.get("PORT", "5000"))  # 监听端口
# =================================
```

**多账号轮询**（可选）：

```python
CAMEL_ACCOUNTS = [
    ("piannly@163.com", "lyy050710"),
    ("piannly@163.cm", "lyy050710"),
    ("backup@gmail.com", "password123"),
]
```

---

## SillyTavern 配置

| 场景 | API URL |
|------|---------|
| 本机使用 | `http://localhost:5000/v1` |
| 局域网 | `http://192.168.x.x:5000/v1` |
| Cloudflare Tunnel | `https://xxx.trycloudflare.com/v1` |
| 云服务器 | `http://服务器IP:5000/v1` |

| 配置项 | 值 |
|--------|-----|
| API 来源 | Custom (OpenAI-compatible) |
| API Key | 任意值，如 `sk-camel` |
| 模型 | `claude-opus-4-7` |

---

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 列出可用模型 |
| `/v1/chat/completions` | POST | 聊天补全（支持 `stream=true`） |
| `/health` | GET | 健康检查 + Cookie 状态 |
| `/admin/refresh` | POST | 手动刷新 Cookie |

---

## 可用模型

- `claude-opus-4-7` / `claude-opus-4-8` / `claude-opus-4-6`
- `claude-sonnet-4-6` / `claude-sonnet-5`
- `claude-haiku-4-5` / `claude-fable-5`
- `gpt-5.5-pro` / `gpt-5.5` / `gpt-5.4` / `gpt-4o` / `gpt-5.6-sol`
- `gemini-3.1-pro` / `gemini-3.5-flash`
- `deepseek-v3.2` / `deepseek-r1` / `deepseek-v4-pro`
- `kimi-k3`

---

## 注意事项

1. **密码安全**：`proxy.py` 里明文存密码，不要分享给他人
2. **CaMeL 官方可见**：所有发送内容 CaMeL 服务器都能看到
3. **免费模型慢**：响应 1-2 分钟，SillyTavern 请设长超时
4. **Cookie 自动刷新**：每 6 小时自动登录刷新，无需干预
5. **首次启动**：Playwright 会下载 Chromium（约 100MB），稍等即可

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `proxy.py` | 主服务（单文件包含全部逻辑） |
| `requirements.txt` | Python 依赖 |
| `start.bat` | Windows 一键启动 |
| `README.md` | 本文档 |
