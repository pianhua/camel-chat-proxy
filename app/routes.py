"""API路由 — OpenAI 兼容接口 + 模型发现 + 管理后台。"""

import json
import time
import uuid
import os
import re
import httpx
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pathlib import Path

from .auth import verify_admin
from .config import config_manager, CamelAccount
from .camel_client import CamelClient, CamelAPIError, CAMEL_BASE
from .session_store import session_store

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


# ─── 多模态消息处理 ─────────────────────────────────────────

def _extract_text(content) -> str:
    """从 content（可能为字符串或数组）提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        return "\n".join(parts)
    return str(content)


def _parse_multimodal(content):
    """解析多模态消息，返回 (text, [image_urls], [file_urls])。"""
    if isinstance(content, str):
        image_urls = re.findall(r'!\[.*?\]\((https?://[^\)]+)\)', content)
        file_urls = re.findall(r'\[📎.*?\]\(([^\)]+)\)', content)
        return content, image_urls, file_urls
    if isinstance(content, list):
        text_parts = []
        image_urls = []
        file_urls = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if url:
                        image_urls.append(url)
                        text_parts.append(f"[image: {url}]")
                elif item.get("type") == "file_url":
                    url = item.get("file_url", {}).get("url", "")
                    if url:
                        file_urls.append(url)
                        text_parts.append(f"[file: {url}]")
        return "\\n".join(text_parts) if text_parts else str(content), image_urls, file_urls
    return str(content), [], []


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

    # 分离 system prompt + 处理多模态消息
    system_parts = [_extract_text(m.get("content", "")) for m in messages if m.get("role") == "system"]
    system_prompt = "\n".join(system_parts) if system_parts else ""

    convo = []
    multi_medias = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        text_part, image_urls, file_urls = _parse_multimodal(content)
        convo.append({"role": m["role"], "content": text_part})
        for url in image_urls:
            multi_medias.append({"type": "image", "url": url})

    if not convo:
        raise HTTPException(status_code=400, detail={"error": {"message": "user or assistant message required"}})

    # 获取账号和 client
    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no camel account configured"}})

    http_client = await _get_http_client()
    camel = _get_camel_client(account)
    await camel.ensure_cookie(http_client)

    # 处理文件上传：下载文件 → 上传到 CaMeL parse-file → 把文本附加到消息
    if file_urls:
        for file_url in file_urls:
            try:
                # 判断是否本地路径
                is_local = (
                    file_url.startswith("file:///") or
                    bool(re.match(r'^[A-Za-z]:[/\\\\]', file_url)) or
                    (file_url.startswith("/") and not file_url.startswith("//"))
                )
                if is_local:
                    # 本地文件：直接读取
                    local_path = file_url
                    if local_path.startswith("file:///"):
                        local_path = local_path[8:]  # 去掉 file:///
                    # Windows 路径修正
                    local_path = os.path.normpath(local_path)
                    if not os.path.exists(local_path):
                        print(f"[File Upload] Local file not found: {local_path}")
                        continue
                    file_name = os.path.basename(local_path) or "file"
                    # 推断 MIME
                    ext = os.path.splitext(file_name)[1].lower()
                    mime_map = {
                        ".txt":"text/plain", ".md":"text/markdown", ".json":"application/json",
                        ".py":"text/x-python", ".js":"text/javascript", ".html":"text/html",
                        ".css":"text/css", ".csv":"text/csv", ".xml":"text/xml",
                        ".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png",
                        ".gif":"image/gif", ".webp":"image/webp", ".bmp":"image/bmp",
                        ".pdf":"application/pdf", ".doc":"application/msword",
                        ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                    content_type = mime_map.get(ext, "application/octet-stream")
                    with open(local_path, "rb") as f:
                        file_content = f.read()
                else:
                    # 远程 URL：下载
                    file_resp = await http_client.get(file_url, timeout=30.0, follow_redirects=True)
                    if file_resp.status_code != 200:
                        continue
                    file_content = file_resp.content
                    file_name = file_url.split("/")[-1].split("?")[0] or "file"
                    content_type = file_resp.headers.get("content-type", "application/octet-stream")
                    if ";" in content_type:
                        content_type = content_type.split(";")[0].strip()
                
                # 上传到 CaMeL parse-file
                if content_type.startswith("image/"):
                    # 图片：base64 内联，不走 parse-file
                    import base64
                    img_b64 = base64.b64encode(file_content).decode()
                    convo[-1]["content"] += f"\n\n![本地图片](data:{content_type};base64,{img_b64})"
                    continue
                parsed = await camel.parse_file(http_client, file_name, file_content, content_type)
                file_text = parsed.get("text", "")
                if file_text and convo:
                    convo[-1]["content"] += f"\n\n[文件内容: {file_name}]\n{file_text}"
            except Exception as e:
                print(f"[File Upload] Failed: {e}")

    # 获取或复用 session
    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no camel account configured"}})

    http_client = await _get_http_client()
    camel = _get_camel_client(account)
    await camel.ensure_cookie(http_client)

    # 获取或复用 session（按账号+模型缓存，避免越积越多）
    session_id = body.get("session_id")
    if not session_id:
        session_id = session_store.get(account.email, model)
    if not session_id:
        try:
            session_id = await camel.create_session(http_client, title="chat", model=model)
            session_store.set(account.email, model, session_id)
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


# ─── OpenAI Images API ───────────────────────────────────────

# 模型名映射（兼容 dall-e-3 等标准名）
IMAGE_MODEL_MAP = {
    "dall-e-3": "gpt-image-2",
    "dall-e-2": "gpt-image-2",
    "gpt-image-2": "gpt-image-2",
    "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",
}


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    """OpenAI 兼容图片生成接口。"""
    authorization = request.headers.get("authorization")
    x_api_key = request.headers.get("x-api-key")
    api_key = authorization or (f"Bearer {x_api_key}" if x_api_key else None)
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key"}})

    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": {"message": "prompt is required"}})

    # 映射模型名
    raw_model = body.get("model", "gpt-image-2")
    model = IMAGE_MODEL_MAP.get(raw_model, "gpt-image-2")
    n = min(body.get("n", 1), 4)  # 最多 4 张
    size = body.get("size", "1024x1024")
    response_format = body.get("response_format", "b64_json")

    # 获取账号
    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no account configured"}})

    http_client = await _get_http_client()
    camel = _get_camel_client(account)
    await camel.ensure_cookie(http_client)

    # 通过 chat completion 调用生图模型
    images = []
    import re, base64

    for i in range(n):
        try:
            # 拼接 size 到 prompt
            full_prompt = prompt
            if size and "1024" not in prompt.lower():
                full_prompt = f"{size} — {prompt}"

            # 创建临时会话
            session_id = uuid.uuid4().hex[:32]
            try:
                session_id = await camel.create_session(http_client, title="image-gen", model=model)
            except Exception:
                pass

            try:
                text = await camel.chat_completion(
                    http_client=http_client,
                    messages=[{"role": "user", "content": full_prompt}],
                    model=model,
                    session_id=session_id,
                )
            finally:
                # 删除临时生图会话，避免堆积
                try:
                    await camel.delete_conversation(http_client, session_id)
                except Exception:
                    pass

            # 提取 base64
            match = re.search(r"data:image/\w+;base64,([^)\"]+)", text)
            if match:
                b64 = match.group(1)
                if response_format == "b64_json":
                    images.append({"b64_json": b64})
                else:
                    # url 格式：暂时返回 base64（CaMeL 没有持久化 URL）
                    images.append({"b64_json": b64})
            else:
                # 没匹配到图片，返回原始文本
                images.append({"b64_json": ""})

        except CamelAPIError as e:
            raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})

    return {
        "created": int(time.time()),
        "data": images,
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
