import json
from app.core.config import ConfigManager


def test_rotate_turns_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cm = ConfigManager("config.json")
    assert cm.config.session_rotate_turns == 10


def test_rotate_turns_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"session_rotate_turns": 5}), encoding="utf-8")
    cm = ConfigManager("config.json")
    assert cm.config.session_rotate_turns == 5
    cm.update_config({"session_rotate_turns": 20})
    assert cm.config.session_rotate_turns == 20
    assert cm.config.api_keys == "sk-camel"


def test_account_rotation_skips_invalid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "camel_accounts": [
            {"email": "a@x.com", "password": "p", "is_valid": False},
            {"email": "b@x.com", "password": "p", "is_valid": True},
        ]
    }), encoding="utf-8")
    cm = ConfigManager("config.json")
    assert cm.get_next_account().email == "b@x.com"


def test_validate_api_key_rejects_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"api_keys": "sk-a,"}), encoding="utf-8")
    cm = ConfigManager("config.json")
    assert cm.validate_api_key("") is False
    assert cm.validate_api_key("sk-a") is True


def test_corrupt_rotate_turns_does_not_reset_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "session_rotate_turns": "not-a-number",
        "camel_accounts": [{"email": "a@x.com", "password": "p"}],
    }), encoding="utf-8")
    cm = ConfigManager("config.json")
    assert cm.config.session_rotate_turns == 10
    assert len(cm.config.camel_accounts) == 1  # 账号不丢


def test_env_var_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAMEL_API_KEYS", "sk-from-env,sk-env-2")
    monkeypatch.setenv("CAMEL_ADMIN_PASSWORD", "env-admin-pass")
    monkeypatch.setenv("CAMEL_ACCOUNTS", "env1@x.com:p1:sess1234567890,env2@x.com:p2")
    monkeypatch.setenv("CAMEL_WEB_SEARCH", "true")
    monkeypatch.setenv("CAMEL_MAX_INFLIGHT_PER_ACCOUNT", "2")

    cm = ConfigManager("config.json")
    assert cm.config.api_keys == "sk-from-env,sk-env-2"
    assert cm.config.admin_password == "env-admin-pass"
    assert cm.config.web_search is True
    assert cm.config.max_inflight_per_account == 2
    assert len(cm.config.camel_accounts) == 2
    assert cm.config.camel_accounts[0].email == "env1@x.com"
    assert cm.config.camel_accounts[0].cookie == "sess1234567890"
    assert cm.config.camel_accounts[0].is_valid is True  # 带 cookie 自动置为 True


def test_account_dict_cookie_masking():
    from app.core.config import CamelAccount
    acc = CamelAccount(email="test@x.com", password="secretpassword", cookie="camel_session_very_long_secret_12345")
    d = acc.to_dict()
    assert d["password_masked"] == "***"
    assert "password" in d
    assert d["cookie_masked"].startswith("camel_...")
    assert d["cookie_masked"].endswith("12345")

