"""模型别名与最新 17 款模型列表集成测试。"""

import pytest
from app.core.config import config_manager


def test_resolve_model_aliases():
    config_manager.config.model_aliases = {
        "gpt-4o": "claude-opus-4-7",
        "gpt-5": "gpt-5.5",
        "claude-3-7-sonnet": "claude-sonnet-5"
    }

    assert config_manager.resolve_model("gpt-4o") == "claude-opus-4-7"
    assert config_manager.resolve_model("GPT-4O") == "claude-opus-4-7"
    assert config_manager.resolve_model("gpt-5") == "gpt-5.5"
    assert config_manager.resolve_model("claude-3-7-sonnet") == "claude-sonnet-5"
    assert config_manager.resolve_model("claude-fable-5") == "claude-fable-5"


@pytest.mark.asyncio
async def test_fallback_models_list():
    from app.camel.client import CamelClient
    from app.core.http import get_http_client

    client = CamelClient(email="test@a.com")
    http = await get_http_client()
    models = await client.get_models(http)

    model_ids = {m["id"] for m in models}
    assert "claude-fable-5" in model_ids
    assert "claude-opus-4-8" in model_ids
    assert "claude-sonnet-5" in model_ids
    assert "gpt-5.6-sol" in model_ids
    assert "kimi-k3" in model_ids
    assert "gpt-image-2" in model_ids
