"""CaMeL Chat (chat.camel-hub.com) → OpenAI-compatible API proxy for SillyTavern.

Single-account, fully automatic cookie management. Deploy once, run forever.
"""

import asyncio
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ 配置区（直接改这里） ============
CAMEL_EMAIL = "piannly@163.com"
CAMEL_PASSWORD = "lyy050710"
CAMEL_BASE = "https://chat.camel-hub.com"
PORT = int(os.environ.get("PORT", "5000"))
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


async def _do_login() -> bool:
    """Login to CaMeL and extract camel_session cookie."""
    global _cookie, _cookie_time
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            # Step 1: GET login page
            r = await client.get(f"{CAMEL_BASE}/login")
            if r.status_code != 200:
                logger.error("Login page fetch failed: %s", r.status_code)
                return False

            # Step 2: Extract action ID
            match = re.search(r'ACTION_ID_([a-f0-9]+)', r.text)
            if not match:
                logger.error("ACTION_ID not found in login page")
                return False
            action_id = match.group(1)
            logger.debug("Found ACTION_ID: %s", action_id)

            # Step 3: Build multipart form (exact Next.js Server Action format)
            boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
            lines = [
                f"--{boundary}",
                f'Content-Disposition: form-data; name="_1_$ACTION_ID_{action_id}"',
                "",
                "",
                f"--{boundary}",
                'Content-Disposition: form-data; name="_1_redirectTo"',
                "",
                "",
                f"--{boundary}",
                'Content-Disposition: form-data; name="_1_email"',
                "",
                CAMEL_EMAIL,
                f"--{boundary}",
                'Content-Disposition: form-data; name="_1_password"',
                "",
                CAMEL_PASSWORD,
                f"--{boundary}",
                'Content-Disposition: form-data; name="0"',
                "",
                '["$K1"]',
                f"--{boundary}--",
                "",
            ]
            body = "\r\n".join(lines).encode("utf-8")
            headers = {
                "content-type": f"multipart/form-data; boundary={boundary}",
                "accept": "text/x-component",
                "origin": CAMEL_BASE,
                "referer": f"{CAMEL_BASE}/login",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            }

            login_r = await client.post(f"{CAMEL_BASE}/login", content=body, headers=headers)

            # Step 4: Extract session cookie
            set_cookie = login_r.headers.get("set-cookie", "")
            session_match = re.search(r'camel_session=([^;]+)', set_cookie)
            if session_match:
                _cookie = session_match.group(1)
                _cookie_time = time.time()
                logger.info("Cookie refreshed successfully")
                return True

            # Check redirect for cookie
            if login_r.status_code in (301, 302, 303, 307, 308):
                redirect_url = login_r.headers.get("location", "")
                if redirect_url:
                    full_url = redirect_url if redirect_url.startswith("http") else f"{CAMEL_BASE}{redirect_url}"
                    redirect_r = await client.get(full_url, headers={"referer": f"{CAMEL_BASE}/login"})
                    set_cookie = redirect_r.headers.get("set-cookie", "")
                    session_match = re.search(r'camel_session=([^;]+)', set_cookie)
                    if session_match:
                        _cookie = session_match.group(1)
                        _cookie_time = time.time()
                        logger.info("Cookie refreshed via redirect")
                        return True

            logger.error("No camel_session in response. Status: %s", login_r.status_code)
            return False

    except Exception as e:
        logger.exception("Login error: %s", e)
        return False


async def _refresh_loop():
    """Background task: refresh cookie every 6 hours."""
    while True:
        await asyncio.sleep(6 * 3600)  # 6 hours
        logger.info("Scheduled cookie refresh...")
        await _do_login()


async def _ensure_cookie() -> bool:
    """Ensure we have a valid cookie, refresh if needed."""
    global _cookie
    if _cookie and (time.time() - _cookie_time) < 6 * 3600:
        return True
    logger.info("Cookie missing or expired, refreshing...")
    return await _do_login()


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
    """Auto-login on startup and start background refresh task."""
    global _refresh_task
    logger.info("Starting CaMeL proxy...")
    success = await _do_login()
    if success:
        logger.info("Initial login successful")
    else:
        logger.warning("Initial login failed, will retry on first request")
    _refresh_task = asyncio.create_task(_refresh_loop())
    logger.info("Background cookie refresh task started (every 6h)")


@app.on_event("shutdown")
async def shutdown():
    if _refresh_task:
        _refresh_task.cancel()


@app.get("/v1/models")
async def list_models():
    await _ensure_cookie()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{CAMEL_BASE}/api/chat/models",
            headers=_headers(),
            cookies=_cookies(),
        )
        if r.status_code != 200:
            logger.error("Models failed: %s", r.status_code)
            raise HTTPException(status_code=502, detail="CaMeL models endpoint failed")
        models = r.json()
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": 0, "owned_by": m.get("providerId", "CaMeL")}
            for m in models if m.get("type") == "chat"
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    await _ensure_cookie()

    body = await request.json()
    model = _to_camel_model(body.get("model", "claude-opus-4-7"))
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    # Build message list: merge system into first user, keep assistant prefill
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

    # Create session
    async with httpx.AsyncClient(timeout=30.0) as client:
        session_r = await client.post(
            f"{CAMEL_BASE}/api/chat/sessions",
            headers=_headers({"content-type": "application/json"}),
            cookies=_cookies(),
            json={"title": last_user_text[:64], "model": model},
        )
        if session_r.status_code != 201:
            logger.error("Session failed: %s %s", session_r.status_code, session_r.text[:500])
            raise HTTPException(status_code=502, detail="CaMeL session creation failed")
        session_id = session_r.json()["id"]

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

    async with httpx.AsyncClient(timeout=600.0) as client:
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
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{CAMEL_BASE}/api/chat/completion",
            headers=_headers({"content-type": "application/json"}),
            cookies=_cookies(),
            json=payload,
        )
        if r.status_code != 200:
            logger.error("Stream completion failed: %s", r.status_code)
            raise HTTPException(status_code=502, detail="CaMeL completion failed")
        text = r.text

    for chunk in _split_text(text, 4):
        data = {
            "id": f"chatcmpl-{msg_id}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _split_text(text: str, size: int = 4) -> list[str]:
    return [text[i:i+size] for i in range(0, len(text), size)]


@app.get("/health")
async def health():
    age = time.time() - _cookie_time if _cookie_time else None
    return {
        "status": "ok",
        "cookie_ready": bool(_cookie),
        "cookie_age_hours": round(age / 3600, 1) if age else None,
    }


@app.post("/admin/refresh")
async def admin_refresh():
    success = await _do_login()
    return {"status": "ok" if success else "failed", "cookie_ready": bool(_cookie)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
