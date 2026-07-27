# CaMeL Chat Proxy 全面重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-07-27-camel-proxy-overhaul-design.md` 全面重构代理：修复全部协议 bug、拆分为 api/convert/camel/core 四层、新增用量监控等新功能、重写暖米色 WebUI。

**Architecture:** 单向依赖 `api → convert → camel → core`。camel 层只懂 CaMeL 协议；convert 层做 OpenAI/Anthropic ↔ CaMeL 转换；api 层是 HTTP 薄层。会话池 10 句轮换，隐蔽优先。

**Tech Stack:** Python 3.10+ / FastAPI / httpx / Playwright / pytest

**参照文档:** API 协议以 `C:\Users\10697\.gemini\antigravity\brain\544c23be-ff94-4036-8a65-c9eb98350f1e\camel_chat_api_documentation.md` 为准。

---

### Task 0: 环境准备

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 添加 pytest 依赖**

在 `requirements.txt` 末尾追加：

```
pytest>=8.0
pytest-asyncio>=0.23
```

文件顶部加一行配置注释不需要；另创建 `pytest.ini`：

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 2: 安装**

Run: `pip install pytest pytest-asyncio`
Expected: 安装成功

- [ ] **Step 3: Commit**

```bash
git add requirements.txt pytest.ini
git commit -m "chore: add pytest for refactor test suite"
```

---

### Task 1: core 层（config / auth / http）

**Files:**
- Create: `app/core/__init__.py`（空文件）
- Create: `app/core/config.py`（由 `app/config.py` 迁入并扩展）
- Create: `app/core/auth.py`（由 `app/auth.py` 迁入）
- Create: `app/core/http.py`
- Delete: `app/config.py`、`app/auth.py`（在 Task 11 统一删除，先建新的）

- [ ] **Step 1: 写 `app/core/config.py`**

在旧 `Config` dataclass 基础上新增 `session_rotate_turns` 字段，其余原样迁移：

```python
"""配置管理 — 线程安全，支持多账号、API Key、模型列表。"""

import json
import threading
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict, field


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

    def to_dict(self):
        d = {
            "api_keys": self.api_keys,
            "admin_password": self.admin_password,
            "camel_accounts": [acc.to_dict() for acc in self.camel_accounts],
            "web_search": self.web_search,
            "refresh_interval": self.refresh_interval,
            "session_rotate_turns": self.session_rotate_turns,
        }
        if self.models:
            d["models"] = self.models
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
        }
        if self.models:
            d["models"] = self.models
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
        return Config(
            api_keys=data.get("api_keys", "sk-camel"),
            admin_password=data.get("admin_password", "admin"),
            camel_accounts=accounts,
            models=data.get("models", []),
            web_search=data.get("web_search", False),
            refresh_interval=data.get("refresh_interval", 21600),
            session_rotate_turns=int(data.get("session_rotate_turns", 10)),
        )

    def load(self):
        if not self.config_file.exists():
            self.save()
            return
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                self.config = self._parse(json.load(f))
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
            return key in [k.strip() for k in self.config.api_keys.split(",")]

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
            merged = self.to_save_dict()
            merged.update(new_config)
            self.config = self._parse(merged)
            self.save()

    def get_config(self) -> dict:
        with self.lock:
            return self.config.to_dict()


config_manager = ConfigManager()
```

注意：`update_config` 改为**合并式**更新（旧版整体覆盖会丢掉未提交字段）。

- [ ] **Step 2: 写 `app/core/auth.py`**

```python
"""管理面板认证。"""

from fastapi import Header, HTTPException
from .config import config_manager


def verify_admin(x_admin_password: str = Header(None, alias="x-admin-password")) -> bool:
    if not x_admin_password:
        raise HTTPException(status_code=401, detail="Admin password required (x-admin-password header)")
    if x_admin_password != config_manager.config.admin_password:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    return True
```

- [ ] **Step 3: 写 `app/core/http.py`**

```python
"""共享 httpx 异步客户端。"""

from typing import Optional
import httpx

_client: Optional[httpx.AsyncClient] = None


async def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=30.0),
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30),
        )
    return _client


async def close_http_client():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
```

- [ ] **Step 4: 写失败测试 `tests/core/test_config.py`**

```python
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
    assert cm.config.api_keys == "sk-camel"  # 合并更新不丢字段


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
```

- [ ] **Step 5: 跑测试**

Run: `python -m pytest tests/core -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add app/core tests/core
git commit -m "feat(core): split config/auth/http into core layer, add session_rotate_turns"
```

---

### Task 2: camel/login.py（Playwright 登录）

**Files:**
- Create: `app/camel/__init__.py`（空文件）
- Create: `app/camel/login.py`
- Test: `tests/camel/test_login.py`

- [ ] **Step 1: 写 `app/camel/login.py`**

从旧 `camel_client.refresh_cookie` 抽出，返回 cookie 字符串：

```python
"""CaMeL Playwright 自动登录。"""

from typing import Optional

CAMEL_BASE = "https://chat.camel-hub.com"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


async def playwright_login(email: str, password: str) -> Optional[str]:
    """用 Playwright 登录 CaMeL，返回 camel_session cookie 值；失败返回 None。"""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(user_agent=_UA, locale="zh-CN", viewport={"width": 1920, "height": 1080})
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                delete navigator.__proto__.webdriver;
            """)

            print(f"[Login] Navigating to {CAMEL_BASE}/login ...")
            await page.goto(f"{CAMEL_BASE}/login", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('input[name="email"]', timeout=10000)
            await page.fill('input[name="email"]', email)
            await page.fill('input[name="password"]', password)
            print(f"[Login] Submitting for {email} ...")
            async with page.expect_navigation(wait_until="networkidle", timeout=30000):
                await page.click('button[type="submit"]')
            await page.wait_for_timeout(2000)

            cookies = await context.cookies()
            print(f"[Login] Got {len(cookies)} cookies: {[c['name'] for c in cookies]}")
            cookie_value = next((c["value"] for c in cookies if c["name"] == "camel_session"), None)
            await browser.close()
            if cookie_value:
                print(f"[Login] OK Cookie refreshed for {email}")
            else:
                print(f"[Login] FAIL camel_session cookie not found for {email}")
            return cookie_value
    except Exception as e:
        print(f"[Login] FAIL Error: {e}")
        import traceback
        traceback.print_exc()
        return None
```

- [ ] **Step 2: 写测试 `tests/camel/test_login.py`**（不真登录，只验证模块可导入、异常返回 None）

```python
import pytest
from app.camel import login


async def test_login_returns_none_on_playwright_failure(monkeypatch):
    def _boom():
        raise RuntimeError("no playwright")
    monkeypatch.setitem(__import__("sys").modules, "playwright.async_api", None)
    result = await login.playwright_login("a@b.com", "pw")
    assert result is None


def test_camel_base():
    assert login.CAMEL_BASE == "https://chat.camel-hub.com"
```

