"""CaMeL Chat → OpenAI + Anthropic 兼容 API 代理 — 主入口。"""

import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from app.routes import router, _get_http_client
from app.anthropic_routes import anthropic_router
from app.config import config_manager

app = FastAPI(
    title="CaMeL Chat Proxy",
    description="CaMeL Chat → OpenAI + Anthropic 兼容 API（Chat / Messages），支持多账号自动登录、Web管理面板。",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(anthropic_router)


@app.on_event("startup")
async def startup():
    """启动时自动登录并预加载模型列表。"""
    print("╔══════════════════════════════════════════════╗")
    print("║          CaMeL Chat Proxy v3.0              ║")
    print("║   CaMeL Chat → OpenAI + Anthropic API       ║")
    print("╚══════════════════════════════════════════════╝")

    # 预热 HTTP 客户端
    http_client = await _get_http_client()

    # 自动登录所有账号（使用统一客户端注册表）
    from app.routes import _get_camel_client
    accounts = config_manager.config.camel_accounts
    if accounts:
        print(f"\n[Startup] Logging in {len(accounts)} account(s)...")
        for acc in accounts:
            client = _get_camel_client(acc)
            try:
                ok = await client.refresh_cookie(http_client)
                acc.is_valid = ok
                print(f"  {'✓' if ok else '✗'} {acc.email}")
            except Exception as e:
                print(f"  ✗ {acc.email}: {e}")
        config_manager.save()
    else:
        print("\n[Startup] ⚠ No accounts configured. Add one via web UI at /")

    # 预加载模型列表
    try:
        from app.routes import _discover_models
        await _discover_models(http_client)
        print("[Startup] ✓ Models pre-loaded")
    except Exception as e:
        print(f"[Startup] ⚠ Model pre-load failed (non-fatal): {e}")


@app.on_event("shutdown")
async def shutdown():
    http_client = await _get_http_client()
    if http_client and not http_client.is_closed:
        await http_client.aclose()


def main():
    port = int(os.environ.get("PORT", "5050"))
    print(f"\n{'─' * 50}")
    print(f"🚀 Server starting...")
    print(f"📍 API:    http://0.0.0.0:{port}/v1")
    print(f"🌐 Admin:  http://0.0.0.0:{port}/")
    print(f"📖 Docs:   http://0.0.0.0:{port}/docs")
    print(f"🔑 Keys:   {config_manager.config.api_keys}")
    print(f"👤 Accounts: {len(config_manager.config.camel_accounts)}")
    print(f"{'─' * 50}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
