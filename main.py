"""CaMeL Chat → OpenAI + Anthropic 兼容 API 代理 — 主入口。"""

import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.openai_routes import router as openai_router, discover_models
from app.api.anthropic_routes import router as anthropic_router
from app.api.admin_routes import router as admin_router
from app.api.deps import get_camel_client
from app.core.config import config_manager
from app.core.http import get_http_client, close_http_client

app = FastAPI(title="CaMeL Chat Proxy", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(openai_router)
app.include_router(anthropic_router)
app.include_router(admin_router)


@app.on_event("startup")
async def startup():
    print("=" * 46)
    print("  CaMeL Chat Proxy v4.0")
    print("=" * 46)
    http = await get_http_client()
    accounts = config_manager.config.camel_accounts
    if accounts:
        print(f"\n[Startup] Logging in {len(accounts)} account(s)...")
        for acc in accounts:
            try:
                ok = await get_camel_client(acc).refresh_cookie()
                acc.is_valid = ok
                print(f"  {'OK' if ok else 'FAIL'} {acc.email}")
            except Exception as e:
                print(f"  FAIL {acc.email}: {e}")
        config_manager.save()
    else:
        print("\n[Startup] No accounts configured. Add one via web UI at /")
    try:
        await discover_models(http)
        print("[Startup] Models pre-loaded")
    except Exception as e:
        print(f"[Startup] Model pre-load failed (non-fatal): {e}")


@app.on_event("shutdown")
async def shutdown():
    await close_http_client()


def main():
    port = int(os.environ.get("PORT", "5050"))
    print(f"\nAPI:   http://0.0.0.0:{port}/v1")
    print(f"Admin: http://0.0.0.0:{port}/")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
