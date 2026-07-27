import pytest
from app.convert.anthropic_conv import to_openai_messages, to_camel_model


def test_model_mapping():
    assert to_camel_model("claude-sonnet-4-20250514") == "claude-sonnet-4-6"
    assert to_camel_model("claude-opus-4-7") == "claude-opus-4-7"


def test_string_content():
    msgs = to_openai_messages([{"role": "user", "content": "hi"}], system="sys")
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_block_content_to_openai_parts():
    body_msg = {"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
    ]}
    msgs = to_openai_messages([body_msg])
    content = msgs[0]["content"]
    assert content[0] == {"type": "text", "text": "看图"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