- [ ] **Step 3: 跑测试**

Run: `python -m pytest tests/camel -v`
Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add app/camel tests/camel
git commit -m "feat(camel): extract playwright login into camel.login"
```

---

### Task 3: camel/client.py（纯上游客户端，含 UUID 修复 + image 端点 + usage/search）

**Files:**
- Create: `app/camel/client.py`
- Test: `tests/camel/test_client.py`

- [ ] **Step 1: 写失败测试 `tests/camel/test_client.py`**

用 httpx.MockTransport 模拟上游，验证端点、payload、UUID 格式：

```python
import re
import httpx
import pytest
from app.camel.client import CamelClient, CamelAPIError

UUID36 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    c = CamelClient(email="a@b.com")
    c.cookie = "sess"
    return c, http


async def test_completion_uses_uuid36_and_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        import json as j
        seen["body"] = j.loads(request.content)
        return httpx.Response(200, text="hello")

    c, http = make_client(handler)
    text = await c.chat_completion(http, {"messages": [{"role": "user", "content": "hi"}], "model": "m",
                                         "sessionId": "s", "userMessageId": "u", "assistantMessageId": "a",
                                         "userText": "hi", "hasAttachments": False,
                                         "hasCustomSystemPrompt": False, "webSearch": False})
    assert text == "hello"
    assert seen["url"] == "https://chat.camel-hub.com/api/chat/completion"
    await http.aclose()


async def test_generate_image_endpoint_and_parse():
    def handler(request):
        import json as j
        body = j.loads(request.content)
        assert str(request.url) == "https://chat.camel-hub.com/api/chat/image"
        assert body["size"] == "1:1"
        assert body["priorImages"] == []
        assert UUID36.match(body["sessionId"])
        return httpx.Response(200, json={"images": [{"id": 0, "url": "data:image/png;base64,QUJD"}]})

    c, http = make_client(handler)
    images = await c.generate_image(http, prompt="a cat", model="gpt-image-2", size="1:1", n=1, prior_images=[])
    assert images == ["data:image/png;base64,QUJD"]
    await http.aclose()


async def test_usage_and_search_endpoints():
    def handler(request):
        if request.url.path == "/api/chat/usage":
            return httpx.Response(200, json={"tokens": 100})
        if request.url.path == "/api/chat/search":
            return httpx.Response(200, json={"used": 0, "limit": 5, "remaining": 5})
        return httpx.Response(404)

    c, http = make_client(handler)
    assert (await c.get_usage(http))["tokens"] == 100
    assert (await c.get_search_limits(http))["remaining"] == 5
    await http.aclose()


async def test_error_raises_camel_api_error():
    def handler(request):
        return httpx.Response(401, text="unauthorized")

    c, http = make_client(handler)
    with pytest.raises(CamelAPIError) as e:
        await c.get_usage(http)
    assert e.value.status_code == 401
    await http.aclose()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/camel/test_client.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 `app/camel/client.py`**

```python
"""CaMeL 上游 API 纯客户端 — 只懂 CaMeL 协议，不懂 OpenAI/Anthropic。"""

import time
import uuid
from typing import Optional, AsyncIterator

import httpx

from .login import playwright_login, CAMEL_BASE, _UA


class CamelAPIError(Exception):
    def __init__(self, status_code: int, response_body: str):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"CaMeL API error {status_code}: {response_body[:200]}")


def new_uuid() -> str:
    """CaMeL 要求 36 位标准 UUID。"""
    return str(uuid.uuid4())


class CamelClient:
    """单账号 CaMeL 客户端：Cookie 生命周期 + 全部上游端点。"""

    def __init__(self, email: str, password: str = ""):
        self.email = email
        self.password = password
        self.cookie: Optional[str] = None
        self.cookie_time: float = 0

    # ── 基础 ──────────────────────────────────────────────

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "user-agent": _UA,
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "origin": CAMEL_BASE,
            "referer": f"{CAMEL_BASE}/chat",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if extra:
            h.update(extra)
        return h

    def _cookies(self) -> dict:
        if not self.cookie:
            raise CamelAPIError(503, "Cookie not ready")
        return {"camel_session": self.cookie, "camel_lang": "zh-CN"}

    async def ensure_cookie(self, http: httpx.AsyncClient) -> bool:
        if self.cookie and (time.time() - self.cookie_time) < 21600:
            return True
        return await self.refresh_cookie()

    async def refresh_cookie(self) -> bool:
        cookie = await playwright_login(self.email, self.password)
        if cookie:
            self.cookie = cookie
            self.cookie_time = time.time()
            return True
        return False

    @staticmethod
    def _check(r: httpx.Response, ok: tuple = (200,)):
        if r.status_code not in ok:
            raise CamelAPIError(r.status_code, r.text)

    # ── 会话 ──────────────────────────────────────────────

    async def create_session(self, http: httpx.AsyncClient, title: str, model: str) -> str:
        r = await http.post(
            f"{CAMEL_BASE}/api/chat/sessions",
            headers=self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json={"title": title[:64], "model": model},
        )
        self._check(r, (201,))
        return r.json()["id"]

    async def update_session_title(self, http: httpx.AsyncClient, session_id: str, title: str) -> bool:
        try:
            r = await http.put(
                f"{CAMEL_BASE}/api/chat/sessions/{session_id}",
                headers=self._headers({"content-type": "application/json"}),
                cookies=self._cookies(),
                json={"title": title[:64]},
                timeout=15.0,
            )
            return r.status_code == 200
        except Exception:
            return False

    # ── 对话 ──────────────────────────────────────────────

    async def chat_completion(self, http: httpx.AsyncClient, payload: dict) -> str:
        r = await http.post(
            f"{CAMEL_BASE}/api/chat/completion",
            headers=self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json=payload,
        )
        self._check(r)
        return r.text

    async def stream_chat(self, http: httpx.AsyncClient, payload: dict) -> AsyncIterator[str]:
        async with http.stream(
            "POST",
            f"{CAMEL_BASE}/api/chat/completion",
            headers=self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json=payload,
        ) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise CamelAPIError(r.status_code, body.decode(errors="replace"))
            async for chunk in r.aiter_text():
                if chunk:
                    yield chunk

    # ── 生图 ──────────────────────────────────────────────

    async def generate_image(
        self,
        http: httpx.AsyncClient,
        prompt: str,
        model: str,
        size: str = "auto",
        n: int = 1,
        prior_images: list | None = None,
        session_id: str | None = None,
    ) -> list:
        """调 /api/chat/image，返回 data URL 列表。"""
        r = await http.post(
            f"{CAMEL_BASE}/api/chat/image",
            headers=self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json={
                "prompt": prompt,
                "model": model,
                "providerId": "CaMeL",
                "sessionId": session_id or new_uuid(),
                "size": size,
                "n": n,
                "priorImages": prior_images or [],
            },
            timeout=300.0,
        )
        self._check(r)
        return [img["url"] for img in r.json().get("images", []) if img.get("url")]

    # ── 文件解析 ──────────────────────────────────────────

    async def parse_file(self, http: httpx.AsyncClient, file_name: str, file_content: bytes, mime_type: str) -> dict:
        boundary = f"----CaMeLFile{uuid.uuid4().hex[:12]}"
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f'Content-Type: {mime_type}\r\n\r\n'
        ).encode() + file_content + f'\r\n--{boundary}--\r\n'.encode()
        r = await http.post(
            f"{CAMEL_BASE}/api/chat/parse-file",
            headers=self._headers({"content-type": f"multipart/form-data; boundary={boundary}"}),
            cookies=self._cookies(),
            content=body,
            timeout=60.0,
        )
        self._check(r)
        return r.json()

    # ── 元数据 ────────────────────────────────────────────

    async def get_models(self, http: httpx.AsyncClient) -> list:
        r = await http.get(f"{CAMEL_BASE}/api/chat/models", headers=self._headers(), cookies=self._cookies())
        self._check(r)
        return r.json()

    async def get_usage(self, http: httpx.AsyncClient) -> dict:
        r = await http.get(f"{CAMEL_BASE}/api/chat/usage", headers=self._headers(), cookies=self._cookies())
        self._check(r)
        return r.json()

    async def get_search_limits(self, http: httpx.AsyncClient) -> dict:
        r = await http.get(f"{CAMEL_BASE}/api/chat/search", headers=self._headers(), cookies=self._cookies())
        self._check(r)
        return r.json()

    async def test_connection(self, http: httpx.AsyncClient) -> bool:
        try:
            return len(await self.get_models(http)) > 0
        except Exception:
            return False
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/camel/test_client.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/camel/client.py tests/camel/test_client.py
git commit -m "feat(camel): pure upstream client with uuid36, image endpoint, usage/search"
```

