"""API 端点与路由完整集成测试。"""

import json
import pytest
import httpx
from fastapi.testclient import TestClient

from main import app
from app.core.config import config_manager, Config, CamelAccount
from app.core.account_pool import account_pool
import app.api.deps as deps
import app.api.openai_routes as openai_routes


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """重置 config、account_pool 和 _clients。"""
    cfg = Config(
        api_keys="sk-camel,sk-test",
        admin_password="admin",
        camel_accounts=[
            CamelAccount(email="acc1@test.com", password="pw1", is_valid=True),
            CamelAccount(email="acc2@test.com", password="pw2", is_valid=True),
        ],
        web_search=False,
        session_rotate_turns=10,
    )
    monkeypatch.setattr(config_manager, "config", cfg)
    config_manager.account_idx = 0
    deps._clients.clear()
    openai_routes.invalidate_model_cache()


import time


@pytest.fixture
def mock_http(monkeypatch):
    def _setter(fake_handler):
        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_handler))
        async def mock_get_http(proxy=None):
            return mock_client
        monkeypatch.setattr("app.core.http.get_http_client", mock_get_http)
        monkeypatch.setattr("app.api.deps.get_http_client", mock_get_http)
        monkeypatch.setattr("app.api.openai_routes.get_http_client", mock_get_http)
        monkeypatch.setattr("app.api.anthropic_routes.get_http_client", mock_get_http)
        monkeypatch.setattr("app.api.admin_routes.get_http_client", mock_get_http)
        return mock_client
    return _setter


def test_auth_validation(mock_http):
    client = TestClient(app)
    # 无 key
    r = client.get("/v1/models")
    assert r.status_code == 401

    # 错误 key
    r = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
    assert r.status_code == 401

    # x-api-key 正确
    def fake_handler(req):
        if req.url.path == "/api/chat/models":
            return httpx.Response(200, json=[{"id": "claude-opus-4-7", "type": "chat"}])
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    r = client.get("/v1/models", headers={"x-api-key": "sk-camel"})
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert any(m["id"] == "claude-opus-4-7" for m in data["data"])


def test_openai_chat_completions(mock_http):
    client = TestClient(app)

    def fake_handler(req):
        if req.url.path == "/api/chat/sessions":
            return httpx.Response(201, json={"id": "sess-123"})
        if req.url.path == "/api/chat/completion":
            return httpx.Response(200, text="你好，我是 CaMeL！")
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    # 空消息报错
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-camel"},
                    json={"model": "claude-opus-4-7", "messages": []})
    assert r.status_code == 400

    # 正常请求
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-camel"},
                    json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "你好"}]})
    assert r.status_code == 200
    res = r.json()
    assert res["choices"][0]["message"]["content"] == "你好，我是 CaMeL！"
    assert res["usage"]["total_tokens"] > 0


def test_openai_chat_completions_stream(mock_http):
    client = TestClient(app)

    def fake_handler(req):
        if req.url.path == "/api/chat/sessions":
            return httpx.Response(201, json={"id": "sess-123"})
        if req.url.path == "/api/chat/completion":
            return httpx.Response(200, text="流式回答")
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-camel"},
                    json={"model": "claude-opus-4-7", "stream": True, "messages": [{"role": "user", "content": "你好"}]})
    assert r.status_code == 200
    assert "data: " in r.text
    assert "[DONE]" in r.text


