"""账号池 — 并发槽位 + 排队等待 + 失败冷却。

参考 ds2api 的 account pool 设计，保持简单可靠、以隐蔽优先：
- 每账号并发槽位上限（config.max_inflight_per_account，默认 1）
- 无可用槽位时排队等待（config.account_acquire_timeout 秒，默认 30），超时返回 None
- 失败账号进入冷却（config.account_cooldown_seconds，默认 120），到期自动恢复，
  不再需要手动重新登录；is_valid 仅用于面板展示
- 轮询顺序沿用 ConfigManager 的 round-robin（account_idx）

用法：
    account = await account_pool.acquire(exclude={"a@b.com"})
    if not account:  # 超时/无账号
        ...
    try:
        ... 使用 account ...
    except SomeError:
        await account_pool.mark_failed(account)
    finally:
        await account_pool.release(account)
"""

import asyncio
import time
from typing import Optional, Set

from .config import config_manager, CamelAccount


class AccountPool:
    def __init__(self):
        self._in_use: dict[str, int] = {}            # email -> 当前占用数
        self._cooldown_until: dict[str, float] = {}  # email -> 冷却截止时间戳
        self._lock_obj: Optional[asyncio.Lock] = None
        self._wake_obj: Optional[asyncio.Event] = None

    def _get_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock_obj is None or (getattr(self._lock_obj, "_loop", None) is not None and self._lock_obj._loop is not loop):
            self._lock_obj = asyncio.Lock()
        return self._lock_obj

    def _get_wake(self) -> asyncio.Event:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._wake_obj is None or (getattr(self._wake_obj, "_loop", None) is not None and self._wake_obj._loop is not loop):
            self._wake_obj = asyncio.Event()
            self._wake_obj.set()
        return self._wake_obj

    # ---- 配置读取（每次实时读，支持面板改配置后立即生效）----
    def _max_inflight(self) -> int:
        return max(1, int(getattr(config_manager.config, "max_inflight_per_account", 1) or 1))

    def _cooldown_seconds(self) -> float:
        return max(0, float(getattr(config_manager.config, "account_cooldown_seconds", 120) or 0))

    def _acquire_timeout(self) -> float:
        return max(5, float(getattr(config_manager.config, "account_acquire_timeout", 30) or 30))

    def _is_cooling(self, email: str) -> bool:
        return self._cooldown_until.get(email, 0) > time.time()

    # ---- 对外接口 ----
    async def acquire(self, exclude: Optional[Set[str]] = None) -> Optional[CamelAccount]:
        """排队获取一个可用账号（非冷却、槽位未满）。返回账号；超时返回 None。"""
        exclude = exclude or set()
        deadline = time.monotonic() + self._acquire_timeout()
        while True:
            async with self._get_lock():
                acc = self._try_acquire_locked(exclude)
                if acc is not None:
                    return acc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._wait_for_change(), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                pass

    async def release(self, account: CamelAccount):
        async with self._get_lock():
            email = account.email
            self._in_use[email] = max(0, self._in_use.get(email, 0) - 1)
            self._get_wake().set()

    async def mark_failed(self, account: CamelAccount):
        async with self._get_lock():
            email = account.email
            account.is_valid = False
            if self._cooldown_seconds() > 0:
                self._cooldown_until[email] = time.time() + self._cooldown_seconds()
            self._get_wake().set()

    async def mark_ok(self, account: CamelAccount):
        async with self._get_lock():
            email = account.email
            account.is_valid = True
            self._cooldown_until.pop(email, None)
            self._get_wake().set()

    # ---- 内部 ----
    def _try_acquire_locked(self, exclude: Set[str]) -> Optional[CamelAccount]:
        max_inflight = self._max_inflight()
        accounts = config_manager.config.camel_accounts
        if not accounts:
            return None
        total = len(accounts)
        tried = 0
        while tried < total:
            account = accounts[config_manager.account_idx % total]
            config_manager.account_idx += 1
            tried += 1
            email = account.email
            if not getattr(account, "enabled", True):
                continue
            if email in exclude:
                continue
            if self._is_cooling(email):
                continue
            if self._in_use.get(email, 0) >= max_inflight:
                continue
            self._in_use[email] = self._in_use.get(email, 0) + 1
            return account
        return None

    async def _wait_for_change(self):
        wake = self._get_wake()
        wake.clear()
        await wake.wait()


account_pool = AccountPool()
