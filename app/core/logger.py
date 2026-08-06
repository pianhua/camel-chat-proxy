"""统一日志 — 替代裸 print，支持环境变量 CAMEL_LOG_LEVEL 控制级别。"""

import logging
import os

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _configure():
    level = _LOG_LEVELS.get(os.environ.get("CAMEL_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


_configure()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
