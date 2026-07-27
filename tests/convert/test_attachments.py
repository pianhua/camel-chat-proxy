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


async def test_file_to_text_remote_without_http_returns_none():
    from app.convert.attachments import resolve_file_to_text
    assert await resolve_file_to_text(None, object(), "https://example.com/a.txt") is None


def test_posix_file_uri_preserved(tmp_path):
    from app.convert.attachments import _read_local
    p = tmp_path / "f.txt"
    p.write_bytes(b"hi")
    # 用 file:/// + 绝对路径模拟 POSIX 风格 URI（Windows 上 tmp_path 带盘符，单独断言 POSIX 分支逻辑）
    got = _read_local("file:///" + str(p).replace("\\", "/"))
    assert got is not None and got[1] == b"hi"
