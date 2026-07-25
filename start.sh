#!/bin/bash
# CaMeL Chat Proxy — 后台启动脚本
cd "$(dirname "$0")"
export PORT=${PORT:-5050}

# 杀旧进程
lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 后台启动
nohup python main.py > proxy.log 2>&1 &
PID=$!
echo "🚀 CaMeL Proxy started (PID: $PID, port: $PORT)"
sleep 3

# 健康检查
curl -s http://localhost:$PORT/health | python -m json.tool 2>/dev/null || echo "⚠ Health check failed — check proxy.log"
