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
