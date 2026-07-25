#!/bin/bash
# Start CaMeL proxy in background
cd "$(dirname "$0")"
export PORT=${PORT:-5050}

# Kill existing process on port
lsof -ti:$PORT | xargs kill -9 2>/dev/null || true

# Start in background
nohup python proxy.py > proxy.log 2>&1 &
echo "Proxy started on port $PORT (PID: $!)"
sleep 2
curl -s http://localhost:$PORT/health || echo "Health check failed - check proxy.log"