---

### Task 4: camel/session_pool.py（10 句轮换 + 标题同步）

**Files:**
- Create: `app/camel/session_pool.py`
- Test: `tests/camel/test_session_pool.py`

- [ ] **Step 1: 写失败测试 `tests/camel/test_session_pool.py`**

```python
import pytest
from app.camel.session_pool import SessionPool


class FakeClient:
    def __init__(self):
        self.created = []
        self.titles = []

    async def create_session(self, http, title, model):
        sid = f"sess-{len(self.created)}"
        self.created.append((title, model))
        return sid

    async def update_session_title(self, http, session_id, title):
        self.titles.append((session_id, title))
        return True


async def test_session_reuse_and_rotation():
    pool = SessionPool(rotate_turns=3)
    client = FakeClient()

    s1 = await pool.get_session(None, client, "claude-opus-4-7", "acc@x.com")
    assert s1 == "sess-0"
    # 3 句内复用
    for _ in range(2):
        pool.record_turn("acc@x.com")
        assert await pool.get_session(None, client, "claude-opus-4-7", "acc@x.com") == "sess-0"
    # 第 3 句到达上限 → 轮换
    pool.record_turn("acc@x.com")
    s2 = await pool.get_session(None, client, "claude-opus-4-7", "acc@x.com")
    assert s2 == "sess-1"
    # 旧会话遗弃不删除（FakeClient 无 delete 调用即证明）


async def test_title_sync_only_once():
    pool = SessionPool(rotate_turns=10)
    client = FakeClient()
    await pool.get_session(None, client, "m", "acc@x.com")
    await pool.sync_title(None, client, "acc@x.com", "你好世界" * 30)
    await pool.sync_title(None, client, "acc@x.com", "第二次不应生效")
    assert len(client.titles) == 1
    assert len(client.titles[0][1]) == 20  # 标题截断 20 字


async def test_independent_accounts():
    pool = SessionPool(rotate_turns=10)
    client = FakeClient()
    sa = await pool.get_session(None, client, "m", "a@x.com")
    sb = await pool.get_session(None, client, "m", "b@x.com")
    assert sa != sb
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/camel/test_session_pool.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 `app/camel/session_pool.py`**

```python
"""CaMeL 会话池 — 每账号 1 活跃会话，N 句轮换，旧会话遗弃（隐蔽优先）。"""

import asyncio


class SessionPool:
    def __init__(self, rotate_turns: int = 10):
        self.rotate_turns = rotate_turns
        # email -> {"session_id": str, "turns": int, "title_synced": bool}
        self._active: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def get_session(self, http, client, model: str, email: str) -> str:
        """取当前活跃会话；没有则创建。模型切换无需换会话（payload 自带 model）。"""
        async with self._lock:
            entry = self._active.get(email)
            if entry is None:
                entry = await self._new_session(http, client, model, email)
            return entry["session_id"]

    def record_turn(self, email: str):
        """每发一句用户消息计数；达上限后作废，下次 get_session 自动换新。"""
        entry = self._active.get(email)
        if entry is None:
            return
        entry["turns"] += 1
        if entry["turns"] >= self.rotate_turns:
            del self._active[email]  # 遗弃，不删除（模拟真人堆积）

    async def sync_title(self, http, client, email: str, first_user_text: str):
        """每会话仅同步一次标题（取首句前 20 字，模拟真人行为）。"""
        entry = self._active.get(email)
        if entry is None or entry["title_synced"]:
            return
        entry["title_synced"] = True
        title = (first_user_text or "新对话").strip()[:20] or "新对话"
        await client.update_session_title(http, entry["session_id"], title)

    async def _new_session(self, http, client, model: str, email: str) -> dict:
        session_id = await client.create_session(http, "新对话", model)
        entry = {"session_id": session_id, "turns": 0, "title_synced": False}
        self._active[email] = entry
        return entry


session_pool = SessionPool()
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/camel/test_session_pool.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/camel/session_pool.py tests/camel/test_session_pool.py
git commit -m "feat(camel): session pool with N-turn rotation and one-time title sync"
```

---

### Task 5: convert/attachments.py（统一附件管线）

**Files:**
- Create: `app/convert/__init__.py`（空文件）
- Create: `app/convert/attachments.py`
- Test: `tests/convert/test_attachments.py`

- [ ] **Step 1: 写失败测试 `tests/convert/test_attachments.py`**

```python
import base64
import httpx
import pytest
from app.convert.attachments import resolve_image_to_data_url, resolve_file_to_text, is_image_mime


async def test_data_url_passthrough():
    url = "data:image/png;base64,QUJD"
    assert await resolve_image_to_data_url(None, url) == url


