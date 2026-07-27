import sys

import pytest
from app.camel import login


async def test_login_returns_none_on_playwright_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    result = await login.playwright_login("a@b.com", "pw")
    assert result is None


def test_camel_base():
    assert login.CAMEL_BASE == "https://chat.camel-hub.com"
