"""API 层共享依赖：验权、账号/client 注册表、账号池调度。"""

from typing import Optional, Set

from fastapi import HTTPException

from app.core.config import config_manager, CamelAccount
from app.core.account_pool import account_pool
from app.camel.client import CamelClient

_clients: dict[str, CamelClient] = {}


def get_camel_client(account: CamelAccount) -> CamelClient:
    if account.email not in _clients:
        _clients[account.email] = CamelClient(account.email, account.password)
    c = _clients[account.email]
    c.password = account.password  # 密码可能在面板里更新过
    return c


def check_api_key(authorization: Optional[str], x_api_key: Optional[str]):
    key = None
    if authorization:
        key = authorization.replace("Bearer ", "").strip()
    elif x_api_key:
        key = x_api_key.strip()
    if not key or not config_manager.validate_api_key(key):
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key"}})


async def pick_ready_account(http, exclude: Optional[Set[str]] = None):
    """从账号池获取一个可用账号（并发槽位 + 排队 + 冷却），确保 cookie 就绪。

    返回 (account, client)。调用方必须在请求结束时调用 account_pool.release(account)；
    登录失败会 mark_failed 进入冷却。全部账号不可用（忙/冷却中）且排队超时时抛 503。
    """
    account = await account_pool.acquire(exclude=exclude)
    if not account:
        raise HTTPException(status_code=503, detail={
            "error": {"message": "no available camel account (all busy or cooling down)"}})
    client = get_camel_client(account)
    try:
        if not await client.ensure_cookie(http):
            await account_pool.mark_failed(account)
            raise HTTPException(status_code=503, detail={
                "error": {"message": f"login failed for {account.email}"}})
    except HTTPException:
        await account_pool.mark_failed(account)
        raise
    except Exception:
        await account_pool.mark_failed(account)
        raise
    return account, client
