import sys

import pytest
from app.camel import login


async def test_login_returns_none_on_playwright_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    result = await login.playwright_login("a@b.com", "pw")
    assert result is None


def test_camel_base():
    assert login.CAMEL_BASE == "https://chat.camel-hub.com"


async def test_login_skips_when_no_password():
    res = await login.playwright_login("user@test.com", "")
    assert res is None


async def test_login_retries_and_succeeds(monkeypatch):
    calls = 0
    async def mock_login_once(email, password):
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("Temporary network timeout")
        return "sess-ok"

    monkeypatch.setattr(login, "_playwright_login_once", mock_login_once)
    result = await login.playwright_login("a@b.com", "pw", max_retries=2)
    assert result == "sess-ok"
    assert calls == 2

