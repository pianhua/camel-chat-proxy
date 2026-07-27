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
