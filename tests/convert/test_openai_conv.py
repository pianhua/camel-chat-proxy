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
