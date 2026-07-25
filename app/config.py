"""配置管理 — 线程安全，支持多账号、API Key、模型列表。"""

import json
import threading
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict, field


@dataclass
class CamelAccount:
    """CaMeL 账号配置。"""
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
    """应用配置。"""
    api_keys: str = "sk-camel"
    admin_password: str = "admin"
    camel_accounts: List[CamelAccount] = field(default_factory=list)
    models: List[str] = field(default_factory=list)  # 空=自动发现
    web_search: bool = False  # 全局联网搜索开关
    refresh_interval: int = 21600  # Cookie 刷新间隔（秒），默认6小时

    def __post_init__(self):
        if not self.camel_accounts:
            self.camel_accounts = []

    def to_dict(self):
        d = {
            "api_keys": self.api_keys,
            "admin_password": self.admin_password,
            "camel_accounts": [acc.to_dict() for acc in self.camel_accounts],
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
        }
        if self.models:
            d["models"] = self.models
        return d

    def to_save_dict(self):
        """保存到文件时，不含 password_masked。"""
        d = {
            "api_keys": self.api_keys,
            "admin_password": self.admin_password,
            "camel_accounts": [
                {k: v for k, v in acc.to_dict().items() if k != "password_masked"}
                for acc in self.camel_accounts
            ],
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
        }
        if self.models:
            d["models"] = self.models
        return d


class ConfigManager:
    """配置管理器 — 线程安全。"""

    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self.config = Config()
        self.lock = threading.RLock()
        self.account_idx = 0
        self.load()

    def load(self):
        if not self.config_file.exists():
            self.save()
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts = [
                CamelAccount(**{k: v for k, v in acc.items() if k in CamelAccount.__dataclass_fields__})
                for acc in data.get("camel_accounts", [])
            ]
            self.config = Config(
                api_keys=data.get("api_keys", "sk-camel"),
                admin_password=data.get("admin_password", "admin"),
                camel_accounts=accounts,
                models=data.get("models", []),
                web_search=data.get("web_search", False),
                refresh_interval=data.get("refresh_interval", 21600),
            )
        except Exception as e:
            print(f"加载配置失败: {e}")
            self.config = Config()
            self.save()

    def save(self):
        with self.lock:
            try:
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(self.config.to_save_dict(), f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"保存配置失败: {e}")

    def validate_api_key(self, key: str) -> bool:
        with self.lock:
            keys = [k.strip() for k in self.config.api_keys.split(",")]
            return key in keys

    def get_next_account(self) -> Optional[CamelAccount]:
        """轮询获取下一个账号。"""
        with self.lock:
            if not self.config.camel_accounts:
                return None
            account = self.config.camel_accounts[self.account_idx % len(self.config.camel_accounts)]
            self.account_idx += 1
            return account

    def update_config(self, new_config: dict):
        with self.lock:
            accounts = [
                CamelAccount(**{k: v for k, v in acc.items() if k in CamelAccount.__dataclass_fields__})
                for acc in new_config.get("camel_accounts", [])
            ]
            self.config = Config(
                api_keys=new_config.get("api_keys", "sk-camel"),
                admin_password=new_config.get("admin_password", "admin"),
                camel_accounts=accounts,
                models=new_config.get("models", []),
                web_search=new_config.get("web_search", False),
                refresh_interval=new_config.get("refresh_interval", 21600),
            )
            self.save()

    def get_config(self) -> dict:
        with self.lock:
            return self.config.to_dict()


# 全局实例
config_manager = ConfigManager()
