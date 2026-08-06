"""统一日志 — 替代裸 print，支持环境变量 CAMEL_LOG_LEVEL 控制级别 + 内存环形缓冲（/admin/logs 用）。"""

import logging
import os
import threading
from collections import deque

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_RING_CAPACITY = 300
_RING: deque = deque(maxlen=_RING_CAPACITY)
_RING_LOCK = threading.Lock()


class _RingBufferHandler(logging.Handler):
    """把格式化后的日志行保留在内存环形缓冲，供管理面板实时查看。"""

    def emit(self, record):
        try:
            msg = self.format(record)
            with _RING_LOCK:
                _RING.append(msg)
        except Exception:
            pass


def _configure():
    level = _LOG_LEVELS.get(os.environ.get("CAMEL_LOG_LEVEL", "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    # 控制台输出
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # 内存环形缓冲（避免重复挂载，例如热重载/重复导入）
    if not any(isinstance(h, _RingBufferHandler) for h in root.handlers):
        handler = _RingBufferHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(handler)


_configure()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_recent_logs(n: int = 200) -> list:
    """返回最近 n 条日志（按时间正序）。"""
    with _RING_LOCK:
        items = list(_RING)
    return items[-n:]
