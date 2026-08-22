"""配置管理 — 线程安全，支持多账号、API Key、模型列表。"""

import json
import threading
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict, field

from app.core.logger import get_logger

logger = get_logger(__name__)


import os


@dataclass
class CamelAccount:
    email: str
    password: str = ""
    cookie: str = ""
    proxy: str = ""  # 独立 HTTP/SOCKS5 代理（如 socks5://127.0.0.1:10801）
    mihomo_node: str = ""  # 绑定的 Mihomo 节点名
    enabled: bool = True  # 账号启停开关
    login_time: str = ""
    last_test: str = ""
    is_valid: bool = False

    def to_dict(self):
        d = asdict(self)
        d["password_masked"] = "***" if self.password else ""
        if self.cookie:
            d["cookie_masked"] = self.cookie[:6] + "..." + self.cookie[-6:] if len(self.cookie) > 12 else "***"
        return d


@dataclass
class Config:
    api_keys: str = "sk-camel"
    admin_password: str = "admin"
    camel_accounts: List[CamelAccount] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    model_aliases: dict = field(default_factory=lambda: {
        "gpt-4o": "claude-opus-4-7",
        "gpt-4o-mini": "claude-haiku-4-5",
        "gpt-5": "gpt-5.5",
        "claude-3-7-sonnet": "claude-sonnet-5",
        "claude-3-5-sonnet": "claude-sonnet-4-6",
        "claude-3-5-haiku": "claude-haiku-4-5",
        "deepseek-r1": "deepseek-v4-pro",
        "deepseek-v3": "deepseek-v3.2"
    })
    web_search: bool = False
    refresh_interval: int = 21600
    session_rotate_turns: int = 10  # 每个 CaMeL 会话最多承载的用户消息数
    message_mode: str = "native"  # 默认 native：保持原生多轮数组；compact：折叠文本
    model_contexts: dict = field(default_factory=dict)  # 模型上下文上限覆盖（OpenAI 格式 context_window）
    max_inflight_per_account: int = 1   # 账号池：每账号并发槽位（隐蔽优先默认 1）
    account_cooldown_seconds: int = 120  # 账号池：失败冷却秒数，到期自动恢复
    account_acquire_timeout: float = 30  # 账号池：无可用账号时排队等待秒数
    mihomo: dict = field(default_factory=lambda: {
        "enabled": False,
        "binary_path": "",
        "base_port": 10801,
        "api_port": 19090,
        "auto_bind": False
    })

    def to_dict(self):
        d = {
            "api_keys": self.api_keys,
            "admin_password": self.admin_password,
            "camel_accounts": [acc.to_dict() for acc in self.camel_accounts],
            "model_aliases": self.model_aliases,
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
            "session_rotate_turns": self.session_rotate_turns,
            "message_mode": self.message_mode,
            "max_inflight_per_account": self.max_inflight_per_account,
            "account_cooldown_seconds": self.account_cooldown_seconds,
            "account_acquire_timeout": self.account_acquire_timeout,
            "mihomo": self.mihomo,
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
                {k: v for k, v in acc.to_dict().items() if k not in ("password_masked", "cookie_masked")}
                for acc in self.camel_accounts
            ],
            "model_aliases": self.model_aliases,
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
            "session_rotate_turns": self.session_rotate_turns,
            "message_mode": self.message_mode,
            "max_inflight_per_account": self.max_inflight_per_account,
            "account_cooldown_seconds": self.account_cooldown_seconds,
            "account_acquire_timeout": self.account_acquire_timeout,
            "mihomo": self.mihomo,
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

    def _apply_env_overrides(self, data: dict) -> dict:
        """从环境变量读取配置并覆盖合并。"""
        out = dict(data)
        env_keys = os.environ.get("CAMEL_API_KEYS") or os.environ.get("API_KEYS")
        if env_keys:
            out["api_keys"] = env_keys

        env_pwd = os.environ.get("CAMEL_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD")
        if env_pwd:
            out["admin_password"] = env_pwd

        env_accounts = os.environ.get("CAMEL_ACCOUNTS")
        if env_accounts and env_accounts.strip():
            env_accounts = env_accounts.strip()
            if env_accounts.startswith("["):
                try:
                    out["camel_accounts"] = json.loads(env_accounts)
                except Exception as e:
                    logger.warning("[Config] Failed to parse CAMEL_ACCOUNTS JSON: %s", e)
            else:
                parsed_accs = []
                for pair in env_accounts.split(","):
                    parts = pair.strip().split(":")
                    if len(parts) >= 2:
                        acc = {"email": parts[0], "password": parts[1]}
                        if len(parts) >= 3:
                            acc["cookie"] = parts[2]
                        parsed_accs.append(acc)
                if parsed_accs:
                    out["camel_accounts"] = parsed_accs

        if "CAMEL_WEB_SEARCH" in os.environ:
            out["web_search"] = os.environ["CAMEL_WEB_SEARCH"].lower() in ("1", "true", "yes")

        if "CAMEL_REFRESH_INTERVAL" in os.environ:
            try:
                out["refresh_interval"] = int(os.environ["CAMEL_REFRESH_INTERVAL"])
            except ValueError:
                pass

        if "CAMEL_SESSION_ROTATE_TURNS" in os.environ:
            try:
                out["session_rotate_turns"] = int(os.environ["CAMEL_SESSION_ROTATE_TURNS"])
            except ValueError:
                pass

        if "CAMEL_MESSAGE_MODE" in os.environ:
            out["message_mode"] = os.environ["CAMEL_MESSAGE_MODE"]

        if "CAMEL_MAX_INFLIGHT_PER_ACCOUNT" in os.environ:
            try:
                out["max_inflight_per_account"] = int(os.environ["CAMEL_MAX_INFLIGHT_PER_ACCOUNT"])
            except ValueError:
                pass

        if "CAMEL_ACCOUNT_COOLDOWN_SECONDS" in os.environ:
            try:
                out["account_cooldown_seconds"] = int(os.environ["CAMEL_ACCOUNT_COOLDOWN_SECONDS"])
            except ValueError:
                pass

        if "CAMEL_ACCOUNT_ACQUIRE_TIMEOUT" in os.environ:
            try:
                out["account_acquire_timeout"] = float(os.environ["CAMEL_ACCOUNT_ACQUIRE_TIMEOUT"])
            except ValueError:
                pass

        return out

    def _parse(self, data: dict) -> Config:
        data = self._apply_env_overrides(data)
        accounts = []
        for acc in data.get("camel_accounts", []):
            acc_kwargs = {k: v for k, v in acc.items() if k in CamelAccount.__dataclass_fields__}
            if acc_kwargs.get("cookie") and not acc_kwargs.get("is_valid"):
                acc_kwargs["is_valid"] = True
            accounts.append(CamelAccount(**acc_kwargs))

        try:
            rotate = int(data.get("session_rotate_turns", 10))
        except (TypeError, ValueError):
            rotate = 10

        model_aliases = data.get("model_aliases")
        if model_aliases is None:
            model_aliases = {
                "gpt-4o": "claude-opus-4-7",
                "gpt-4o-mini": "claude-haiku-4-5",
                "gpt-5": "gpt-5.5",
                "claude-3-7-sonnet": "claude-sonnet-5",
                "claude-3-5-sonnet": "claude-sonnet-4-6",
                "claude-3-5-haiku": "claude-haiku-4-5",
                "deepseek-r1": "deepseek-v4-pro",
                "deepseek-v3": "deepseek-v3.2"
            }

        return Config(
            api_keys=data.get("api_keys", "sk-camel"),
            admin_password=data.get("admin_password", "admin"),
            camel_accounts=accounts,
            models=data.get("models", []),
            model_aliases=model_aliases,
            web_search=data.get("web_search", False),
            refresh_interval=data.get("refresh_interval", 21600),
            session_rotate_turns=rotate,
            message_mode=data.get("message_mode", "native"),
            model_contexts=data.get("model_contexts", {}) or {},
            max_inflight_per_account=max(1, int(data.get("max_inflight_per_account", 1) or 1)),
            account_cooldown_seconds=max(0, int(data.get("account_cooldown_seconds", 120) or 0)),
            account_acquire_timeout=max(5, float(data.get("account_acquire_timeout", 30) or 30)),
            mihomo=data.get("mihomo", {}) or {},
        )

    def load(self):
        if not self.config_file.exists():
            self.config = self._parse({})
            self.save()
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                self.config = self._parse(json.load(f))
        except Exception as e:
            logger.error("加载配置失败: %s", e, exc_info=True)
            self.config = self._parse({})
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

    def resolve_model(self, model: str) -> str:
        """解析模型别名。若有别名映射则返回真实模型，否则原样返回。"""
        if not model:
            return model
        with self.lock:
            aliases = self.config.model_aliases or {}
            if model in aliases:
                return aliases[model]
            m_lower = model.lower()
            for k, v in aliases.items():
                if k.lower() == m_lower:
                    return v
            return model

    def get_next_account(self, require_valid: bool = True) -> Optional[CamelAccount]:
        with self.lock:
            active_accounts = [a for a in self.config.camel_accounts if getattr(a, "enabled", True)]
            if not active_accounts:
                return None
            total = len(active_accounts)
            tried = 0
            while tried < total:
                account = active_accounts[self.account_idx % total]
                self.account_idx += 1
                tried += 1
                if not require_valid or account.is_valid:
                    return account
            return active_accounts[self.account_idx % total]

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
