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
for i in 1 2 3; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/health)
    if [ "$HTTP_CODE" = "200" ]; then
        echo "✅ Health check passed (HTTP 200)"
        exit 0
    fi
    sleep 2
done
echo "⚠ Health check failed — check proxy.log"
