"""CaMeL 会话池 — 每账号 1 活跃会话，N 句轮换，旧会话遗弃（隐蔽优先）。"""

import asyncio


class SessionPool:
    def __init__(self, rotate_turns: int = 10):
        self.rotate_turns = rotate_turns
        # email -> {"session_id": str, "turns": int, "title_synced": bool}
        self._active: dict[str, dict] = {}
        self._lock_obj: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock_obj is None or (getattr(self._lock_obj, "_loop", None) is not None and self._lock_obj._loop is not loop):
            self._lock_obj = asyncio.Lock()
        return self._lock_obj

    async def get_session(self, http, client, model: str, email: str) -> str:
        """取当前活跃会话；没有则创建。模型切换无需换会话（payload 自带 model）。"""
        async with self._get_lock():
            entry = self._active.get(email)
            if entry is None:
                entry = await self._new_session(http, client, model, email)
            return entry["session_id"]

    def record_turn(self, email: str):
        """每发一句用户消息计数；达上限后作废，下次 get_session 自动换新。"""
        entry = self._active.get(email)
        if entry is None:
            return
        entry["turns"] += 1
        if entry["turns"] >= self.rotate_turns:
            del self._active[email]  # 遗弃，不删除（模拟真人堆积）

    async def sync_title(self, http, client, email: str, first_user_text: str):
        """每会话仅同步一次标题（取首句前 20 字，模拟真人行为）。"""
        entry = self._active.get(email)
        if entry is None or entry["title_synced"]:
            return
        entry["title_synced"] = True
        title = (first_user_text or "新对话").strip()[:20] or "新对话"
        await client.update_session_title(http, entry["session_id"], title)

    async def _new_session(self, http, client, model: str, email: str) -> dict:
        session_id = await client.create_session(http, "新对话", model)
        entry = {"session_id": session_id, "turns": 0, "title_synced": False}
        self._active[email] = entry
        return entry


session_pool = SessionPool()
