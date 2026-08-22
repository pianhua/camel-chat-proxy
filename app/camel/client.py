"""CaMeL 上游 API 纯客户端 — 只懂 CaMeL 协议，不懂 OpenAI/Anthropic。"""

import time
import uuid
from typing import Optional, AsyncIterator

import httpx

from app.core.config import config_manager
from app.core.logger import get_logger
from .login import playwright_login, CAMEL_BASE, _UA

logger = get_logger(__name__)


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

    def __init__(self, email: str, password: str = "", cookie: str = "", proxy: str = ""):
        self.email = email
        self.password = password
        self.cookie: Optional[str] = cookie or None
        self.cookie_time: float = time.time() if cookie else 0
        self.proxy: str = proxy

    def _get_cookie_val(self) -> str:
        if not self.cookie:
            return ""
        c = self.cookie.strip()
        if "camel_session=" in c:
            import re
            m = re.search(r'camel_session=([^;]+)', c)
            if m:
                return m.group(1).strip()
        return c

    def _headers(self, extra: dict | None = None) -> dict:
        c_val = self._get_cookie_val()
        if not c_val:
            raise CamelAPIError(503, "Cookie not ready")
        h = {
            "user-agent": _UA,
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "origin": CAMEL_BASE,
            "referer": f"{CAMEL_BASE}/chat",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "cookie": f"camel_session={c_val}; camel_lang=zh-CN",
        }
        if extra:
            h.update(extra)
        return h

    def _cookies(self) -> dict:
        c_val = self._get_cookie_val()
        if not c_val:
            raise CamelAPIError(503, "Cookie not ready")
        return {"camel_session": c_val, "camel_lang": "zh-CN"}

    async def ensure_cookie(self, http: httpx.AsyncClient) -> bool:
        ttl = getattr(config_manager.config, "refresh_interval", 21600)
        if self.cookie and (time.time() - self.cookie_time) < ttl:
            return True
        return await self.refresh_cookie()

    async def refresh_cookie(self) -> bool:
        cookie = await playwright_login(self.email, self.password, proxy=self.proxy)
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
            json={"title": title[:64], "model": model},
        )
        self._check(r, (201,))
        return r.json()["id"]

    async def update_session_title(self, http: httpx.AsyncClient, session_id: str, title: str) -> bool:
        try:
            r = await http.put(
                f"{CAMEL_BASE}/api/chat/sessions/{session_id}",
                headers=self._headers({"content-type": "application/json"}),
                json={"title": title[:64]},
                timeout=15.0,
            )
            return r.status_code == 200
        except Exception:
            return False

    async def chat_completion(self, http: httpx.AsyncClient, payload: dict) -> str:
        if "providerId" not in payload:
            payload["providerId"] = "CaMeL"
        r = await http.post(
            f"{CAMEL_BASE}/api/chat/completion",
            headers=self._headers({"content-type": "application/json"}),
            json=payload,
        )
        self._check(r)
        return r.text

    async def stream_chat(self, http: httpx.AsyncClient, payload: dict) -> AsyncIterator[str]:
        if "providerId" not in payload:
            payload["providerId"] = "CaMeL"
        async with http.stream(
            "POST",
            f"{CAMEL_BASE}/api/chat/completion",
            headers=self._headers({"content-type": "application/json"}),
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
        model: str = "gpt-image-2",
        size: str = "auto",
        n: int = 1,
        prior_images: list | None = None,
        session_id: str | None = None,
    ) -> list:
        """生图：调 /api/chat/image 或 /api/chat/completion，返回 data URL 列表。"""
        # 1. 优先尝试 /api/chat/image
        try:
            r = await http.post(
                f"{CAMEL_BASE}/api/chat/image",
                headers=self._headers({"content-type": "application/json"}),
                json={
                    "prompt": prompt,
                    "model": model,
                    "providerId": "CaMeL",
                    "sessionId": session_id or new_uuid(),
                    "size": size,
                    "n": n,
                    "priorImages": prior_images or [],
                    "userMessageId": new_uuid(),
                    "assistantMessageId": new_uuid(),
                },
                timeout=300.0,
            )
            if r.status_code == 200:
                imgs = [img["url"] for img in r.json().get("images", []) if img.get("url")]
                if imgs:
                    return imgs
        except Exception as e:
            logger.debug("[ImageGen] /api/chat/image failed: %s, trying completion", e)

        # 2. 尝试 completion 端点
        if not session_id:
            try:
                session_id = await self.create_session(http, f"Image: {prompt[:30]}", model)
            except Exception:
                session_id = new_uuid()

        payload = {
            "sessionId": session_id,
            "model": model,
            "providerId": "CaMeL",
            "messages": [{"role": "user", "content": prompt}],
            "webSearch": False,
            "sampling": {
                "temperature": 0.7,
                "topP": 0.9,
                "camelGroup": "default",
                "useCamelGroup": False
            }
        }
        try:
            res_text = await self.chat_completion(http, payload)
            import re
            urls = re.findall(r'!\[.*?\]\((data:image/[^;]+;base64,[^\)]+|https?://[^\)]+)\)', res_text)
            if urls:
                return urls
            if res_text.startswith("data:image/") or res_text.startswith("http"):
                return [res_text.strip()]
        except Exception:
            pass

        return []

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
            content=body,
            timeout=60.0,
        )
        self._check(r)
        return r.json()

    async def get_models(self, http: httpx.AsyncClient) -> list:
        try:
            r = await http.get(f"{CAMEL_BASE}/api/chat/models", headers=self._headers(), timeout=15.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass

        # 官方实测 17 款最新模型兜底列表
        return [
            {"id": "claude-fable-5", "providerId": "CaMeL", "displayName": "Claude Fable 5", "description": "最新旗舰 · Mythos 级推理", "type": "chat"},
            {"id": "claude-sonnet-5", "providerId": "CaMeL", "displayName": "Claude Sonnet 5", "description": "新一代均衡之选", "type": "chat"},
            {"id": "claude-opus-4-8", "providerId": "CaMeL", "displayName": "Claude Opus 4.8", "description": "Claude 最强旗舰", "type": "chat"},
            {"id": "claude-opus-4-7", "providerId": "CaMeL", "displayName": "Claude Opus 4.7", "description": "最强推理能力，适合复杂任务", "type": "chat"},
            {"id": "claude-opus-4-6", "providerId": "CaMeL", "displayName": "Claude Opus 4.6", "description": "前代旗舰，性价比高", "type": "chat"},
            {"id": "claude-sonnet-4-6", "providerId": "CaMeL", "displayName": "Claude Sonnet 4.6", "description": "均衡之选，日常推荐", "type": "chat"},
            {"id": "claude-haiku-4-5", "providerId": "CaMeL", "displayName": "Claude Haiku 4.5", "description": "快速响应，低成本", "type": "chat"},
            {"id": "gpt-5.6-sol", "providerId": "CaMeL", "displayName": "GPT-5.6", "description": "OpenAI 最新旗舰 · 深度思考", "type": "chat"},
            {"id": "gpt-5.5", "providerId": "CaMeL", "displayName": "GPT-5.5", "description": "OpenAI 最新旗舰，最强综合能力", "type": "chat"},
            {"id": "gpt-5.4", "providerId": "CaMeL", "displayName": "GPT-5.4", "description": "上一代旗舰，稳定可靠", "type": "chat"},
            {"id": "gemini-3.1-pro", "providerId": "CaMeL", "displayName": "Gemini 3.1 Pro", "description": "Google 最新旗舰，超强推理 + 超长上下文", "type": "chat"},
            {"id": "gemini-3.5-flash", "providerId": "CaMeL", "displayName": "Gemini 3.5 Flash", "description": "Google 极速模型，低延迟", "type": "chat"},
            {"id": "deepseek-v4-pro", "providerId": "CaMeL", "displayName": "DeepSeek V4 Pro", "description": "最新旗舰，对标 GPT-5", "type": "chat"},
            {"id": "deepseek-v3.2", "providerId": "CaMeL", "displayName": "DeepSeek V3.2", "description": "国产最强开源，超高性价比", "type": "chat"},
            {"id": "kimi-k3", "providerId": "CaMeL", "displayName": "Kimi K3", "description": "月之暗面 K3 · 长上下文", "type": "chat"},
            {"id": "gpt-image-2", "providerId": "CaMeL", "displayName": "GPT Image 2", "description": "OpenAI 最新生图模型", "type": "image"},
            {"id": "gemini-3-pro-image-preview", "providerId": "CaMeL", "displayName": "Gemini 3 Pro 生图", "description": "聊天式生图 · 谷歌", "type": "image"},
        ]

    async def get_usage(self, http: httpx.AsyncClient) -> dict:
        r = await http.get(f"{CAMEL_BASE}/api/chat/usage", headers=self._headers())
        self._check(r)
        return r.json()

    async def get_search_limits(self, http: httpx.AsyncClient) -> dict:
        r = await http.get(f"{CAMEL_BASE}/api/chat/search", headers=self._headers())
        self._check(r)
        return r.json()

    async def test_connection(self, http: httpx.AsyncClient) -> bool:
        try:
            return len(await self.get_models(http)) > 0
        except Exception:
            return False
