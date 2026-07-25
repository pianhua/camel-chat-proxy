"""API路由 — OpenAI 兼容接口 + 模型发现 + 管理后台。"""

import json
import time
import uuid
import httpx
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pathlib import Path

from .auth import verify_admin
from .config import config_manager, CamelAccount
from .camel_client import CamelClient, CamelAPIError, CAMEL_BASE

router = APIRouter()

# ─── 全局状态 ─────────────────────────────────────────────────
_http_client: Optional[httpx.AsyncClient] = None
_model_cache: Optional[dict] = None
_model_cache_time: float = 0
MODEL_CACHE_TTL = 3600

# 账号 → CamelClient 实例
_clients: dict[str, CamelClient] = {}


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=30.0),
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30),
        )
    return _http_client


def _get_camel_client(account: CamelAccount) -> CamelClient:
    key = account.email
    if key not in _clients:
        _clients[key] = CamelClient(account)
    return _clients[key]


# ─── API Key 验证 ─────────────────────────────────────────────

def validate_api_key(authorization: Optional[str]) -> bool:
    if not authorization:
        return False
    key = authorization.replace("Bearer ", "").strip()
    return config_manager.validate_api_key(key)


# ─── 模型发现 ─────────────────────────────────────────────────

async def _discover_models(http_client: httpx.AsyncClient) -> list:
    """从 CaMeL API 获取可用模型列表。"""
    global _model_cache, _model_cache_time
    now = time.time()
    if _model_cache and (now - _model_cache_time) < MODEL_CACHE_TTL:
        return _model_cache

    account = config_manager.get_next_account()
    if not account:
        # 返回上次缓存
        if _model_cache:
            return _model_cache
        return []

    client = _get_camel_client(account)
    await client.ensure_cookie(http_client)
    try:
        raw_models = await client.get_models(http_client)
        result = [
            {"id": m["id"], "object": "model", "created": 0, "owned_by": m.get("providerId", "CaMeL")}
            for m in raw_models if m.get("type") in ("chat", "image")
        ]
        _model_cache = result
        _model_cache_time = now
        print(f"[Models] Discovered {len(result)} models")
        return result
    except Exception as e:
        print(f"[Models] Discovery failed: {e}")
        if _model_cache:
            return _model_cache
        raise


# ─── OpenAI 端点 ──────────────────────────────────────────────

