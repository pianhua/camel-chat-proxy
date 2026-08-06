"""CaMeL 上游 API 纯客户端 — 只懂 CaMeL 协议，不懂 OpenAI/Anthropic。"""

import time
import uuid
from typing import Optional, AsyncIterator

import httpx

from app.core.config import config_manager
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
        ttl = getattr(config_manager.config, "refresh_interval", 21600)
        if self.cookie and (time.time() - self.cookie_time) < ttl:
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
