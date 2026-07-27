"""管理端点 — 账号/配置/模型/用量。"""

import time
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import FileResponse
from pathlib import Path

from app.core.auth import verify_admin
from app.core.config import config_manager
from app.core.http import get_http_client
from .deps import get_camel_client

router = APIRouter()

_usage_cache: dict = {"data": None, "time": 0}
USAGE_CACHE_TTL = 1800  # 30 分钟，绝不自动轮询


@router.get("/admin/config")
async def get_admin_config(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    return config_manager.get_config()


@router.post("/admin/config")
async def set_admin_config(request: Request, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    config_manager.update_config(await request.json())
    return {"status": "ok", "config": config_manager.get_config()}


@router.post("/admin/login")
async def admin_login(request: Request):
    """手动触发指定账号登录（body 可选 {"email": "..."}），面板已带管理密码即可。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    target = body.get("email", "")
    account = None
    if target:
        account = next((a for a in config_manager.config.camel_accounts if a.email == target), None)
        if not account:
            raise HTTPException(status_code=404, detail={"error": {"message": f"account {target} not found"}})
    else:
        account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no account configured"}})
    camel = get_camel_client(account)
    ok = await camel.refresh_cookie()
    account.is_valid = ok
    account.login_time = time.strftime("%m-%d %H:%M") if ok else ""
    config_manager.save()
    return {"status": "ok" if ok else "failed", "email": account.email}


@router.post("/admin/refresh-cookies")
async def refresh_all(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    results = []
    for acc in config_manager.config.camel_accounts:
        camel = get_camel_client(acc)
        try:
            ok = await camel.refresh_cookie()
            acc.is_valid = ok
            acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
            results.append({"email": acc.email, "ok": ok})
        except Exception as e:
            results.append({"email": acc.email, "ok": False, "error": str(e)})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.post("/admin/test-accounts")
async def test_accounts(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    http = await get_http_client()
    results = []
    for acc in config_manager.config.camel_accounts:
        camel = get_camel_client(acc)
        await camel.ensure_cookie(http)
        ok = await camel.test_connection(http)
        acc.is_valid = ok
        acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
        results.append({"email": acc.email, "ok": ok})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.post("/admin/refresh-models")
async def refresh_models(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    from .openai_routes import invalidate_model_cache, discover_models
    invalidate_model_cache()
    http = await get_http_client()
    models = await discover_models(http)
    return {"status": "ok", "model_count": len(models)}


@router.get("/admin/usage")
async def get_usage(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    """用量聚合 — 仅手动调用，30 分钟缓存，绝不自动轮询。"""
    verify_admin(x_admin_password)
    now = time.time()
    if _usage_cache["data"] is not None and (now - _usage_cache["time"]) < USAGE_CACHE_TTL:
        return {"status": "ok", "cached": True, "accounts": _usage_cache["data"]}
    http = await get_http_client()
    rows = []
    for acc in config_manager.config.camel_accounts:
        camel = get_camel_client(acc)
        row = {"email": acc.email}
        try:
            await camel.ensure_cookie(http)
            row["usage"] = await camel.get_usage(http)
            row["search"] = await camel.get_search_limits(http)
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
    _usage_cache["data"] = rows
    _usage_cache["time"] = now
    return {"status": "ok", "cached": False, "accounts": rows}


@router.get("/health")
async def health():
    accounts = [{
        "email": acc.email,
        "cookie_ready": bool(get_camel_client(acc).cookie),
        "is_valid": acc.is_valid,
        "login_time": acc.login_time,
    } for acc in config_manager.config.camel_accounts]
    from .openai_routes import _model_cache
    return {"status": "ok", "accounts": accounts, "model_count": len(_model_cache) if _model_cache else 0}


@router.get("/")
async def serve_web_ui():
    web = Path(__file__).parent.parent.parent / "web" / "index.html"
    if web.exists():
        return FileResponse(str(web))
    raise HTTPException(status_code=404)
