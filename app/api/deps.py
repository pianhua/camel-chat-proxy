"""API 层共享依赖：验权、账号/client 注册表。"""

from typing import Optional

from fastapi import HTTPException

from app.core.config import config_manager, CamelAccount
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


async def pick_ready_account(http):
    """每次请求只取一次账号，确保 cookie 就绪。失败抛 503。"""
    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no camel account configured"}})
    client = get_camel_client(account)
    if not await client.ensure_cookie(http):
        account.is_valid = False
        raise HTTPException(status_code=503, detail={"error": {"message": f"login failed for {account.email}"}})
    return account, client
