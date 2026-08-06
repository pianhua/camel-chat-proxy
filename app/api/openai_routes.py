"""OpenAI 兼容端点 — HTTP 薄层。"""

import base64
import json
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse

from app.core.config import config_manager
from app.core.http import get_http_client
from app.core.logger import get_logger
from app.core.account_pool import account_pool
from app.camel.client import CamelAPIError
from app.camel.session_pool import session_pool
from app.convert.openai_conv import build_payload, OPENAI_SIZE_TO_CAMEL
from app.convert.attachments import resolve_image_to_data_url
from .deps import check_api_key, pick_ready_account, get_camel_client

router = APIRouter()

logger = get_logger(__name__)


class _RetryRequest(Exception):
    """内部信号：上游 5xx 时换账号重试一次。"""

session_pool.rotate_turns = config_manager.config.session_rotate_turns

_model_cache: Optional[list] = None
_model_cache_time: float = 0
MODEL_CACHE_TTL = 3600

IMAGE_MODEL_MAP = {
    "dall-e-3": "gpt-image-2",
    "dall-e-2": "gpt-image-2",
    "gpt-image-2": "gpt-image-2",
    "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",
}

# CaMeL 不暴露模型上下文上限，此处为社区常识默认值，可被 config.model_contexts 覆盖。
MODEL_CONTEXTS = {
    "claude-opus-4-8": 200000, "claude-opus-4-7": 200000, "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000, "claude-sonnet-5": 200000, "claude-haiku-4-5": 200000,
    "claude-fable-5": 200000,
    "gpt-5.5-pro": 400000, "gpt-5.5": 400000, "gpt-5.4": 400000, "gpt-4o": 128000, "gpt-5.6-sol": 400000,
    "gemini-3.1-pro": 1000000, "gemini-3.5-flash": 1000000,
    "deepseek-v3.2": 128000, "deepseek-r1": 128000, "deepseek-v4-pro": 128000,
    "kimi-k3": 128000,
    "gpt-image-2": 0, "gemini-3-pro-image-preview": 0,
}


def _context_window(model: str) -> int:
    """模型上下文上限：config.model_contexts 优先，其次内置默认表。"""
    overrides = config_manager.config.model_contexts or {}
    return int(overrides.get(model, MODEL_CONTEXTS.get(model, 0)) or 0)


async def discover_models(http) -> list:
    global _model_cache, _model_cache_time
    now = time.time()
    if _model_cache and (now - _model_cache_time) < MODEL_CACHE_TTL:
        return _model_cache
    account = config_manager.get_next_account()
    if not account:
        return _model_cache or []
    client = get_camel_client(account)
    if not await client.ensure_cookie(http):
        return _model_cache or []
    try:
        raw = await client.get_models(http)
        result = [
            {
                "id": m["id"], "object": "model", "created": 0,
                "owned_by": m.get("providerId", "CaMeL"),
                "context_window": _context_window(m["id"]),
            }
            for m in raw if m.get("type") in ("chat", "image")
        ]
        _model_cache, _model_cache_time = result, now
        logger.info("[Models] Discovered %d models", len(result))
        return result
    except Exception as e:
        logger.warning("[Models] Discovery failed: %s", e, exc_info=True)
        if _model_cache:
            return _model_cache
        raise


def invalidate_model_cache():
    global _model_cache, _model_cache_time
    _model_cache, _model_cache_time = None, 0


