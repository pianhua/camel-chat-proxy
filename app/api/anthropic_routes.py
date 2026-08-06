"""Anthropic Messages API — HTTP 薄层。"""

import json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse

from app.core.config import config_manager
from app.core.http import get_http_client
from app.core.account_pool import account_pool
from app.camel.client import CamelAPIError
from app.camel.session_pool import session_pool
from app.convert.anthropic_conv import to_camel_model, to_openai_messages
from app.convert.openai_conv import build_payload
from .deps import check_api_key, pick_ready_account

router = APIRouter()


class _RetryRequest(Exception):
    """内部信号：上游 5xx 时换账号重试一次。"""


def _err(t, m):
    return {"error": {"type": t, "message": m}}


@router.post("/v1/messages")
async def anthropic_messages(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key"),
                             authorization: Optional[str] = Header(None)):
    try:
        check_api_key(authorization, x_api_key)
    except HTTPException:
        raise HTTPException(status_code=401, detail=_err("authentication_error", "invalid api key"))

    body = await request.json()
    model = to_camel_model(body.get("model", "claude-sonnet-4-6"))
    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail=_err("invalid_request_error", "messages required"))
    stream = bool(body.get("stream", False))
    web_search = bool(body.get("web_search", config_manager.config.web_search))

    # Anthropic 采样参数 → CaMeL sampling（字段为驼峰命名，浏览器实测）
    sampling = {}
    for k, v in (("temperature", body.get("temperature")), ("maxTokens", body.get("max_tokens")),
                 ("topP", body.get("top_p")), ("topK", body.get("top_k")),
                 ("frequencyPenalty", body.get("frequency_penalty")),
                 ("presencePenalty", body.get("presence_penalty"))):
        if v is not None:
            sampling[k] = v
    message_mode = body.get("message_mode", config_manager.config.message_mode or "auto")

    openai_messages = to_openai_messages(messages, body.get("system", ""))

    http = await get_http_client()

    excluded: set = set()
    attempt = 0
    while True:
        try:
            account, camel = await pick_ready_account(http, exclude=excluded)
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=_err("service_unavailable", str(e.detail)))

        try:
            session_id = await session_pool.get_session(http, camel, model, account.email)
            payload = await build_payload(http, camel, openai_messages, model, session_id, web_search,
                                          sampling=sampling or None, message_mode=message_mode)
            session_pool.record_turn(account.email)
            msg_id = str(uuid.uuid4())

            async def _after_first_reply():
                if payload.get("userText"):
                    await session_pool.sync_title(http, camel, account.email, payload["userText"])

            if stream:
                async def _stream_sse():
                    try:
                        start_msg = {
                            "type": "message_start",
                            "message": {
                                "id": f"msg_{msg_id}", "type": "message", "role": "assistant",
                                "content": [], "model": model,
                                "stop_reason": None, "stop_sequence": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            },
                        }
                        yield f"event: message_start\ndata: {json.dumps(start_msg, ensure_ascii=False)}\n\n"
                        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                        first = True
                        async for chunk in camel.stream_chat(http, payload):
                            if first:
                                first = False
                                await _after_first_reply()
                            event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}}
                            yield f"event: content_block_delta\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
                        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
                    except Exception as e:
                        yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(e)}})}\n\n"
                    finally:
                        await account_pool.release(account)
                return StreamingResponse(_stream_sse(), media_type="text/event-stream")

            try:
                text = await camel.chat_completion(http, payload)
                await _after_first_reply()
            except CamelAPIError as e:
                await account_pool.mark_failed(account)
                if attempt == 0 and e.status_code >= 500:
                    excluded.add(account.email)
                    raise _RetryRequest()
                raise HTTPException(status_code=e.status_code, detail=_err("api_error", str(e)))

            await account_pool.release(account)
            return {
                "id": f"msg_{msg_id}", "type": "message", "role": "assistant",
                "content": [{"type": "text", "text": text}], "model": model,
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        except _RetryRequest:
            await account_pool.release(account)
            attempt += 1
            continue
        except HTTPException:
            await account_pool.release(account)
            raise
        except Exception as e:
            await account_pool.release(account)
            from app.core.logger import get_logger
            get_logger(__name__).warning("[Messages] unexpected error: %s", e, exc_info=True)
            raise HTTPException(status_code=502, detail=_err("api_error", str(e)))