async def test_http_image_download():
    def handler(request):
        return httpx.Response(200, content=b"ABC", headers={"content-type": "image/png"})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await resolve_image_to_data_url(http, "https://x.com/a.png")
    assert result == "data:image/png;base64," + base64.b64encode(b"ABC").decode()
    await http.aclose()


async def test_local_image_read(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"XYZ")
    result = await resolve_image_to_data_url(None, str(p))
    assert result == "data:image/png;base64," + base64.b64encode(b"XYZ").decode()


async def test_remote_text_file_via_parse_file():
    def handler(request):
        if "example.com" in str(request.url):
            return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})
        return httpx.Response(200, json={"text": "hello", "type": "text", "fileName": "a.txt", "fileSize": 5})

    class FakeCamel:
        async def parse_file(self, http, name, content, mime):
            return {"text": content.decode(), "type": "text"}

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    text = await resolve_file_to_text(http, FakeCamel(), "https://example.com/a.txt")
    assert text == "hello"
    await http.aclose()


def test_is_image_mime():
    assert is_image_mime("image/png")
    assert not is_image_mime("text/plain")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/convert -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写 `app/convert/attachments.py`**

```python
"""统一附件管线：图片/文件 → CaMeL 可用的 data URL 或文本。"""

import base64
import os
import re
from typing import Optional

import httpx

_MIME_BY_EXT = {
    ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
    ".py": "text/x-python", ".js": "text/javascript", ".html": "text/html",
    ".css": "text/css", ".csv": "text/csv", ".xml": "text/xml",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".pdf": "application/pdf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def is_image_mime(mime: str) -> bool:
    return (mime or "").lower().startswith("image/")


def _is_local_path(url: str) -> bool:
    return (
        url.startswith("file:///")
        or bool(re.match(r"^[A-Za-z]:[/\\]", url))
        or (url.startswith("/") and not url.startswith("//"))
    )


def _read_local(path: str) -> tuple[str, bytes, str] | None:
    """返回 (文件名, 内容, mime)；失败 None。"""
    if path.startswith("file:///"):
        path = path[8:]
    path = os.path.normpath(path)
    if not os.path.exists(path):
        print(f"[Attach] Local file not found: {path}")
        return None
    name = os.path.basename(path) or "file"
    mime = _MIME_BY_EXT.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
    with open(path, "rb") as f:
        return name, f.read(), mime


async def _read_remote(http: httpx.AsyncClient, url: str) -> tuple[str, bytes, str] | None:
    try:
        r = await http.get(url, timeout=30.0, follow_redirects=True)
        if r.status_code != 200:
            return None
        name = url.split("/")[-1].split("?")[0] or "file"
        mime = r.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        return name, r.content, mime
    except Exception as e:
        print(f"[Attach] Download failed {url}: {e}")
        return None


async def resolve_image_to_data_url(http: Optional[httpx.AsyncClient], url: str) -> Optional[str]:
    """http URL / data URL / 本地路径 → data:image/...;base64,..."""
    if url.startswith("data:"):
        return url
    got = _read_local(url) if _is_local_path(url) else (await _read_remote(http, url) if http else None)
    if not got:
        return None
    name, content, mime = got
    if not is_image_mime(mime):
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


async def resolve_file_to_text(http, camel, url: str) -> Optional[str]:
    """文件 → 经 /api/chat/parse-file 解析为文本。图片返回 None（走 resolve_image_to_data_url）。"""
    got = _read_local(url) if _is_local_path(url) else await _read_remote(http, url)
    if not got:
        return None
    name, content, mime = got
    if is_image_mime(mime):
        return None
    parsed = await camel.parse_file(http, name, content, mime)
    return parsed.get("text") or None
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/convert -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add app/convert tests/convert
git commit -m "feat(convert): unified attachment pipeline (data url / parse-file)"
```

---

### Task 6: convert/openai_conv.py（OpenAI → CaMeL payload）

**Files:**
- Create: `app/convert/openai_conv.py`
- Test: `tests/convert/test_openai_conv.py`

- [ ] **Step 1: 写失败测试 `tests/convert/test_openai_conv.py`**

```python
import re
import pytest
from app.convert.openai_conv import build_payload, extract_system, OPENAI_SIZE_TO_CAMEL

UUID36 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


async def test_plain_text_payload():
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]
    payload = await build_payload(None, None, messages, model="claude-opus-4-7", session_id="816b589c-7f86-4a93-9450-96ac6845cd3f")
    assert payload["model"] == "claude-opus-4-7"
    assert payload["providerId"] == "CaMeL"
    assert payload["hasAttachments"] is False
    assert payload["hasCustomSystemPrompt"] is True
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    assert payload["messages"][1] == {"role": "user", "content": "你好"}
    assert payload["userText"] == "你好"
    assert UUID36.match(payload["userMessageId"])
    assert UUID36.match(payload["assistantMessageId"])
    # 原列表不被污染
    assert messages[1]["content"] == "你好"


async def test_vision_payload_sets_attachments():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "描述这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ],
    }]
    payload = await build_payload(None, None, messages, model="m", session_id="816b589c-7f86-4a93-9450-96ac6845cd3f")
    assert payload["hasAttachments"] is True
    content = payload["messages"][0]["content"]
    assert "描述这张图" in content
    assert "![本地图片](data:image/png;base64,QUJD)" in content
    assert payload["userText"] == "描述这张图"


async def test_multiline_join_uses_real_newline():
    messages = [{
        "role": "user",
        "content": [{"type": "text", "text": "第一行"}, {"type": "text", "text": "第二行"}],
    }]
    payload = await build_payload(None, None, messages, model="m", session_id="816b589c-7f86-4a93-9450-96ac6845cd3f")
    assert payload["messages"][0]["content"] == "第一行\n第二行"


def test_size_mapping():
    assert OPENAI_SIZE_TO_CAMEL["1024x1024"] == "1:1"
    assert OPENAI_SIZE_TO_CAMEL["1792x1024"] == "16:9"
    assert OPENAI_SIZE_TO_CAMEL["1024x1792"] == "9:16"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/convert/test_openai_conv.py -v`
Expected: FAIL

- [ ] **Step 3: 写 `app/convert/openai_conv.py`**

