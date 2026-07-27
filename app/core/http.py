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