@router.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    check_api_key(authorization, x_api_key)
    http = await get_http_client()
    return {"object": "list", "data": await discover_models(http)}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    check_api_key(request.headers.get("authorization"), request.headers.get("x-api-key"))
    body = await request.json()
    model = body.get("model", "claude-opus-4-7")
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "messages required"}})

    http = await get_http_client()
    web_search = bool(body.get("web_search", config_manager.config.web_search))

    # 专家模式采样参数透传（CaMeL /api/chat/completion 支持 sampling 对象，字段为驼峰命名）
    sampling = {}
    for k, v in (("temperature", body.get("temperature")), ("topP", body.get("top_p")),
                 ("maxTokens", body.get("max_tokens")), ("frequencyPenalty", body.get("frequency_penalty")),
                 ("presencePenalty", body.get("presence_penalty"))):
        if v is not None:
            sampling[k] = v
    message_mode = body.get("message_mode", config_manager.config.message_mode or "auto")

    excluded: set = set()
    attempt = 0
    while True:
        account, camel = await pick_ready_account(http, exclude=excluded)
        try:
            session_id = await session_pool.get_session(http, camel, model, account.email)
            payload = await build_payload(http, camel, messages, model, session_id, web_search,
                                          sampling=sampling or None, message_mode=message_mode)
            session_pool.record_turn(account.email)
            request_id = str(uuid.uuid4())

            async def _after_first_reply():
                if payload.get("userText"):
                    await session_pool.sync_title(http, camel, account.email, payload["userText"])

            if stream:
                async def _stream():
                    try:
                        first = True
                        async for chunk in camel.stream_chat(http, payload):
                            if first:
                                first = False
                                await _after_first_reply()
                            data = {
                                "id": f"chatcmpl-{request_id}", "object": "chat.completion.chunk",
                                "created": 0, "model": model,
                                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                    except Exception as e:
                        yield f"data: {json.dumps({'error': {'message': f'CaMeL API error: {e}'}})}\n\n"
                        yield "data: [DONE]\n\n"
                    finally:
                        await account_pool.release(account)
                return StreamingResponse(_stream(), media_type="text/event-stream")

            try:
                text = await camel.chat_completion(http, payload)
                await _after_first_reply()
            except CamelAPIError as e:
                await account_pool.mark_failed(account)
                if attempt == 0 and e.status_code >= 500:
                    excluded.add(account.email)
                    raise _RetryRequest()
                raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})

            await account_pool.release(account)
            return {
                "id": f"chatcmpl-{request_id}", "object": "chat.completion", "created": 0, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": _estimate_usage(payload, text),
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
            logger.warning("[Chat] unexpected error: %s", e, exc_info=True)
            raise HTTPException(status_code=502, detail={"error": {"message": str(e)}})


def _estimate_usage(payload: dict, output_text: str) -> dict:
    """usage 估算：上游不返回 token 统计，按字符数粗估（中英混合约 4 字符/token）。"""
    prompt_chars = sum(len(str(m.get("content", ""))) for m in payload.get("messages", []))
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(0, len(output_text or "") // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    check_api_key(request.headers.get("authorization"), request.headers.get("x-api-key"))
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": {"message": "prompt is required"}})

    model = IMAGE_MODEL_MAP.get(body.get("model", "gpt-image-2"), "gpt-image-2")
    try:
        n = min(int(body.get("n", 1)), 4)
    except (TypeError, ValueError):
        n = 1
    size = OPENAI_SIZE_TO_CAMEL.get(body.get("size", "1024x1024"), "auto")

    http = await get_http_client()
    # 参考图（图生图）：接受 base64 data URL / http URL / 本地路径
    prior_images = []
    for item in body.get("prior_images", []) or []:
        du = await resolve_image_to_data_url(http, item)
        if du:
            prior_images.append(du)

    account, camel = await pick_ready_account(http)
    try:
        session_id = await session_pool.get_session(http, camel, model, account.email)
        try:
            urls = await camel.generate_image(http, prompt=prompt, model=model, size=size, n=n,
                                              prior_images=prior_images, session_id=session_id)
        except CamelAPIError as e:
            await account_pool.mark_failed(account)
            raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})
    finally:
        await account_pool.release(account)

    data = []
    for du in urls:
        m = re.match(r"data:image/\w+;base64,(.+)", du, re.S)
        data.append({"b64_json": m.group(1) if m else base64.b64encode(du.encode()).decode()})
    return {"created": int(time.time()), "data": data}