```python
"""OpenAI 消息格式 → CaMeL payload 转换。"""

import re
from typing import Optional

from app.camel.client import new_uuid
from .attachments import resolve_image_to_data_url, resolve_file_to_text

OPENAI_SIZE_TO_CAMEL = {
    "1024x1024": "1:1",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
}

_MD_IMAGE_RE = re.compile(r"!\[.*?\]\((https?://[^\)]+)\)")
_FILE_RE = re.compile(r"\[📎.*?\]\(([^\)]+)\)")


def extract_system(messages: list) -> str:
    parts = []
    for m in messages:
        if m.get("role") == "system":
            c = m.get("content", "")
            parts.append(c if isinstance(c, str) else " ".join(
                i.get("text", "") for i in c if isinstance(i, dict) and i.get("type") == "text"
            ))
    return "\n".join(p for p in parts if p)


async def _convert_one(http, camel, msg: dict) -> tuple[dict, bool]:
    """单条 user/assistant 消息 → CaMeL 格式。返回 (消息, 是否含附件)。不修改入参。"""
    role = msg["role"]
    content = msg.get("content", "")
    attachments: list[str] = []  # data URL 图片

    if isinstance(content, list):
        texts, images, files = [], [], []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text":
                texts.append(item.get("text", ""))
            elif t == "image_url":
                u = (item.get("image_url") or {}).get("url", "")
                if u:
                    images.append(u)
            elif t == "file_url":
                u = (item.get("file_url") or {}).get("url", "")
                if u:
                    files.append(u)
        text = "\n".join(t for t in texts if t)
    else:
        text = content
        images = _MD_IMAGE_RE.findall(text) if role == "user" else []
        files = _FILE_RE.findall(text) if role == "user" else []

    for u in images:
        data_url = await resolve_image_to_data_url(http, u)
        if data_url:
            attachments.append(data_url)
    file_texts = []
    for u in files:
        ft = await resolve_file_to_text(http, camel, u)
        if ft:
            file_texts.append((u.split("/")[-1].split("?")[0] or "file", ft))
        else:
            data_url = await resolve_image_to_data_url(http, u)
            if data_url:
                attachments.append(data_url)

    if attachments:
        for i, du in enumerate(attachments):
            text += f"\n[file: image-{i + 1}.png]"
        text += "\n" + "\n".join(f"![本地图片]({du})" for du in attachments)
    for name, ft in file_texts:
        text += f"\n\n[文件内容: {name}]\n{ft}"

    return {"role": role, "content": text}, bool(attachments or file_texts)


async def build_payload(http, camel, messages: list, model: str, session_id: str, web_search: bool = False) -> dict:
    """OpenAI messages → CaMeL completion payload（完整新对象，不污染入参）。"""
    system = extract_system(messages)
    convo, has_attach, user_text = [], False, ""
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        converted, had = await _convert_one(http, camel, m)
        convo.append(converted)
        has_attach = has_attach or had
        if converted["role"] == "user":
            user_text = converted["content"]
    if system:
        convo.insert(0, {"role": "system", "content": system})

    return {
        "messages": convo,
        "model": model,
        "providerId": "CaMeL",
        "sessionId": session_id,
        "userMessageId": new_uuid(),
        "assistantMessageId": new_uuid(),
        "userText": user_text,
        "hasAttachments": has_attach,
        "hasCustomSystemPrompt": bool(system),
        "webSearch": web_search,
    }
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/convert/test_openai_conv.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add app/convert/openai_conv.py tests/convert/test_openai_conv.py
git commit -m "feat(convert): openai messages -> camel payload with vision support"
```

---

### Task 7: convert/anthropic_conv.py（Anthropic blocks → CaMeL）

**Files:**
- Create: `app/convert/anthropic_conv.py`
- Test: `tests/convert/test_anthropic_conv.py`

- [ ] **Step 1: 写失败测试 `tests/convert/test_anthropic_conv.py`**

```python
import pytest
from app.convert.anthropic_conv import to_openai_messages, to_camel_model


def test_model_mapping():
    assert to_camel_model("claude-sonnet-4-20250514") == "claude-sonnet-4-6"
    assert to_camel_model("claude-opus-4-7") == "claude-opus-4-7"


def test_string_content():
    msgs = to_openai_messages([{"role": "user", "content": "hi"}], system="sys")
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_block_content_to_openai_parts():
    body_msg = {"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
    ]}
    msgs = to_openai_messages([body_msg])
    content = msgs[0]["content"]
    assert content[0] == {"type": "text", "text": "看图"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/convert/test_anthropic_conv.py -v`
Expected: FAIL

- [ ] **Step 3: 写 `app/convert/anthropic_conv.py`**

```python
"""Anthropic Messages 格式 → OpenAI 中间格式（再经 openai_conv 转 CaMeL）。"""

ANTHROPIC_MODEL_MAP = {
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
    "claude-opus-4-20250514": "claude-opus-4-7",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-5": "claude-sonnet-4-6",
    "claude-opus-4-5": "claude-opus-4-7",
    "claude-opus-4-1": "claude-opus-4-7",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5",
}


def to_camel_model(model: str) -> str:
    return ANTHROPIC_MODEL_MAP.get(model, model)


def _blocks_to_openai(content) -> object:
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif t == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
            else:
                url = src.get("url", "")
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts if parts else ""


def to_openai_messages(messages: list, system: str = "") -> list:
    out = []
    if system:
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
        out.append({"role": "system", "content": system})
    for m in messages:
        out.append({"role": m.get("role", "user"), "content": _blocks_to_openai(m.get("content", ""))})
    return out
```

- [ ] **Step 4: 跑测试**

Run: `python -m pytest tests/convert/test_anthropic_conv.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add app/convert/anthropic_conv.py tests/convert/test_anthropic_conv.py
git commit -m "feat(convert): anthropic blocks -> openai intermediate format"
```

---

### Task 8: api/deps.py + api/openai_routes.py

**Files:**
- Create: `app/api/__init__.py`（空文件）
- Create: `app/api/deps.py`
- Create: `app/api/openai_routes.py`

- [ ] **Step 1: 写 `app/api/deps.py`**

```python
"""API 层共享依赖：验权、账号/client 注册表。"""

from typing import Optional

from fastapi import HTTPException

from app.core.config import config_manager, CamelAccount
from app.camel.client import CamelClient, CamelAPIError

_clients: dict[str, CamelClient] = {}


def get_camel_client(account: CamelAccount) -> CamelClient:
    if account.email not in _clients:
        _clients[account.email] = CamelClient(account.email, account.password)
    c = _clients[account.email]
    c.password = account.password  # 密码可能在面板里更新过
    return c


def check_api_key(authorization: Optional[str], x_api_key: Optional[str]):
    key = None
    if authorization:
        key = authorization.replace("Bearer ", "").strip()
    elif x_api_key:
        key = x_api_key.strip()
    if not key or not config_manager.validate_api_key(key):
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key"}})


async def pick_ready_account(http):
    """每次请求只取一次账号，确保 cookie 就绪。失败抛 503。"""
    account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no camel account configured"}})
    client = get_camel_client(account)
    if not await client.ensure_cookie(http):
        account.is_valid = False
        raise HTTPException(status_code=503, detail={"error": {"message": f"login failed for {account.email}"}})
    return account, client
```

- [ ] **Step 2: 写 `app/api/openai_routes.py`**

