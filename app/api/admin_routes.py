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
    from app.camel.session_pool import session_pool
    session_pool.rotate_turns = config_manager.config.session_rotate_turns
    _usage_cache["data"] = None
    _usage_cache["time"] = 0
    return {"status": "ok", "config": config_manager.get_config()}


from app.core.account_pool import account_pool


@router.post("/admin/login")
async def admin_login(request: Request, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    """手动触发指定账号登录或手动填入 Cookie。"""
    verify_admin(x_admin_password)
    try:
        body = await request.json()
    except Exception:
        body = {}
    target = body.get("email", "")
    direct_cookie = body.get("cookie", "").strip()
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
    if direct_cookie:
        account.cookie = direct_cookie
        camel.cookie = direct_cookie
        camel.cookie_time = time.time()
        account.is_valid = True
        account.login_time = time.strftime("%m-%d %H:%M")
        await account_pool.mark_ok(account)
        config_manager.save()
        return {"status": "ok", "email": account.email, "method": "manual_cookie"}

    ok = await camel.refresh_cookie()
    account.is_valid = ok
    account.login_time = time.strftime("%m-%d %H:%M") if ok else ""
    if ok:
        await account_pool.mark_ok(account)
    config_manager.save()
    return {"status": "ok" if ok else "failed", "email": account.email, "method": "playwright"}


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
        try:
            await camel.ensure_cookie(http)
            ok = await camel.test_connection(http)
            acc.is_valid = ok
            acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
            results.append({"email": acc.email, "ok": ok})
        except Exception as e:
            results.append({"email": acc.email, "ok": False, "error": str(e)})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.get("/admin/models")
async def get_admin_models(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    http = await get_http_client()
    from .openai_routes import discover_models
    models = await discover_models(http)
    return {"object": "list", "data": models}


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


@router.post("/admin/accounts/{email}/toggle")
async def toggle_account(email: str, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    """一键启用/禁用指定账号。"""
    verify_admin(x_admin_password)
    account = next((a for a in config_manager.config.camel_accounts if a.email == email), None)
    if not account:
        raise HTTPException(status_code=404, detail={"error": {"message": f"account {email} not found"}})
    account.enabled = not getattr(account, "enabled", True)
    config_manager.save()
    return {"status": "ok", "email": email, "enabled": account.enabled}


@router.post("/admin/accounts/{email}/proxy")
async def set_account_proxy(email: str, request: Request, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    """设置指定账号的独立代理。"""
    verify_admin(x_admin_password)
    body = await request.json()
    account = next((a for a in config_manager.config.camel_accounts if a.email == email), None)
    if not account:
        raise HTTPException(status_code=404, detail={"error": {"message": f"account {email} not found"}})
    account.proxy = body.get("proxy", "").strip()
    config_manager.save()
    return {"status": "ok", "email": email, "proxy": account.proxy}


# ==========================================
# Mihomo 代理桥（一号一 IP）管理 API
# ==========================================

from app.core.mihomo import mihomo_manager


@router.get("/admin/mihomo/status")
async def get_mihomo_status(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    bin_path = mihomo_manager.detect_binary()
    is_running = mihomo_manager.process is not None and mihomo_manager.process.poll() is None
    return {
        "status": "ok",
        "running": is_running,
        "pid": mihomo_manager.process.pid if is_running else None,
        "binary_path": bin_path,
        "base_port": mihomo_manager.base_port,
        "api_port": mihomo_manager.api_port,
        "active_ports": list(mihomo_manager.port_map.values()),
        "subscription_count": len(mihomo_manager.subscriptions),
        "node_count": len(mihomo_manager.get_all_nodes())
    }


@router.post("/admin/mihomo/settings")
async def set_mihomo_settings(request: Request, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    body = await request.json()
    if "binary_path" in body:
        mihomo_manager.binary_path = body["binary_path"].strip()
    if "base_port" in body:
        mihomo_manager.base_port = int(body["base_port"])
    if "api_port" in body:
        mihomo_manager.api_port = int(body["api_port"])
    if "enabled" in body:
        mihomo_manager.enabled = bool(body["enabled"])
        if mihomo_manager.enabled:
            await mihomo_manager.apply_and_restart()
        else:
            mihomo_manager.stop()
    return {"status": "ok"}


@router.get("/admin/mihomo/subscriptions")
async def get_mihomo_subscriptions(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    return {"status": "ok", "subscriptions": mihomo_manager.subscriptions}


@router.post("/admin/mihomo/subscriptions")
async def add_mihomo_subscription(request: Request, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    body = await request.json()
    url = body.get("url", "").strip()
    name = body.get("name", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail={"error": {"message": "url required"}})
    try:
        sub = await mihomo_manager.add_subscription(url, name)
        return {"status": "ok", "subscription": sub}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"message": str(e)}})


@router.delete("/admin/mihomo/subscriptions/{sub_id}")
async def delete_mihomo_subscription(sub_id: str, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    mihomo_manager.remove_subscription(sub_id)
    await mihomo_manager.apply_and_restart()
    return {"status": "ok"}


@router.post("/admin/mihomo/subscriptions/{sub_id}/refresh")
async def refresh_mihomo_subscription(sub_id: str, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    sub = next((s for s in mihomo_manager.subscriptions if s.get("id") == sub_id), None)
    if not sub:
        raise HTTPException(status_code=404, detail={"error": {"message": "subscription not found"}})
    sub_obj = await mihomo_manager.add_subscription(sub["url"], sub.get("name", ""))
    await mihomo_manager.apply_and_restart()
    return {"status": "ok", "subscription": sub_obj}


@router.get("/admin/mihomo/nodes")
async def get_mihomo_nodes(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    nodes = mihomo_manager.get_all_nodes()
    # 标注绑定的账号
    acc_map = {a.mihomo_node: a.email for a in config_manager.config.camel_accounts if a.mihomo_node}
    for n in nodes:
        n["assigned_account"] = acc_map.get(n["name"])
    return {"status": "ok", "nodes": nodes}


@router.post("/admin/mihomo/delay-test")
async def test_mihomo_delays(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    results = await mihomo_manager.test_all_nodes()
    return {"status": "ok", "results": results}


@router.post("/admin/mihomo/assign")
async def assign_mihomo_nodes(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    """一键分配账号与独占节点（一人一号一 IP）。"""
    verify_admin(x_admin_password)
    mihomo_manager.assign_nodes_to_accounts(config_manager.config.camel_accounts)
    config_manager.save()
    await mihomo_manager.apply_and_restart()
    return {
        "status": "ok",
        "accounts": [
            {"email": a.email, "proxy": a.proxy, "node": a.mihomo_node, "enabled": a.enabled}
            for a in config_manager.config.camel_accounts
        ]
    }


# ==========================================
# 模型别名映射 API
# ==========================================

@router.get("/admin/model-aliases")
async def get_model_aliases(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    return {"status": "ok", "model_aliases": config_manager.config.model_aliases or {}}


@router.post("/admin/model-aliases")
async def set_model_aliases(request: Request, x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    body = await request.json()
    aliases = body.get("model_aliases", {})
    config_manager.config.model_aliases = aliases
    config_manager.save()
    from .openai_routes import invalidate_model_cache
    invalidate_model_cache()
    return {"status": "ok", "model_aliases": config_manager.config.model_aliases}


@router.get("/health")
async def health():
    accounts = []
    now = time.time()
    for acc in config_manager.config.camel_accounts:
        c = get_camel_client(acc)
        cooldown_left = max(0, int(account_pool._cooldown_until.get(acc.email, 0) - now))
        accounts.append({
            "email": acc.email,
            "enabled": getattr(acc, "enabled", True),
            "cookie_ready": bool(c.cookie),
            "proxy": getattr(acc, "proxy", ""),
            "mihomo_node": getattr(acc, "mihomo_node", ""),
            "is_valid": acc.is_valid,
            "login_time": acc.login_time,
            "last_test": acc.last_test,
            "in_flight": account_pool._in_use.get(acc.email, 0),
            "cooldown_seconds": cooldown_left,
            "has_custom_cookie": bool(acc.cookie),
        })
    from .openai_routes import _model_cache
    return {
        "status": "ok",
        "accounts": accounts,
        "model_count": len(_model_cache) if _model_cache else 0,
        "models_cached": _model_cache is not None,
        "mihomo_running": mihomo_manager.process is not None and mihomo_manager.process.poll() is None
    }


@router.get("/admin/logs")
async def get_logs(x_admin_password: Optional[str] = Header(None, alias="x-admin-password")):
    """最近日志（内存环形缓冲，供管理面板实时查看）。"""
    verify_admin(x_admin_password)
    from app.core.logger import get_recent_logs
    return {"status": "ok", "logs": get_recent_logs(300)}


@router.get("/")
async def serve_web_ui():
    web = Path(__file__).parent.parent.parent / "web" / "index.html"
    if web.exists():
        return FileResponse(str(web))
    raise HTTPException(status_code=404)