@router.get("/v1/models")
async def list_models(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
):
    api_key = authorization or (f"Bearer {x_api_key}" if x_api_key else None)
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key"}})

    http_client = await _get_http_client()
    models = await _discover_models(http_client)
    return {"object": "list", "data": models}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    # API Key 验证
    authorization = request.headers.get("authorization")
    x_api_key = request.headers.get("x-api-key")
    api_key = authorization or (f"Bearer {x_api_key}" if x_api_key else None)
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key"}})

    body = await request.json()
    model = body.get("model", "claude-opus-4-7")
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    temperature = body.get("temperature")
    top_p = body.get("top_p")
    max_tokens = body.get("max_tokens")

    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "messages required"}})

    # 分离 system prompt
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    system_prompt = "\n".join(system_parts) if system_parts else ""
    convo = [{"role": m["role"], "content": m.get("content", "")} for m in messages if m.get("role") in ("user", "assistant")]

    if not convo:
        raise HTTPException(status_code=400, detail={"error": {"message": "user or assistant message required"}})

    # 获取账号和 client
    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no camel account configured"}})

    http_client = await _get_http_client()
    camel = _get_camel_client(account)
    await camel.ensure_cookie(http_client)

    # 获取或创建 session
    session_id = body.get("session_id")  # 可选，复用会话
    if not session_id:
        try:
            session_id = await camel.create_session(http_client, title="chat", model=model)
        except Exception:
            session_id = None  # 失败也不阻塞

    # 构建 payload 消息（system prompt 由 camel_client 处理）
    camel_messages = [{"role": m["role"], "content": m["content"]} for m in convo]

    # web_search
    web_search = body.get("web_search", config_manager.config.web_search)

    assistant_msg_id = str(uuid.uuid4())

    if stream:
        async def _stream():
            try:
                async for chunk in camel.stream_chat(
                    http_client=http_client,
                    messages=camel_messages,
                    model=model,
                    system_prompt=system_prompt,
                    web_search=web_search,
                    session_id=session_id,
                ):
                    data = {
                        "id": f"chatcmpl-{assistant_msg_id}",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except CamelAPIError as e:
                err = {"error": {"message": f"CaMeL API error: {e}"}}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    # 非流式
    try:
        text = await camel.chat_completion(
            http_client=http_client,
            messages=camel_messages,
            model=model,
            system_prompt=system_prompt,
            web_search=web_search,
            session_id=session_id,
        )
    except CamelAPIError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})

    return {
        "id": f"chatcmpl-{assistant_msg_id}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ─── 健康检查 ─────────────────────────────────────────────────

@router.get("/health")
async def health():
    http_client = await _get_http_client()
    accounts = config_manager.config.camel_accounts
    account_status = []
    for acc in accounts:
        camel = _get_camel_client(acc)
        account_status.append({
            "email": acc.email,
            "cookie_ready": bool(camel.cookie),
            "is_valid": acc.is_valid,
            "login_time": acc.login_time,
        })

    return {
        "status": "ok",
        "accounts": account_status,
        "models_cached": _model_cache is not None,
        "model_count": len(_model_cache) if _model_cache else 0,
    }


# ─── 管理 API ─────────────────────────────────────────────────

@router.get("/admin/config")
async def get_admin_config(x_admin_password: str = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    return config_manager.get_config()


@router.post("/admin/config")
async def set_admin_config(request: Request, x_admin_password: str = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    body = await request.json()
    config_manager.update_config(body)
    return {"status": "ok", "config": config_manager.get_config()}


@router.post("/admin/login")
async def admin_login(request: Request):
    """手动触发指定账号登录（POST body 可选 email 参数）。"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    http_client = await _get_http_client()
    target_email = body.get("email", "")

    if target_email:
        # 查找指定账号
        account = None
        for acc in config_manager.config.camel_accounts:
            if acc.email == target_email:
                account = acc
                break
        if not account:
            raise HTTPException(status_code=404, detail={"error": {"message": f"account {target_email} not found"}})
    else:
        # 轮询
        account = config_manager.get_next_account()

    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no account configured"}})

    camel = _get_camel_client(account)
    success = await camel.refresh_cookie(http_client)
    account.is_valid = success
    account.login_time = time.strftime("%m-%d %H:%M") if success else ""
    config_manager.save()
    return {"status": "ok" if success else "failed", "email": account.email}


@router.post("/admin/refresh-cookies")
async def refresh_all(x_admin_password: str = Header(None, alias="x-admin-password")):
    """刷新所有账号 Cookie。"""
    verify_admin(x_admin_password)
    http_client = await _get_http_client()
    results = []
    for acc in config_manager.config.camel_accounts:
        camel = _get_camel_client(acc)
        try:
            ok = await camel.refresh_cookie(http_client)
            acc.is_valid = ok
            acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
            results.append({"email": acc.email, "ok": ok})
        except Exception as e:
            results.append({"email": acc.email, "ok": False, "error": str(e)})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.post("/admin/test-accounts")
async def test_accounts(x_admin_password: str = Header(None, alias="x-admin-password")):
    """测试所有账号连接。"""
    verify_admin(x_admin_password)
    http_client = await _get_http_client()
    results = []
    for acc in config_manager.config.camel_accounts:
        camel = _get_camel_client(acc)
        await camel.ensure_cookie(http_client)
        ok = await camel.test_connection(http_client)
        acc.is_valid = ok
        acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
        results.append({"email": acc.email, "ok": ok})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.post("/admin/refresh-models")
async def refresh_models(x_admin_password: str = Header(None, alias="x-admin-password")):
    """强制刷新模型缓存。"""
    verify_admin(x_admin_password)
    global _model_cache, _model_cache_time
    _model_cache = None
    _model_cache_time = 0
    http_client = await _get_http_client()
    models = await _discover_models(http_client)
    return {"status": "ok", "model_count": len(models)}


# ─── Web 管理面板 ─────────────────────────────────────────────

@router.get("/")
async def serve_web_ui():
    """返回 Web 管理面板。"""
    web_dir = Path(__file__).parent.parent / "web" / "index.html"
    if web_dir.exists():
        return FileResponse(str(web_dir))
    raise HTTPException(status_code=404)