def test_anthropic_messages(mock_http):
    client = TestClient(app)

    def fake_handler(req):
        if req.url.path == "/api/chat/sessions":
            return httpx.Response(201, json={"id": "sess-123"})
        if req.url.path == "/api/chat/completion":
            return httpx.Response(200, text="Claude 回答")
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    r = client.post("/v1/messages",
                    headers={"x-api-key": "sk-camel"},
                    json={"model": "claude-3-5-sonnet-20241022", "messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200
    res = r.json()
    assert res["type"] == "message"
    assert res["role"] == "assistant"
    assert res["content"][0]["text"] == "Claude 回答"


def test_anthropic_messages_stream(mock_http):
    client = TestClient(app)

    def fake_handler(req):
        if req.url.path == "/api/chat/sessions":
            return httpx.Response(201, json={"id": "sess-123"})
        if req.url.path == "/api/chat/completion":
            return httpx.Response(200, text="Claude 流式")
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    r = client.post("/v1/messages",
                    headers={"x-api-key": "sk-camel"},
                    json={"model": "claude-3-5-sonnet-20241022", "stream": True, "messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200
    assert "event: message_start" in r.text
    assert "event: message_stop" in r.text


def test_images_generations(mock_http):
    client = TestClient(app)

    def fake_handler(req):
        if req.url.path == "/api/chat/sessions":
            return httpx.Response(201, json={"id": "sess-123"})
        if req.url.path == "/api/chat/image":
            return httpx.Response(200, json={"images": [{"id": 0, "url": "data:image/png;base64,QUJD"}]})
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    r = client.post("/v1/images/generations",
                    headers={"Authorization": "Bearer sk-camel"},
                    json={"prompt": ""})
    assert r.status_code == 400

    r = client.post("/v1/images/generations",
                    headers={"Authorization": "Bearer sk-camel"},
                    json={"prompt": "draw a cat"})
    assert r.status_code == 200
    res = r.json()
    assert len(res["data"]) == 1
    assert res["data"][0]["b64_json"] == "QUJD"


def test_admin_routes():
    client = TestClient(app)

    r = client.get("/admin/config")
    assert r.status_code == 401

    r = client.get("/admin/config", headers={"x-admin-password": "wrong"})
    assert r.status_code == 403

    r = client.get("/admin/config", headers={"x-admin-password": "admin"})
    assert r.status_code == 200
    assert r.json()["api_keys"] == "sk-camel,sk-test"

    r = client.post("/admin/config",
                    headers={"x-admin-password": "admin"},
                    json={"session_rotate_turns": 15})
    assert r.status_code == 200
    assert r.json()["config"]["session_rotate_turns"] == 15

    r = client.get("/admin/logs", headers={"x-admin-password": "admin"})
    assert r.status_code == 200
    assert isinstance(r.json()["logs"], list)

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    r = client.get("/")
    assert r.status_code == 200
    assert "CaMeL Chat Proxy" in r.text


async def test_pick_ready_account_releases_slot_on_login_failure(monkeypatch):
    """验证登录失败时 slot 被正常释放，不会导致并发槽位永久泄漏。"""
    cfg = Config(
        camel_accounts=[CamelAccount(email="fail@test.com", password="pw", is_valid=False)],
        max_inflight_per_account=1,
        account_cooldown_seconds=0,
    )
    monkeypatch.setattr(config_manager, "config", cfg)
    config_manager.account_idx = 0
    deps._clients.clear()

    class FailingClient:
        email = "fail@test.com"
        async def ensure_cookie(self, http):
            return False

    monkeypatch.setattr(deps, "get_camel_client", lambda acc: FailingClient())

    # 第一次调用抛 503
    with pytest.raises(Exception):
        await deps.pick_ready_account(None)

    # 验证 _in_use 被清零，而不是停留在 1
    assert account_pool._in_use.get("fail@test.com", 0) == 0


def test_openai_retry_on_500(mock_http):
    """验证上游 500 错误时自动换号重试。"""
    client = TestClient(app)
    attempt_emails = []

    def fake_handler(req):
        if req.url.path == "/api/chat/sessions":
            return httpx.Response(201, json={"id": "sess-123"})
        if req.url.path == "/api/chat/completion":
            cookie = req.headers.get("cookie", "")
            if "acc1@test.com" in cookie:
                return httpx.Response(500, text="Internal Server Error")
            return httpx.Response(200, text="由 acc2 成功回答")
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        # 用 email 放在 cookie 里方便 handler 区分
        c.cookie = f"cookie-for-{acc.email}"
        c.cookie_time = time.time()

    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-camel"},
                    json={"model": "claude-opus-4-7", "messages": [{"role": "user", "content": "你好"}]})
    assert r.status_code == 200
    res = r.json()
    assert res["choices"][0]["message"]["content"] == "由 acc2 成功回答"
    # 验证两个账号的占用槽位都已释放
    assert account_pool._in_use.get("acc1@test.com", 0) == 0
    assert account_pool._in_use.get("acc2@test.com", 0) == 0


def test_admin_actions(mock_http):
    client = TestClient(app)

    def fake_handler(req):
        if req.url.path == "/api/chat/models":
            return httpx.Response(200, json=[{"id": "claude-opus-4-7", "type": "chat"}])
        if req.url.path == "/api/chat/usage":
            return httpx.Response(200, json={"tokens": 100})
        if req.url.path == "/api/chat/search":
            return httpx.Response(200, json={"used": 0, "limit": 5, "remaining": 5})
        return httpx.Response(404)

    mock_http(fake_handler)

    for acc in config_manager.config.camel_accounts:
        c = deps.get_camel_client(acc)
        c.cookie = "fake-cookie"
        c.cookie_time = time.time()

    # 刷新模型
    r = client.post("/admin/refresh-models", headers={"x-admin-password": "admin"})
    assert r.status_code == 200
    assert r.json()["model_count"] >= 1

    # 查看用量
    r = client.get("/admin/usage", headers={"x-admin-password": "admin"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert len(r.json()["accounts"]) == 2

    # 测试全部账号
    r = client.post("/admin/test-accounts", headers={"x-admin-password": "admin"})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is True

    # 获取模型列表（管理面板用）
    r = client.get("/admin/models", headers={"x-admin-password": "admin"})
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1


