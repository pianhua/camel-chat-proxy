"""CaMeL API 客户端 — 登录、会话管理、聊天补全。"""

import json
import time
import uuid
import httpx
from typing import Optional, AsyncIterator

from .config import CamelAccount


CAMEL_BASE = "https://chat.camel-hub.com"


class CamelAPIError(Exception):
    """CaMeL API 上游错误。"""
    def __init__(self, status_code: int, response_body: str):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"CaMeL API error {status_code}: {response_body[:200]}")


class CamelClient:
    """CaMeL 网页 API 客户端。

    管理单个账号的 Cookie 生命周期、会话创建和聊天请求。
    """

    def __init__(self, account: CamelAccount):
        self.account = account
        self.cookie: Optional[str] = None
        self.cookie_time: float = 0

    async def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "accept-encoding": "gzip, deflate, br, zstd",
            "origin": CAMEL_BASE,
            "referer": f"{CAMEL_BASE}/chat",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "priority": "u=1, i",
        }
        if extra:
            h.update(extra)
        return h

    def _cookies(self) -> dict:
        if not self.cookie:
            raise CamelAPIError(503, "Cookie not ready")
        return {"camel_session": self.cookie}

    async def ensure_cookie(self, http_client: httpx.AsyncClient) -> bool:
        """确保 Cookie 有效，或自动刷新。"""
        if self.cookie and (time.time() - self.cookie_time) < 21600:
            return True
        return await self.refresh_cookie(http_client)

    async def refresh_cookie(self, http_client: httpx.AsyncClient) -> bool:
        """通过 Playwright 自动登录获取 cookie。"""
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
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                    locale="zh-CN",
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                # 隐藏 webdriver 标记
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    delete navigator.__proto__.webdriver;
                """)

                print(f"[Login] Navigating to {CAMEL_BASE}/login ...")
                await page.goto(f"{CAMEL_BASE}/login", wait_until="networkidle", timeout=30000)
                await page.wait_for_selector('input[name="email"]', timeout=10000)

                await page.fill('input[name="email"]', self.account.email)
                await page.fill('input[name="password"]', self.account.password)

                print(f"[Login] Submitting for {self.account.email} ...")
                async with page.expect_navigation(wait_until="networkidle", timeout=30000):
                    await page.click('button[type="submit"]')

                # 等一小会确保 cookie 写入
                await page.wait_for_timeout(2000)
                cookies = await context.cookies()
                print(f"[Login] Got {len(cookies)} cookies: {[c['name'] for c in cookies]}")

                for c in cookies:
                    if c["name"] == "camel_session":
                        self.cookie = c["value"]
                        self.cookie_time = time.time()
                        self.account.is_valid = True
                        self.account.login_time = time.strftime("%m-%d %H:%M")
                        print(f"[Login] ✓ Cookie refreshed for {self.account.email}")
                        await browser.close()
                        return True

                print(f"[Login] ✗ camel_session cookie not found for {self.account.email}")
                await browser.close()
                return False

        except Exception as e:
            print(f"[Login] ✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def create_session(self, http_client: httpx.AsyncClient, title: str = "chat", model: str = "claude-opus-4-7") -> str:
        """创建 CaMeL 会话，返回 sessionId。"""
        r = await http_client.post(
            f"{CAMEL_BASE}/api/chat/sessions",
            headers=await self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json={"title": title[:64], "model": model},
        )
        if r.status_code != 201:
            raise CamelAPIError(r.status_code, r.text)
        return r.json()["id"]

    async def chat_completion(
        self,
        http_client: httpx.AsyncClient,
        messages: list,
        model: str,
        system_prompt: str = "",
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        web_search: bool = False,
        session_id: str | None = None,
        stream: bool = False,
    ) -> str:
        """非流式聊天补全。返回完整文本。"""
        payload = self._build_payload(messages, model, system_prompt, web_search, session_id)
        r = await http_client.post(
            f"{CAMEL_BASE}/api/chat/completion",
            headers=await self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json=payload,
        )
        if r.status_code != 200:
            raise CamelAPIError(r.status_code, r.text)
        return r.text

    async def stream_chat(
        self,
        http_client: httpx.AsyncClient,
        messages: list,
        model: str,
        system_prompt: str = "",
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        web_search: bool = False,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        """流式聊天补全。yield 每个文本 chunk。"""
        payload = self._build_payload(messages, model, system_prompt, web_search, session_id)

        async with http_client.stream(
            "POST",
            f"{CAMEL_BASE}/api/chat/completion",
            headers=await self._headers({"content-type": "application/json"}),
            cookies=self._cookies(),
            json=payload,
        ) as r:
            if r.status_code != 200:
                error_body = await r.aread()
                raise CamelAPIError(r.status_code, error_body.decode(errors="replace"))

            async for chunk in r.aiter_text():
                if chunk:
                    yield chunk

    async def get_models(self, http_client: httpx.AsyncClient) -> list:
        """从 CaMeL API 获取可用模型列表。"""
        r = await http_client.get(
            f"{CAMEL_BASE}/api/chat/models",
            headers=await self._headers(),
            cookies=self._cookies(),
        )
        if r.status_code != 200:
            raise CamelAPIError(r.status_code, r.text)
        return r.json()

    async def test_connection(self, http_client: httpx.AsyncClient) -> bool:
        """测试账号连接是否正常。"""
        try:
            models = await self.get_models(http_client)
            return len(models) > 0
        except Exception:
            return False

    def _build_payload(
        self,
        messages: list,
        model: str,
        system_prompt: str = "",
        web_search: bool = False,
        session_id: str | None = None,
    ) -> dict:
        """构建 CaMeL API 请求体。"""
        has_system = bool(system_prompt)
        last_user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user_text = m["content"]
                break

        # 如果提供了 system_prompt，注入到 payload
        if system_prompt:
            # 把 system prompt 作为第一条 user 消息的前缀
            for m in messages:
                if m["role"] == "user":
                    m["content"] = f"[System instructions]\n{system_prompt}\n\n{m.get('content', '')}"
                    break
            has_system = True

        return {
            "messages": messages,
            "model": model,
            "providerId": "CaMeL",
            "sessionId": session_id or uuid.uuid4().hex[:32],
            "userMessageId": uuid.uuid4().hex[:32],
            "assistantMessageId": uuid.uuid4().hex[:32],
            "userText": last_user_text,
            "hasAttachments": False,
            "hasCustomSystemPrompt": has_system,
            "webSearch": web_search,
        }

    async def delete_conversation(self, http_client: httpx.AsyncClient, conversation_id: str) -> bool:
        """删除 CaMeL 服务端的一条会话（避免侧边栏越积越多）。"""
        try:
            r = await http_client.post(
                f"{CAMEL_BASE}/api/chat/conversation/delete",
                headers=await self._headers({"content-type": "application/json"}),
                cookies=self._cookies(),
                json=[conversation_id],
                timeout=30.0,
            )
            return r.status_code == 200
        except Exception:
            return False

    async def parse_file(self, http_client: httpx.AsyncClient, file_name: str, file_content: bytes, mime_type: str = "text/plain") -> dict:
        """上传文件到 CaMeL 并获取解析后的文本内容。

        Args:
            file_name: 文件名
            file_content: 文件内容的字节数据
            mime_type: 文件 MIME 类型

        Returns:
            {"text": "...", "fileName": "...", "fileSize": 123, "type": "text"}
        """
        import uuid
        boundary = f"----CaMeLFile{uuid.uuid4().hex[:12]}"
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f'Content-Type: {mime_type}\r\n\r\n'
        ).encode() + file_content + f'\r\n--{boundary}--\r\n'.encode()

        r = await http_client.post(
            f"{CAMEL_BASE}/api/chat/parse-file",
            headers=await self._headers({"content-type": f"multipart/form-data; boundary={boundary}"}),
            cookies=self._cookies(),
            content=body,
            timeout=30.0,
        )
        if r.status_code != 200:
            raise CamelAPIError(r.status_code, r.text)
        return r.json()
