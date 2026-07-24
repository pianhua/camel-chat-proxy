"""CaMeL Chat (chat.camel-hub.com) → OpenAI-compatible API proxy for SillyTavern."""

import os
import re
import json
import uuid
import logging
from typing import AsyncGenerator

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from cookie_manager import get_manager, Account

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CaMeL Chat OpenAI Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base URL for the free CaMeL Chat web app
CAMEL_BASE = os.environ.get("CAMEL_BASE", "https://chat.camel-hub.com")


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


def _account_cookies(account: Account) -> dict:
    return {
        "camel_session": account.camel_session,
    }


def _to_camel_model(openai_model: str) -> str:
    """Map an OpenAI model id to a CaMeL model id."""
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
    if openai_model in known:
        return known[openai_model]
    if re.match(r"^[a-z0-9.-]+$", openai_model):
        return openai_model
    return "claude-opus-4-7"  # default


@app.on_event("startup")
async def startup_event():
    """Refresh cookies on startup."""
    manager = get_manager()
    await manager.refresh_all_cookies()
    valid = sum(1 for a in manager.accounts if a.is_valid)
    logger.info("Startup complete: %d/%d accounts ready", valid, len(manager.accounts))


@app.get("/v1/models")
async def list_models():
    manager = get_manager()
    account = manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail="No available accounts")

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{CAMEL_BASE}/api/chat/models",
            headers=_headers(),
            cookies=_account_cookies(account),
        )
        if r.status_code != 200:
            logger.error("Failed to list models: %s %s", r.status_code, r.text[:500])
            account.mark_failure()
            raise HTTPException(status_code=502, detail="CaMeL models endpoint failed")
        account.mark_success()
        models = r.json()
    return {
        "object": "list",
        "data": [
            {
                "id": m["id"],
                "object": "model",
                "created": 0,
                "owned_by": m.get("providerId", "CaMeL"),
            }
            for m in models
            if m.get("type") == "chat"
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = _to_camel_model(body.get("model", "claude-opus-4-7"))
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    # Build CaMeL message list: merge system into first user message, keep assistant prefill.
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    convo_messages = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not convo_messages:
        raise HTTPException(status_code=400, detail="at least one user or assistant message required")

    # Find the last user message index for session title
    last_user_idx = None
    for i, m in enumerate(convo_messages):
        if m.get("role") == "user":
            last_user_idx = i
    if last_user_idx is None:
        last_user_text = convo_messages[-1].get("content", "")
    else:
        last_user_text = convo_messages[last_user_idx].get("content", "")

    if system_parts:
        for m in convo_messages:
            if m.get("role") == "user":
                m["content"] = "[System instructions]\n" + "\n".join(system_parts) + "\n\n" + m.get("content", "")
                break

    camel_messages = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in convo_messages
    ]

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())

    manager = get_manager()
    account = manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail="No available accounts")

    logger.info("Using account: %s (requests: %d)", account.name, account.request_count)

    # 1. Create a CaMeL chat session
    async with httpx.AsyncClient(timeout=30.0) as client:
        session_r = await client.post(
            f"{CAMEL_BASE}/api/chat/sessions",
            headers=_headers({"content-type": "application/json"}),
            cookies=_account_cookies(account),
            json={"title": last_user_text[:64], "model": model},
        )
        if session_r.status_code != 201:
            logger.error("Failed to create session: %s %s", session_r.status_code, session_r.text[:500])
            account.mark_failure()
            raise HTTPException(status_code=502, detail="CaMeL session creation failed")
        session_id = session_r.json()["id"]

    # 2. Send the completion request
    camel_payload = {
        "messages": camel_messages,
        "model": model,
        "providerId": "CaMeL",
        "sessionId": session_id,
        "userMessageId": user_message_id,
        "assistantMessageId": assistant_message_id,
        "userText": last_user_text,
        "hasAttachments": False,
        "hasCustomSystemPrompt": False,
        "webSearch": False,
    }

    if stream:
        return StreamingResponse(
            _stream_completion(camel_payload, assistant_message_id, model, account),
            media_type="text/event-stream",
        )

    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{CAMEL_BASE}/api/chat/completion",
            headers=_headers({"content-type": "application/json"}),
            cookies=_account_cookies(account),
            json=camel_payload,
        )
        if r.status_code != 200:
            logger.error("CaMeL completion failed: %s %s", r.status_code, r.text[:500])
            account.mark_failure()
            raise HTTPException(status_code=502, detail="CaMeL completion failed")
        account.mark_success()
        manager.save()
        text = r.text

    return JSONResponse({
        "id": f"chatcmpl-{assistant_message_id}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


async def _stream_completion(
    payload: dict, assistant_message_id: str, model: str, account: Account
) -> AsyncGenerator[str, None]:
    """Yield OpenAI-style SSE chunks from CaMeL's plain-text response."""
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{CAMEL_BASE}/api/chat/completion",
            headers=_headers({"content-type": "application/json"}),
            cookies=_account_cookies(account),
            json=payload,
        )
        if r.status_code != 200:
            logger.error("CaMeL completion failed: %s %s", r.status_code, r.text[:500])
            account.mark_failure()
            raise HTTPException(status_code=502, detail="CaMeL completion failed")
        account.mark_success()
        get_manager().save()
        text = r.text

    for chunk in _split_text(text, chunk_size=4):
        data = {
            "id": f"chatcmpl-{assistant_message_id}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _split_text(text: str, chunk_size: int = 4) -> list[str]:
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i : i + chunk_size])
    return chunks


@app.get("/health")
async def health():
    manager = get_manager()
    return {
        "status": "ok",
        "accounts": [
            {
                "name": a.name,
                "enabled": a.enabled,
                "valid": a.is_valid,
                "request_count": a.request_count,
            }
            for a in manager.accounts
        ],
    }


@app.post("/admin/refresh")
async def admin_refresh():
    """Manually trigger cookie refresh for all accounts."""
    manager = get_manager()
    await manager.refresh_all_cookies()
    return {"status": "ok", "accounts": [
        {"name": a.name, "valid": a.is_valid, "enabled": a.enabled}
        for a in manager.accounts
    ]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
