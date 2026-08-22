"""共享 httpx 异步客户端（支持多代理独立连接池与事件循环自愈）。"""

import asyncio
from typing import Optional, Dict
import httpx

_clients: Dict[Optional[str], httpx.AsyncClient] = {}
_client_loops: Dict[Optional[str], Optional[asyncio.AbstractEventLoop]] = {}


async def get_http_client(proxy: Optional[str] = None) -> httpx.AsyncClient:
    global _clients, _client_loops
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    client = _clients.get(proxy)
    client_loop = _client_loops.get(proxy)

    if client is None or client.is_closed or client_loop is not loop:
        client = httpx.AsyncClient(
            proxy=proxy or None,
            trust_env=False,
            timeout=httpx.Timeout(600.0, connect=30.0),
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30),
        )
        _clients[proxy] = client
        _client_loops[proxy] = loop
    return client


async def close_http_client():
    global _clients, _client_loops
    for proxy, client in list(_clients.items()):
        if client and not client.is_closed:
            try:
                await client.aclose()
            except Exception:
                pass
    _clients.clear()
    _client_loops.clear()


