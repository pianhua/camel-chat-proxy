"""CaMeL Chat (chat.camel-hub.com) → OpenAI-compatible API proxy for SillyTavern.

Single-account, fully automatic cookie management. Deploy once, run forever.
Optimized: global HTTP client, session pool, true streaming, model cache.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ 配置区（直接改这里） ============
CAMEL_EMAIL = "piannly@163.com"
CAMEL_PASSWORD = "lyy050710"
CAMEL_BASE = "https://chat.camel-hub.com"
PORT = int(os.environ.get("PORT", "5000"))

# Session pool size (pre-created sessions for lower latency)
SESSION_POOL_SIZE = int(os.environ.get("SESSION_POOL_SIZE", "3"))
# Model cache TTL (seconds)
MODEL_CACHE_TTL = int(os.environ.get("MODEL_CACHE_TTL", "3600"))
# =============================================

app = FastAPI(title="CaMeL Chat OpenAI Proxy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
_cookie: Optional[str] = None
_cookie_time: float = 0
_refresh_task: Optional[asyncio.Task] = None

# Global HTTP client (reused across requests, with connection pooling + HTTP/2)
_http_client: Optional[httpx.AsyncClient] = None

# Session pool
_session_pool: asyncio.Queue = asyncio.Queue()
_session_pool_task: Optional[asyncio.Task] = None

# Model cache
_model_cache: Optional[dict] = None
_model_cache_time: float = 0


def _headers(extra: dict | None = None) -> dict:
    h = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "accept-encoding": "gzip, deflate, br, zstd",
        "origin": CAMEL_BASE,
        "referer": f"{CAMEL_BASE}/chat",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "priority": "u=1, i",
    }
    if extra:
        h.update(extra)
    return h


def _cookies() -> dict:
    if not _cookie:
        raise HTTPException(status_code=503, detail="Cookie not ready, try again in a few seconds")
    return {"camel_session": _cookie}


async def _get_client() -> httpx.AsyncClient:
    """Get or create the global HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=30.0),
            http2=True,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30,
            ),
        )
        logger.info("Created global HTTP client with HTTP/2 and connection pooling")
    return _http_client


async def _do_login() -> bool:
    """Login to CaMeL via Playwright browser automation and extract camel_session cookie."""
    global _cookie, _cookie_time
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                locale="zh-CN",
            )
            page = await context.new_page()

            logger.info("Navigating to login page...")
            await page.goto(f"{CAMEL_BASE}/login", wait_until="domcontentloaded", timeout=15000)

            await page.fill('input[name="email"]', CAMEL_EMAIL)
            await page.fill('input[name="password"]', CAMEL_PASSWORD)

            logger.info("Submitting login form...")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                await page.click('button[type="submit"]')

            cookies = await context.cookies()
            for c in cookies:
                if c["name"] == "camel_session":
                    _cookie = c["value"]
                    _cookie_time = time.time()
                    logger.info("Cookie refreshed successfully via Playwright")
                    await browser.close()
                    return True

            logger.error("camel_session cookie not found after login")
            await browser.close()
            return False

    except Exception as e:
        logger.exception("Playwright login error: %s", e)
        return False


async def _refresh_loop():
    """Background task: refresh cookie every 6 hours."""
    while True:
        await asyncio.sleep(6 * 3600)
        logger.info("Scheduled cookie refresh...")
        await _do_login()


async def _ensure_cookie() -> bool:
    """Ensure we have a valid cookie, refresh if needed."""
    global _cookie
    if _cookie and (time.time() - _cookie_time) < 6 * 3600:
        return True
    logger.info("Cookie missing or expired, refreshing...")
    return await _do_login()


async def _create_session(title: str, model: str) -> str:
    """Create a new CaMeL chat session and return its ID."""
    client = await _get_client()
    r = await client.post(
        f"{CAMEL_BASE}/api/chat/sessions",
        headers=_headers({"content-type": "application/json"}),
        cookies=_cookies(),
        json={"title": title[:64], "model": model},
    )
    if r.status_code != 201:
        logger.error("Session creation failed: %s %s", r.status_code, r.text[:500])
        raise HTTPException(status_code=502, detail="CaMeL session creation failed")
    return r.json()["id"]


