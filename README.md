# CaMeL Chat OpenAI Proxy

将免费网页端 [CaMeL Chat](https://chat.camel-hub.com) 逆向为 OpenAI 兼容 API，供 SillyTavern 使用。

## 特点

- **单文件部署**：所有逻辑在 `proxy.py` 一个文件里
- **硬编码账号**：改配置区即可，无需外部配置文件
- **全自动 Cookie**：启动自动登录，每 6 小时自动刷新
- **一次部署，永久运行**：无需维护，无需手动复制 Cookie

## 快速部署

### 1. 克隆

```bash
git clone https://github.com/你的用户名/camel-chat-proxy.git
cd camel-chat-proxy
```

### 2. 改配置

编辑 `proxy.py` 顶部配置区：

```python
CAMEL_EMAIL = "your@email.com"      # 你的 CaMeL 邮箱
CAMEL_PASSWORD = "yourpassword"      # 你的 CaMeL 密码
PORT = 5000                          # 监听端口
```

### 3. 安装运行

```bash
pip install -r requirements.txt
python proxy.py
```

完成。SillyTavern 填 `http://你的IP:5000/v1` 即可。

## 服务器守护（Linux）

```bash
# /etc/systemd/system/camel-proxy.service
[Unit]
Description=CaMeL Chat Proxy
After=network.target

[Service]
Type=simple
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

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 模型列表 |
| `/v1/chat/completions` | POST | 聊天（支持 stream） |
| `/health` | GET | 状态 + Cookie 年龄 |
| `/admin/refresh` | POST | 手动刷新 Cookie |

## 模型

`claude-opus-4-7` `claude-opus-4-8` `claude-sonnet-4-6` `claude-sonnet-5` `claude-haiku-4-5` `claude-fable-5` `gpt-5.5` `gpt-5.5-pro` `gpt-4o` `gemini-3.1-pro` `deepseek-v3.2` `deepseek-r1` `kimi-k3` 等