```python
"""OpenAI 兼容端点 — HTTP 薄层。"""

import base64
import json
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse

from app.core.config import config_manager
from app.core.http import get_http_client
from app.camel.client import CamelAPIError
from app.camel.session_pool import session_pool
from app.convert.openai_conv import build_payload, OPENAI_SIZE_TO_CAMEL
from app.convert.attachments import resolve_image_to_data_url
from .deps import check_api_key, pick_ready_account, get_camel_client

router = APIRouter()

_model_cache: Optional[list] = None
_model_cache_time: float = 0
MODEL_CACHE_TTL = 3600

IMAGE_MODEL_MAP = {
    "dall-e-3": "gpt-image-2",
    "dall-e-2": "gpt-image-2",
    "gpt-image-2": "gpt-image-2",
    "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",
}

IMAGE_MODELS = {"gpt-image-2", "gemini-3-pro-image-preview"}


async def discover_models(http) -> list:
    global _model_cache, _model_cache_time
    now = time.time()
    if _model_cache and (now - _model_cache_time) < MODEL_CACHE_TTL:
        return _model_cache
    account = config_manager.get_next_account()
    if not account:
        return _model_cache or []
    client = get_camel_client(account)
    if not await client.ensure_cookie(http):
        return _model_cache or []
    try:
        raw = await client.get_models(http)
        result = [
            {"id": m["id"], "object": "model", "created": 0, "owned_by": m.get("providerId", "CaMeL")}
            for m in raw if m.get("type") in ("chat", "image")
        ]
        _model_cache, _model_cache_time = result, now
        print(f"[Models] Discovered {len(result)} models")
        return result
    except Exception as e:
        print(f"[Models] Discovery failed: {e}")
        if _model_cache:
            return _model_cache
        raise


def invalidate_model_cache():
    global _model_cache, _model_cache_time
    _model_cache, _model_cache_time = None, 0


@router.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    check_api_key(authorization, x_api_key)
    http = await get_http_client()
    return {"object": "list", "data": await discover_models(http)}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    check_api_key(request.headers.get("authorization"), request.headers.get("x-api-key"))
    body = await request.json()
    model = body.get("model", "claude-opus-4-7")
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    if not messages:
        raise HTTPException(status_code=400, detail={"error": {"message": "messages required"}})

    http = await get_http_client()
    account, camel = await pick_ready_account(http)
    session_id = await session_pool.get_session(http, camel, model, account.email)
    web_search = bool(body.get("web_search", config_manager.config.web_search))

    try:
        payload = await build_payload(http, camel, messages, model, session_id, web_search)
    except CamelAPIError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})

    session_pool.record_turn(account.email)
    request_id = str(uuid.uuid4())

    async def _after_first_reply():
        first_user = next((m for m in payload["messages"] if m["role"] == "user"), None)
        if first_user:
            await session_pool.sync_title(http, camel, account.email, first_user["content"])

    if stream:
        async def _stream():
            try:
                first = True
                async for chunk in camel.stream_chat(http, payload):
                    if first:
                        first = False
                        await _after_first_reply()
                    data = {
                        "id": f"chatcmpl-{request_id}", "object": "chat.completion.chunk",
                        "created": 0, "model": model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except CamelAPIError as e:
                yield f"data: {json.dumps({'error': {'message': f'CaMeL API error: {e}'}})}\n\n"
                yield "data: [DONE]\n\n"
        return StreamingResponse(_stream(), media_type="text/event-stream")

    try:
        text = await camel.chat_completion(http, payload)
        await _after_first_reply()
    except CamelAPIError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})

    return {
        "id": f"chatcmpl-{request_id}", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    check_api_key(request.headers.get("authorization"), request.headers.get("x-api-key"))
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": {"message": "prompt is required"}})

    model = IMAGE_MODEL_MAP.get(body.get("model", "gpt-image-2"), "gpt-image-2")
    n = min(int(body.get("n", 1)), 4)
    size = OPENAI_SIZE_TO_CAMEL.get(body.get("size", "1024x1024"), "auto")

    http = await get_http_client()
    # 参考图（图生图）：接受 base64 data URL / http URL / 本地路径
    prior_images = []
    for item in body.get("prior_images", []) or []:
        du = await resolve_image_to_data_url(http, item)
        if du:
            prior_images.append(du)

    account, camel = await pick_ready_account(http)
    try:
        urls = await camel.generate_image(http, prompt=prompt, model=model, size=size, n=n, prior_images=prior_images)
    except CamelAPIError as e:
        raise HTTPException(status_code=e.status_code, detail={"error": {"message": str(e)}})

    data = []
    for du in urls:
        m = re.match(r"data:image/\w+;base64,(.+)", du, re.S)
        data.append({"b64_json": m.group(1) if m else base64.b64encode(du.encode()).decode()})
    return {"created": int(time.time()), "data": data}
```

- [ ] **Step 3: 冒烟测试（import 不炸）**

Run: `python -c "from app.api.openai_routes import router; print('ok')"`
Expected: `ok`

注意：此时旧 `app/routes.py` 仍存在且被 main.py 引用，冒烟可能连锁失败；若失败，先跳到 Task 11 完成 main.py 切换后回头验证。顺序可换：建议直接继续 Task 9-11 后统一验证。

- [ ] **Step 4: Commit**

```bash
git add app/api
git commit -m "feat(api): openai routes thin layer (models/chat/images)"
```

---

### Task 9: api/anthropic_routes.py

**Files:**
- Create: `app/api/anthropic_routes.py`（替换旧 `app/anthropic_routes.py`）

- [ ] **Step 1: 写文件**

```python
"""Anthropic Messages API — HTTP 薄层。"""

import json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import StreamingResponse

from app.core.http import get_http_client
from app.camel.client import CamelAPIError
from app.camel.session_pool import session_pool
from app.convert.anthropic_conv import to_camel_model, to_openai_messages
from app.convert.openai_conv import build_payload
from .deps import check_api_key, pick_ready_account

router = APIRouter()

_ERR = lambda t, m: {"error": {"type": t, "message": m}}


@router.post("/v1/messages")
async def anthropic_messages(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key"),
                             authorization: Optional[str] = Header(None)):
    try:
        check_api_key(authorization, x_api_key)
    except HTTPException:
        raise HTTPException(status_code=401, detail=_ERR("authentication_error", "invalid api key"))

    body = await request.json()
    model = to_camel_model(body.get("model", "claude-sonnet-4-6"))
    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail=_ERR("invalid_request_error", "messages required"))
    stream = bool(body.get("stream", False))
    web_search = bool(body.get("web_search", False))

    openai_messages = to_openai_messages(messages, body.get("system", ""))

    http = await get_http_client()
    try:
        account, camel = await pick_ready_account(http)
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=_ERR("service_unavailable", str(e.detail)))

    session_id = await session_pool.get_session(http, camel, model, account.email)
    payload = await build_payload(http, camel, openai_messages, model, session_id, web_search)
    session_pool.record_turn(account.email)
    msg_id = str(uuid.uuid4())

    if stream:
        async def _stream_sse():
            try:
                async for chunk in camel.stream_chat(http, payload):
                    event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}}
                    yield f"event: content_block_delta\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            except CamelAPIError as e:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(e)}})}\n\n"
        return StreamingResponse(_stream_sse(), media_type="text/event-stream")

    try:
        text = await camel.chat_completion(http, payload)
    except CamelAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=_ERR("api_error", str(e)))

    return {
        "id": f"msg_{msg_id}", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": text}], "model": model,
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
```

