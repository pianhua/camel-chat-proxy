"""Anthropic Messages API 兼容路由 — RikkaHub 等客户端适配。"""

import json
import time
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse

from .config import config_manager
from .camel_client import CamelClient, CamelAPIError
from .routes import _get_http_client, _get_camel_client

anthropic_router = APIRouter()


def _validate_api_key(authorization: Optional[str] = None, x_api_key: Optional[str] = None) -> bool:
    key = None
    if authorization:
        key = authorization.replace("Bearer ", "").strip()
    elif x_api_key:
        key = x_api_key
    if not key:
        return False
    return config_manager.validate_api_key(key)


ANTHROPIC_MODEL_MAP = {
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
    "claude-opus-4-20250514": "claude-opus-4-7",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-5": "claude-sonnet-4-6",
    "claude-opus-4-5": "claude-opus-4-7",
    "claude-opus-4-1": "claude-opus-4-7",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5",
}


def _to_camel_model(anthropic_model: str) -> str:
    if anthropic_model in ANTHROPIC_MODEL_MAP:
        return ANTHROPIC_MODEL_MAP[anthropic_model]
    return anthropic_model


@anthropic_router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    authorization: Optional[str] = Header(None),
):
    """Anthropic Messages API — POST /v1/messages。"""
    if not _validate_api_key(authorization=authorization, x_api_key=x_api_key):
        raise HTTPException(status_code=401, detail={"error": {"type": "authentication_error", "message": "invalid api key"}})

    body = await request.json()
    model = _to_camel_model(body.get("model", "claude-sonnet-4-6"))
    messages = body.get("messages", [])
    system = body.get("system", "")
    max_tokens = body.get("max_tokens", 4096)
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request_error", "message": "messages required"}})

    openai_messages = []
    if system:
        openai_messages.append({"role": "system", "content": system})
    for msg in messages:
        openai_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"type": "service_unavailable", "message": "no account configured"}})

    http_client = await _get_http_client()
    camel = _get_camel_client(account)
    await camel.ensure_cookie(http_client)

    session_id = None
    try:
        session_id = await camel.create_session(http_client, title="chat", model=model)
    except Exception:
        pass

    camel_messages = [{"role": m["role"], "content": m["content"]} for m in openai_messages if m["role"] != "system"]
    system_prompt = next((m["content"] for m in openai_messages if m["role"] == "system"), "")

    msg_id = str(uuid.uuid4())

    if stream:
        async def _stream_sse():
            try:
                async for chunk in camel.stream_chat(
                    http_client=http_client,
                    messages=camel_messages,
                    model=model,
                    system_prompt=system_prompt,
                    session_id=session_id,
                ):
                    event = {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": chunk},
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

                yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            except CamelAPIError as e:
                err = {"type": "error", "error": {"type": "api_error", "message": str(e)}}
                yield f"event: error\ndata: {json.dumps(err)}\n\n"

        return StreamingResponse(_stream_sse(), media_type="text/event-stream")

    try:
        text = await camel.chat_completion(
            http_client=http_client,
            messages=camel_messages,
            model=model,
            system_prompt=system_prompt,
            session_id=session_id,
        )
    except CamelAPIError as e:
        raise HTTPException(status_code=e.status_code,
                          detail={"error": {"type": "api_error", "message": str(e)}})

    return {
        "id": f"msg_{msg_id}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
