# CaMeL Chat OpenAI 兼容代理

将免费网页端 `https://chat.camel-hub.com` 逆向为 OpenAI 兼容 API，供 SillyTavern 等工具使用。

## 新功能：自动 Cookie + 多账号轮询

- **自动登录**：启动时自动用账号密码刷新 `camel_session`，无需手动复制
- **多账号轮询**：多个账号轮流处理请求，分摊负载避免限制
- **失败转移**：某账号连续失败 3 次自动禁用，切换其他账号

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置账号
编辑 `accounts.json`，填入你的 CaMeL 账号：

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
    },
    {
      "name": "备用号1",
      "email": "backup1@email.com",
      "password": "password1",
      "camel_session": "",
      "enabled": true,
      "last_refresh": null,
      "request_count": 0
    }
  ]
}
```

> 首次启动时会自动登录并填充 `camel_session`。也可以手动粘贴浏览器里的 cookie。

### 3. 启动代理
```bash
python proxy.py
# 或双击 start.bat
```

### 4. SillyTavern 配置
- **API 来源**: Custom (OpenAI-compatible)
- **API URL**: `http://localhost:5050/v1`
- **API Key**: 任意值
- **模型**: `claude-opus-4-7`

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CAMEL_BASE` | CaMeL Chat 基础 URL | `https://chat.camel-hub.com` |
| `PORT` | 代理监听端口 | `5000` |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/models` | GET | 列出可用模型 |
| `/v1/chat/completions` | POST | 聊天补全（支持 `stream=true`） |
| `/health` | GET | 健康检查 + 账号状态 |
| `/admin/refresh` | POST | 手动刷新所有账号 cookie |

## 多账号轮询机制

- 每次请求**轮询**选择一个有效账号
- 启动时自动刷新所有账号 cookie
- Cookie 有效期约 8 小时，过期前 2 小时自动刷新
- 某账号连续失败 3 次自动禁用
- 可通过 `/health` 查看各账号状态

## 可用模型

- `claude-opus-4-7` / `claude-opus-4-8` / `claude-opus-4-6`
- `claude-sonnet-4-6` / `claude-sonnet-5`
- `claude-haiku-4-5` / `claude-fable-5`
- `gpt-5.5-pro` / `gpt-5.5` / `gpt-5.4` / `gpt-4o` / `gpt-5.6-sol`
- `gemini-3.1-pro` / `gemini-3.5-flash`
- `deepseek-v3.2` / `deepseek-r1` / `deepseek-v4-pro`
- `kimi-k3`

## 预设兼容性

已验证兼容 SillyTavern 破限预设（如 `初心破限.json`）：
- System prompts 合并到首条 user 消息
- 多轮规则确认对话完整透传
- Assistant prefill 作为历史上下文传入

## 注意事项

- 密码明文存储在 `accounts.json`，请确保文件安全
- 免费模型响应较慢（1-2 分钟），SillyTavern 请设置长超时
- 系统提示词会被合并到第一条用户消息
- 流式模式等待完整响应后分块发送

## 文件说明

| 文件 | 说明 |
|------|------|
| `proxy.py` | FastAPI 主服务 |
| `cookie_manager.py` | 自动登录 + 多账号管理 |
| `accounts.json` | 账号配置 |
| `start.bat` | Windows 一键启动 |