- [ ] **Step 2: 冒烟**

Run: `python -c "from app.api.anthropic_routes import router; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/api/anthropic_routes.py
git commit -m "feat(api): anthropic messages route via converter pipeline"
```

---

### Task 10: api/admin_routes.py（含 /admin/usage 手动拉取 + 30 分钟缓存）

**Files:**
- Create: `app/api/admin_routes.py`

- [ ] **Step 1: 写文件**

```python
"""管理端点 — 账号/配置/模型/用量。"""

import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path

from app.core.auth import verify_admin
from app.core.config import config_manager
from app.core.http import get_http_client
from .deps import get_camel_client

router = APIRouter()

_usage_cache: dict = {"data": None, "time": 0}
USAGE_CACHE_TTL = 1800  # 30 分钟，绝不自动轮询


@router.get("/admin/config")
async def get_admin_config(x_admin_password: str = __import__("fastapi").Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    return config_manager.get_config()


@router.post("/admin/config")
async def set_admin_config(request: Request, x_admin_password: str = __import__("fastapi").Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    config_manager.update_config(await request.json())
    return {"status": "ok", "config": config_manager.get_config()}


@router.post("/admin/login")
async def admin_login(request: Request):
    """手动触发指定账号登录（body 可选 {"email": "..."}），面板已带管理密码即可。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    target = body.get("email", "")
    account = None
    if target:
        account = next((a for a in config_manager.config.camel_accounts if a.email == target), None)
        if not account:
            raise HTTPException(status_code=404, detail={"error": {"message": f"account {target} not found"}})
    else:
        account = config_manager.get_next_account()
    if not account:
        raise HTTPException(status_code=503, detail={"error": {"message": "no account configured"}})
    camel = get_camel_client(account)
    ok = await camel.refresh_cookie()
    account.is_valid = ok
    account.login_time = time.strftime("%m-%d %H:%M") if ok else ""
    config_manager.save()
    return {"status": "ok" if ok else "failed", "email": account.email}


@router.post("/admin/refresh-cookies")
async def refresh_all(x_admin_password: str = __import__("fastapi").Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    results = []
    for acc in config_manager.config.camel_accounts:
        camel = get_camel_client(acc)
        try:
            ok = await camel.refresh_cookie()
            acc.is_valid = ok
            acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
            results.append({"email": acc.email, "ok": ok})
        except Exception as e:
            results.append({"email": acc.email, "ok": False, "error": str(e)})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.post("/admin/test-accounts")
async def test_accounts(x_admin_password: str = __import__("fastapi").Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    http = await get_http_client()
    results = []
    for acc in config_manager.config.camel_accounts:
        camel = get_camel_client(acc)
        await camel.ensure_cookie(http)
        ok = await camel.test_connection(http)
        acc.is_valid = ok
        acc.last_test = time.strftime("%m-%d %H:%M") if ok else acc.last_test
        results.append({"email": acc.email, "ok": ok})
    config_manager.save()
    return {"status": "ok", "results": results}


@router.post("/admin/refresh-models")
async def refresh_models(x_admin_password: str = __import__("fastapi").Header(None, alias="x-admin-password")):
    verify_admin(x_admin_password)
    from .openai_routes import invalidate_model_cache, discover_models
    invalidate_model_cache()
    http = await get_http_client()
    models = await discover_models(http)
    return {"status": "ok", "model_count": len(models)}


@router.get("/admin/usage")
async def get_usage(x_admin_password: str = __import__("fastapi").Header(None, alias="x-admin-password")):
    """用量聚合 — 仅手动调用，30 分钟缓存，绝不自动轮询。"""
    verify_admin(x_admin_password)
    now = time.time()
    if _usage_cache["data"] is not None and (now - _usage_cache["time"]) < USAGE_CACHE_TTL:
        return {"status": "ok", "cached": True, "accounts": _usage_cache["data"]}
    http = await get_http_client()
    rows = []
    for acc in config_manager.config.camel_accounts:
        camel = get_camel_client(acc)
        row = {"email": acc.email}
        try:
            await camel.ensure_cookie(http)
            row["usage"] = await camel.get_usage(http)
            row["search"] = await camel.get_search_limits(http)
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
    _usage_cache["data"] = rows
    _usage_cache["time"] = now
    return {"status": "ok", "cached": False, "accounts": rows}


@router.get("/health")
async def health():
    accounts = [{
        "email": acc.email,
        "cookie_ready": bool(get_camel_client(acc).cookie),
        "is_valid": acc.is_valid,
        "login_time": acc.login_time,
    } for acc in config_manager.config.camel_accounts]
    from .openai_routes import _model_cache
    return {"status": "ok", "accounts": accounts, "model_count": len(_model_cache) if _model_cache else 0}


@router.get("/")
async def serve_web_ui():
    web = Path(__file__).parent.parent / "web" / "index.html"
    if web.exists():
        return FileResponse(str(web))
    raise HTTPException(status_code=404)
```

（`__import__("fastapi").Header` 是为避免与参数名冲突的写法；实际实现时正常 `from fastapi import Header` 并在签名用 `alias="x-admin-password"`，与旧代码一致即可。）

- [ ] **Step 2: 冒烟**

Run: `python -c "from app.api.admin_routes import router; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/api/admin_routes.py
git commit -m "feat(api): admin routes with manual usage aggregation (30min cache)"
```

---

### Task 11: main.py 切换 + 删除旧文件

**Files:**
- Modify: `main.py`
- Delete: `app/routes.py`、`app/anthropic_routes.py`、`app/camel_client.py`、`app/config.py`、`app/auth.py`、`app/session_store.py`、`app/models.py`

- [ ] **Step 1: 重写 `main.py`**

