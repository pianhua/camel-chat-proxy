"""会话管理 — 跟踪 CaMeL 会话，避免越积越多，自动清理过期会话。"""

import time
import threading
from typing import Optional


class SessionStore:
    """会话存储：按 (account_email, model) 缓存 session_id，定时过期。"""

    def __init__(self, ttl: int = 3600):
        """ttl: 会话有效期（秒），默认1小时。"""
        self._ttl = ttl
        self._sessions: dict[str, dict] = {}  # key -> {session_id, created_at}
        self._lock = threading.Lock()

    def _make_key(self, email: str, model: str) -> str:
        return f"{email}::{model}"

    def get(self, email: str, model: str) -> Optional[str]:
        """获取缓存的会话ID（未过期）。"""
        key = self._make_key(email, model)
        with self._lock:
            entry = self._sessions.get(key)
            if entry and (time.time() - entry["created_at"]) < self._ttl:
                return entry["session_id"]
            # 过期则删除
            if entry:
                del self._sessions[key]
            return None

    def set(self, email: str, model: str, session_id: str):
        """缓存会话ID。"""
        key = self._make_key(email, model)
        with self._lock:
            self._sessions[key] = {
                "session_id": session_id,
                "created_at": time.time(),
            }

    def get_all_expired(self) -> list:
        """获取所有过期的会话条目（email, session_id），用于清理CaMeL服务端。"""
        now = time.time()
        expired = []
        with self._lock:
            expired_keys = []
            for key, entry in list(self._sessions.items()):
                if (now - entry["created_at"]) > self._ttl:
                    email = key.split("::")[0]
                    expired.append((email, entry["session_id"]))
                    expired_keys.append(key)
            for key in expired_keys:
                del self._sessions[key]
        return expired

    def size(self) -> int:
        with self._lock:
            return len(self._sessions)


# 全局实例
session_store = SessionStore(ttl=3600)
