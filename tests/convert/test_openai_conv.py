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


async def test_compact_model_folds_history():
    """特例模型（claude-sonnet-4-6）auto 模式下折叠历史进最后一条 user。"""
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "记住暗号：骆驼喝咖啡。"},
        {"role": "assistant", "content": "好的，记住了。"},
        {"role": "user", "content": "暗号是什么？"},
    ]
    payload = await build_payload(None, None, messages, model="claude-sonnet-4-6",
                                  session_id="816b589c-7f86-4a93-9450-96ac6845cd3f")
    # 折叠后：system 独立 + 单条 user（历史 + 当前消息）
    assert len(payload["messages"]) == 2
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    assert payload["messages"][1]["role"] == "user"
    folded = payload["messages"][1]["content"]
    assert "<对话历史>" in folded and "<当前消息>" in folded
    assert "记住暗号：骆驼喝咖啡。" in folded
    assert "<User>:" in folded and "<Assistant>:" in folded


async def test_normal_model_keeps_array():
    """非特例模型（claude-opus-4-7）auto 模式保持原生数组。"""
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "暗号是什么？"},
    ]
    payload = await build_payload(None, None, messages, model="claude-opus-4-7",
                                  session_id="816b589c-7f86-4a93-9450-96ac6845cd3f")
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "暗号是什么？"}


async def test_message_mode_native_forces_array():
    """message_mode=native 时特例模型也保持数组。"""
    messages = [{"role": "user", "content": "hi"}]
    payload = await build_payload(None, None, messages, model="claude-sonnet-4-6",
                                  session_id="816b589c-7f86-4a93-9450-96ac6845cd3f",
                                  message_mode="native")
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


async def test_sampling_passthrough():
    """sampling 参数透传 + 默认 camelGroup 字段。"""
    messages = [{"role": "user", "content": "hi"}]
    sampling = {"temperature": 0.7, "max_tokens": 4096, "top_p": 0.9}
    payload = await build_payload(None, None, messages, model="m",
                                  session_id="816b589c-7f86-4a93-9450-96ac6845cd3f",
                                  sampling=sampling)
    assert payload["sampling"] == sampling
    assert payload["useCamelGroup"] is False
    assert payload["camelGroup"] == "default"
