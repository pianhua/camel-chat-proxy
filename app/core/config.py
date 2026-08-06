"""配置管理 — 线程安全，支持多账号、API Key、模型列表。"""

import json
import threading
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict, field

from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CamelAccount:
    email: str
    password: str
    login_time: str = ""
    last_test: str = ""
    is_valid: bool = False

    def to_dict(self):
        d = asdict(self)
        d["password_masked"] = "***"
        return d


@dataclass
class Config:
    api_keys: str = "sk-camel"
    admin_password: str = "admin"
    camel_accounts: List[CamelAccount] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    web_search: bool = False
    refresh_interval: int = 21600
    session_rotate_turns: int = 10  # 每个 CaMeL 会话最多承载的用户消息数
    message_mode: str = "auto"  # auto/native/compact：auto 按模型实测集合自动折叠
    model_contexts: dict = field(default_factory=dict)  # 模型上下文上限覆盖（OpenAI 格式 context_window）
    max_inflight_per_account: int = 1   # 账号池：每账号并发槽位（隐蔽优先默认 1）
    account_cooldown_seconds: int = 120  # 账号池：失败冷却秒数，到期自动恢复
    account_acquire_timeout: float = 30  # 账号池：无可用账号时排队等待秒数

    def to_dict(self):
        d = {
            "api_keys": self.api_keys,
            "admin_password": self.admin_password,
            "camel_accounts": [acc.to_dict() for acc in self.camel_accounts],
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
            "session_rotate_turns": self.session_rotate_turns,
            "message_mode": self.message_mode,
            "max_inflight_per_account": self.max_inflight_per_account,
            "account_cooldown_seconds": self.account_cooldown_seconds,
            "account_acquire_timeout": self.account_acquire_timeout,
        }
        if self.models:
            d["models"] = self.models
        if self.model_contexts:
            d["model_contexts"] = self.model_contexts
        return d

    def to_save_dict(self):
        d = {
            "api_keys": self.api_keys,
            "admin_password": self.admin_password,
            "camel_accounts": [
                {k: v for k, v in acc.to_dict().items() if k != "password_masked"}
                for acc in self.camel_accounts
            ],
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
            "session_rotate_turns": self.session_rotate_turns,
            "message_mode": self.message_mode,
            "max_inflight_per_account": self.max_inflight_per_account,
            "account_cooldown_seconds": self.account_cooldown_seconds,
            "account_acquire_timeout": self.account_acquire_timeout,
        }
        if self.models:
            d["models"] = self.models
        if self.model_contexts:
            d["model_contexts"] = self.model_contexts
        return d


class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self.config = Config()
        self.lock = threading.RLock()
        self.account_idx = 0
        self.load()

    def _parse(self, data: dict) -> Config:
        accounts = [
            CamelAccount(**{k: v for k, v in acc.items() if k in CamelAccount.__dataclass_fields__})
            for acc in data.get("camel_accounts", [])
        ]
        try:
            rotate = int(data.get("session_rotate_turns", 10))
        except (TypeError, ValueError):
            rotate = 10
        return Config(
            api_keys=data.get("api_keys", "sk-camel"),
            admin_password=data.get("admin_password", "admin"),
            camel_accounts=accounts,
            models=data.get("models", []),
            web_search=data.get("web_search", False),
            refresh_interval=data.get("refresh_interval", 21600),
            session_rotate_turns=rotate,
            message_mode=data.get("message_mode", "auto"),
            model_contexts=data.get("model_contexts", {}) or {},
            max_inflight_per_account=max(1, int(data.get("max_inflight_per_account", 1) or 1)),
            account_cooldown_seconds=max(0, int(data.get("account_cooldown_seconds", 120) or 0)),
            account_acquire_timeout=max(5, float(data.get("account_acquire_timeout", 30) or 30)),
        )

    def load(self):
        if not self.config_file.exists():
            self.save()
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                self.config = self._parse(json.load(f))
        except Exception as e:
            logger.error("加载配置失败: %s", e, exc_info=True)
            self.config = Config()
            self.save()

    def save(self):
        with self.lock:
            try:
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(self.config.to_save_dict(), f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error("保存配置失败: %s", e, exc_info=True)

    def validate_api_key(self, key: str) -> bool:
        if not key or not key.strip():
            return False
        with self.lock:
            return key.strip() in [k.strip() for k in self.config.api_keys.split(",") if k.strip()]

    def get_next_account(self, require_valid: bool = True) -> Optional[CamelAccount]:
        with self.lock:
            if not self.config.camel_accounts:
                return None
            total = len(self.config.camel_accounts)
            tried = 0
            while tried < total:
                account = self.config.camel_accounts[self.account_idx % total]
                self.account_idx += 1
                tried += 1
                if not require_valid or account.is_valid:
                    return account
            return self.config.camel_accounts[self.account_idx % total]

    def update_config(self, new_config: dict):
        with self.lock:
            merged = self.config.to_save_dict()
            merged.update(new_config)
            self.config = self._parse(merged)
            self.save()

    def get_config(self) -> dict:
        with self.lock:
            return self.config.to_dict()


config_manager = ConfigManager()
