FROM python:3.11-slim

WORKDIR /app

# 安装基础系统依赖（curl 用于健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=5050
EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5050/health || exit 1

CMD ["python", "main.py"]
