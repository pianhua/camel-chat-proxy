"""CaMeL Chat → OpenAI + Anthropic 兼容 API 代理 — 主入口。"""

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.openai_routes import router as openai_router, discover_models
from app.api.anthropic_routes import router as anthropic_router
from app.api.admin_routes import router as admin_router
from app.api.deps import get_camel_client
from app.core.config import config_manager
from app.core.http import get_http_client, close_http_client
from app.core.logger import get_logger

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 46)
    logger.info("  CaMeL Chat Proxy v4.0")
    logger.info("=" * 46)
    http = await get_http_client()
    accounts = config_manager.config.camel_accounts
    if accounts:
        logger.info("[Startup] Logging in %d account(s)...", len(accounts))
        for acc in accounts:
            try:
                ok = await get_camel_client(acc).refresh_cookie()
                acc.is_valid = ok
                logger.info("  %s %s", "OK" if ok else "FAIL", acc.email)
            except Exception:
                logger.error("[Startup] Login failed for %s", acc.email, exc_info=True)
        config_manager.save()
    else:
        logger.info("[Startup] No accounts configured. Add one via web UI at /")
    try:
        await discover_models(http)
        logger.info("[Startup] Models pre-loaded")
    except Exception as e:
        logger.warning("[Startup] Model pre-load failed (non-fatal): %s", e)
    yield
    await close_http_client()


app = FastAPI(title="CaMeL Chat Proxy", version="4.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(openai_router)
app.include_router(anthropic_router)
app.include_router(admin_router)


def main():
    port = int(os.environ.get("PORT", "5050"))
    print(f"\nAPI:   http://0.0.0.0:{port}/v1")
    print(f"Admin: http://0.0.0.0:{port}/")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