```python
"""CaMeL Chat → OpenAI + Anthropic 兼容 API 代理 — 主入口。"""

import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.openai_routes import router as openai_router, discover_models
from app.api.anthropic_routes import router as anthropic_router
from app.api.admin_routes import router as admin_router
from app.api.deps import get_camel_client
from app.core.config import config_manager
from app.core.http import get_http_client, close_http_client

app = FastAPI(title="CaMeL Chat Proxy", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(openai_router)
app.include_router(anthropic_router)
app.include_router(admin_router)


@app.on_event("startup")
async def startup():
    print("=" * 46)
    print("  CaMeL Chat Proxy v4.0")
    print("=" * 46)
    http = await get_http_client()
    accounts = config_manager.config.camel_accounts
    if accounts:
        print(f"\n[Startup] Logging in {len(accounts)} account(s)...")
        for acc in accounts:
            try:
                ok = await get_camel_client(acc).refresh_cookie()
                acc.is_valid = ok
                print(f"  {'OK' if ok else 'FAIL'} {acc.email}")
            except Exception as e:
                print(f"  FAIL {acc.email}: {e}")
        config_manager.save()
    else:
        print("\n[Startup] No accounts configured. Add one via web UI at /")
    try:
        await discover_models(http)
        print("[Startup] Models pre-loaded")
    except Exception as e:
        print(f"[Startup] Model pre-load failed (non-fatal): {e}")


@app.on_event("shutdown")
async def shutdown():
    await close_http_client()


def main():
    port = int(os.environ.get("PORT", "5050"))
    print(f"\nAPI:   http://0.0.0.0:{port}/v1")
    print(f"Admin: http://0.0.0.0:{port}/")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
```

注意：**去掉了后台会话清理线程**（会话池遗弃策略，不需要删除任务）。

- [ ] **Step 2: 删除旧文件**

```bash
git rm app/routes.py app/anthropic_routes.py app/camel_client.py app/config.py app/auth.py app/session_store.py app/models.py
```

- [ ] **Step 3: 全量测试 + 冒烟**

Run: `python -m pytest -v`
Expected: 全 PASS

Run: `python -c "import main; print('ok')"`
Expected: `ok`（不调用 main()）

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "refactor: wire new layered architecture, drop legacy modules"
```

---

### Task 12: WebUI 重写（Claude 暖米色 + 用量面板）

**Files:**
- Modify: `web/index.html`（保留路径；`app/web/` 不动以减小改动面，admin_routes 的 `Path(__file__).parent.parent / "web"` 解析到项目根 `web/` 一致）

- [ ] **Step 1: 重写 `web/index.html`**

要点（完整文件较长，实现时按此结构写）：

- CSS 变量：`--bg:#f7f5f0; --card:#fffdf9; --ink:#3d3929; --ink-soft:#8a8272; --accent:#d97757; --accent-ink:#fff; --ok:#6b9e78; --err:#c0564f; --line:#e8e2d6`
- 布局：顶部栏（🐪 标题 + 管理密码输入）→ 统计卡片行（账号在线 / 模型数 / 会话轮换阈值）→ 账号卡片列表（邮箱、状态点、登录时间、操作按钮：登录/测试/删除）→ 添加账号表单 → 用量面板（"查询用量"按钮，调 `GET /admin/usage`，展示每账号 usage + search 剩余，标注缓存）→ 设置区（API Keys、管理密码、联网开关、`session_rotate_turns`、刷新模型按钮）
- 全部现有交互保留：增删账号、单账号登录、全部刷新、测试连接、改 Key/密码、刷新模型
- JS：原生 fetch，统一 `api(path, opts)` 封装自动带 `x-admin-password`，toast 提示替代 alert
- 字体：系统字体栈；圆角 12px；`box-shadow: 0 1px 3px rgba(61,57,41,.08)`；按钮悬停微提亮过渡 150ms

- [ ] **Step 2: 启动服务实测面板**

Run: `python main.py`，浏览器打开 `http://localhost:5050/`
Expected: 面板渲染正常，各按钮可用，用量查询返回数据

- [ ] **Step 3: Commit**

```bash
git add web/index.html
git commit -m "feat(web): rebuild admin UI with warm cream theme and usage panel"
```

---

### Task 13: 端到端 curl 实测

**Files:** 无（验证任务）

- [ ] **Step 1: `GET /v1/models`**

Run: `curl http://localhost:5050/v1/models -H "Authorization: Bearer sk-camel"`
Expected: 返回模型列表，含 chat + image 类型

- [ ] **Step 2: 非流式对话**

Run:
```bash
curl -X POST http://localhost:5050/v1/chat/completions -H "Authorization: Bearer sk-camel" -H "Content-Type: application/json" -d "{\"model\":\"claude-haiku-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"用一句话介绍骆驼\"}]}"
```
Expected: 正常回复文本（此前因 UUID 错误必挂）

- [ ] **Step 3: 流式对话**

同上加 `"stream": true`，Expected: SSE chunk 流 + `[DONE]`

- [ ] **Step 4: 图片对话（vision）**

发送带 `image_url`（data URL 小图）的消息，Expected: 模型能描述图片内容

- [ ] **Step 5: 文生图**

```bash
curl -X POST http://localhost:5050/v1/images/generations -H "Authorization: Bearer sk-camel" -H "Content-Type: application/json" -d "{\"model\":\"gpt-image-2\",\"prompt\":\"一只骆驼\",\"size\":\"1024x1024\"}"
```
Expected: 返回 `b64_json` 非空

- [ ] **Step 6: 图生图**

加 `"prior_images": ["data:image/png;base64,..."]`，Expected: 正常返回

- [ ] **Step 7: Anthropic `/v1/messages`（流式 + 非流式 + 图片 block）**

Expected: 三种均正常

- [ ] **Step 8: 会话轮换验证**

连发 11 句，观察日志第 11 句创建新会话；CaMeL 网页端确认会话标题已同步、旧会话未删除

- [ ] **Step 9: 面板用量查询**

点"查询用量"，Expected: 返回各账号 usage + search 剩余；30 分钟内重复点返回 `cached: true`

- [ ] **Step 10: 更新 README 并提交**

README 补新端点说明（`/v1/images/generations` 支持 `prior_images`、`/admin/usage`、配置项 `session_rotate_turns`），然后：

```bash
git add README.md
git commit -m "docs: update README for v4 overhaul"
```

---

## Self-Review 记录

- 规格覆盖：UUID✅(T3) 生图端点✅(T3/T8) 多模态✅(T5/T6) 双取账号✅(T8 deps) join bug✅(T6) Anthropic blocks✅(T7) system 污染✅(T6 独立 system 消息+不污染入参) 503✅(T8) 会话策略✅(T4) 标题同步✅(T4/T8) 用量✅(T10) 联网额度✅(T10) 图生图✅(T8) WebUI✅(T12) 隐蔽✅(T4 遗弃+T10 手动)
- 类型一致性：`build_payload(http, camel, messages, model, session_id, web_search)` 在 T6 定义、T8/T9 使用一致；`session_pool.get_session(http, client, model, email)` T4 定义、T8/T9 使用一致；`resolve_image_to_data_url(http, url)` T5 定义、T6/T8 使用一致。
