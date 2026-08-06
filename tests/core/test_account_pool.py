"""账号池单元测试：并发槽位 / 冷却 / 轮询 / 释放。"""

import asyncio

import pytest

from app.core.account_pool import AccountPool
from app.core.config import config_manager, Config, CamelAccount


@pytest.fixture
def pool(monkeypatch):
    p = AccountPool()
    cfg = Config()
    cfg.camel_accounts = [
        CamelAccount(email="a@test.com", password="p", is_valid=True),
        CamelAccount(email="b@test.com", password="p", is_valid=True),
    ]
    cfg.max_inflight_per_account = 1
    cfg.account_cooldown_seconds = 60
    cfg.account_acquire_timeout = 1  # 测试用短超时
    monkeypatch.setattr(config_manager, "config", cfg)
    config_manager.account_idx = 0
    return p


async def test_acquire_round_robin(pool):
    a1 = await pool.acquire()
    a2 = await pool.acquire()
    assert a1 is not None and a2 is not None
    assert a1.email != a2.email  # 轮询应切到另一个账号
    await pool.release(a1)
    await pool.release(a2)


async def test_max_inflight_waits_then_times_out(pool):
    a1 = await pool.acquire()
    a2 = await pool.acquire()
    # 两个账号槽位都被占用，第三个 acquire 应排队并超时返回 None
    got = await pool.acquire()
    assert got is None
    await pool.release(a1)
    await pool.release(a2)


async def test_release_frees_slot(pool):
    a1 = await pool.acquire()
    a2 = await pool.acquire()
    await pool.release(a1)
    # 释放后能再次拿到 a1
    a3 = await pool.acquire()
    assert a3 is not None and a3.email == a1.email
    await pool.release(a2)
    await pool.release(a3)


async def test_cooldown_skips_failed_account(pool):
    a1 = await pool.acquire()
    await pool.mark_failed(a1)
    await pool.release(a1)
    # 冷却中的 a1 应被跳过，只能拿到 a2
    a2 = await pool.acquire()
    assert a2 is not None and a2.email == "b@test.com"
    await pool.release(a2)


async def test_exclude_skips_account(pool):
    a1 = await pool.acquire()
    await pool.release(a1)
    a2 = await pool.acquire(exclude={"a@test.com"})
    assert a2 is not None and a2.email == "b@test.com"
    await pool.release(a2)


async def test_all_cooling_returns_none(pool):
    a1 = await pool.acquire()
    a2 = await pool.acquire()
    await pool.mark_failed(a1)
    await pool.mark_failed(a2)
    await pool.release(a1)
    await pool.release(a2)
    got = await pool.acquire()
    assert got is None  # 全部冷却 → 超时返回 None