async def _session_pool_worker():
    """Background task: keep the session pool filled."""
    while True:
        try:
            if _session_pool.qsize() < SESSION_POOL_SIZE:
                await _ensure_cookie()
                session_id = await _create_session("pool", "claude-opus-4-7")
                await _session_pool.put(session_id)
                logger.debug("Added session to pool: %s (pool size: %d)", session_id, _session_pool.qsize())
            else:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error("Session pool worker error: %s", e)
            await asyncio.sleep(5)


async def _get_session(title: str, model: str) -> str:
    """Get a session from the pool, or create a new one if pool is empty."""
    try:
        session_id = _session_pool.get_nowait()
        logger.debug("Reusing pooled session: %s", session_id)
        return session_id
    except asyncio.QueueEmpty:
        logger.debug("Pool empty, creating new session")
        return await _create_session(title, model)


def _to_camel_model(openai_model: str) -> str:
    known = {
        "claude-opus-4-7": "claude-opus-4-7",
        "claude-opus-4-8": "claude-opus-4-8",
        "claude-opus-4-6": "claude-opus-4-6",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-haiku-4-5": "claude-haiku-4-5",
        "claude-fable-5": "claude-fable-5",
        "claude-sonnet-5": "claude-sonnet-5",
        "gpt-5.5-pro": "gpt-5.5-pro",
        "gpt-5.5": "gpt-5.5",
        "gpt-5.4": "gpt-5.4",
        "gpt-4o": "gpt-4o",
        "gpt-5.6-sol": "gpt-5.6-sol",
        "gemini-3.1-pro": "gemini-3.1-pro",
        "gemini-3.5-flash": "gemini-3.5-flash",
        "deepseek-v3.2": "deepseek-v3.2",
        "deepseek-r1": "deepseek-r1",
        "deepseek-v4-pro": "deepseek-v4-pro",
        "kimi-k3": "kimi-k3",
    }
    return known.get(openai_model, "claude-opus-4-7")


@app.on_event("startup")
async def startup():
    """Auto-login on startup and start background tasks."""
    global _refresh_task, _session_pool_task
    logger.info("Starting CaMeL proxy...")
    success = await _do_login()
    if success:
        logger.info("Initial login successful")
    else:
        logger.warning("Initial login failed, will retry on first request")

    _refresh_task = asyncio.create_task(_refresh_loop())
    logger.info("Background cookie refresh task started (every 6h)")

    # Start session pool worker
    _session_pool_task = asyncio.create_task(_session_pool_worker())
    logger.info("Session pool worker started (target size: %d)", SESSION_POOL_SIZE)

    # Pre-warm global HTTP client
    await _get_client()


@app.on_event("shutdown")
async def shutdown():
    if _refresh_task:
        _refresh_task.cancel()
    if _session_pool_task:
        _session_pool_task.cancel()
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


