"""验证 refresh / ensure 后的新 cookie 自动持久化落盘至 account.cookie 与 config.json。"""

import time
import pytest
from app.core.config import config_manager, CamelAccount, Config
from app.api import deps
from app.api.deps import pick_ready_account, get_camel_client
from app.core.account_pool import account_pool


@pytest.mark.asyncio
async def test_ensure_cookie_persists_to_account_and_config(monkeypatch):
    """验证 pick_ready_account 中 ensure_cookie 拿到新 cookie 后，写回 account.cookie 并保存。"""
    acc = CamelAccount(email="refresh_test@example.com", password="pwd", cookie="old-cookie-123", is_valid=True)
    cfg = Config(camel_accounts=[acc])
    monkeypatch.setattr(config_manager, "config", cfg)
    deps._clients.clear()

    saved_calls = 0
    def mock_save():
        nonlocal saved_calls
        saved_calls += 1
    monkeypatch.setattr(config_manager, "save", mock_save)

    client = get_camel_client(acc)

    # 模拟 ensure_cookie 刷新得到了新 cookie
    async def mock_ensure(http):
        client.cookie = "new-refreshed-cookie-456"
        client.cookie_time = time.time()
        return True
    monkeypatch.setattr(client, "ensure_cookie", mock_ensure)

    account, c = await pick_ready_account()
    try:
        assert account.cookie == "new-refreshed-cookie-456"
        assert c.cookie == "new-refreshed-cookie-456"
        assert saved_calls >= 1

        # 再次调用 get_camel_client，不应被旧 cookie 覆盖
        c2 = get_camel_client(account)
        assert c2.cookie == "new-refreshed-cookie-456"
    finally:
        await account_pool.release(account)
