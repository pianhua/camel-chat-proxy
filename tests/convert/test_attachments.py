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