@app.get("/v1/models")
async def list_models():
    global _model_cache, _model_cache_time
    now = time.time()
    if _model_cache and (now - _model_cache_time) < MODEL_CACHE_TTL:
        return _model_cache

    await _ensure_cookie()
    client = await _get_client()
    r = await client.get(
        f"{CAMEL_BASE}/api/chat/models",
        headers=_headers(),
        cookies=_cookies(),
    )
    if r.status_code != 200:
        logger.error("Models failed: %s", r.status_code)
        raise HTTPException(status_code=502, detail="CaMeL models endpoint failed")
    models = r.json()
    result = {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": 0, "owned_by": m.get("providerId", "CaMeL")}
            for m in models if m.get("type") == "chat"
        ],
    }
    _model_cache = result
    _model_cache_time = now
    return result


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    await _ensure_cookie()

    body = await request.json()
    model = _to_camel_model(body.get("model", "claude-opus-4-7"))
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not convo:
        raise HTTPException(status_code=400, detail="user or assistant message required")

    last_user_text = ""
    for m in convo:
        if m.get("role") == "user":
            last_user_text = m.get("content", "")

    if system_parts:
        for m in convo:
            if m.get("role") == "user":
                m["content"] = "[System instructions]\n" + "\n".join(system_parts) + "\n\n" + m.get("content", "")
                break

    camel_messages = [{"role": m["role"], "content": m.get("content", "")} for m in convo]
    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())

    session_id = await _get_session(last_user_text, model)

    payload = {
        "messages": camel_messages,
        "model": model,
        "providerId": "CaMeL",
        "sessionId": session_id,
        "userMessageId": user_msg_id,
        "assistantMessageId": assistant_msg_id,
        "userText": last_user_text,
        "hasAttachments": False,
        "hasCustomSystemPrompt": False,
        "webSearch": False,
    }

    if stream:
        return StreamingResponse(_stream(payload, assistant_msg_id, model), media_type="text/event-stream")

    client = await _get_client()
    r = await client.post(
        f"{CAMEL_BASE}/api/chat/completion",
        headers=_headers({"content-type": "application/json"}),
        cookies=_cookies(),
        json=payload,
    )
    if r.status_code != 200:
        logger.error("Completion failed: %s", r.status_code)
        raise HTTPException(status_code=502, detail="CaMeL completion failed")
    text = r.text

    return JSONResponse({
        "id": f"chatcmpl-{assistant_msg_id}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


async def _stream(payload: dict, msg_id: str, model: str) -> AsyncGenerator[str, None]:
    """Yield OpenAI-style SSE chunks from CaMeL's chunked plain-text response."""
    client = await _get_client()
    buffer = ""
    async with client.stream(
        "POST",
        f"{CAMEL_BASE}/api/chat/completion",
        headers=_headers({"content-type": "application/json"}),
        cookies=_cookies(),
        json=payload,
    ) as r:
        if r.status_code != 200:
            logger.error("Stream completion failed: %s", r.status_code)
            raise HTTPException(status_code=502, detail="CaMeL completion failed")

        async for chunk in r.aiter_text():
            if not chunk:
                continue
            buffer += chunk
            # Yield complete words/phrases as they arrive
            while len(buffer) >= 4:
                # Try to break at a natural boundary (space, punctuation, or every 4 chars)
                split_at = 4
                for i in range(min(20, len(buffer)), 3, -1):
                    if buffer[i-1] in " \n\t，。！？；：,.!?;:":
                        split_at = i
                        break
                piece = buffer[:split_at]
                buffer = buffer[split_at:]
                data = {
                    "id": f"chatcmpl-{msg_id}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # Yield any remaining buffer
    if buffer:
        data = {
            "id": f"chatcmpl-{msg_id}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": buffer}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


@app.get("/health")
async def health():
    age = time.time() - _cookie_time if _cookie_time else None
    return {
        "status": "ok",
        "cookie_ready": bool(_cookie),
        "cookie_age_hours": round(age / 3600, 1) if age else None,
        "session_pool_size": _session_pool.qsize(),
        "model_cached": _model_cache is not None,
    }


@app.post("/admin/refresh")
async def admin_refresh():
    success = await _do_login()
    return {"status": "ok" if success else "failed", "cookie_ready": bool(_cookie)}


@app.post("/admin/session-pool/refill")
async def admin_refill_pool():
    """Manually refill the session pool."""
    count = 0
    while _session_pool.qsize() < SESSION_POOL_SIZE:
        try:
            session_id = await _create_session("pool", "claude-opus-4-7")
            await _session_pool.put(session_id)
            count += 1
        except Exception as e:
            logger.error("Failed to refill pool: %s", e)
            break
    return {"status": "ok", "added": count, "pool_size": _session_pool.qsize()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
