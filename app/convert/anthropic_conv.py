"""Anthropic Messages 格式 → OpenAI 中间格式（再经 openai_conv 转 CaMeL）。"""

ANTHROPIC_MODEL_MAP = {
    "claude-sonnet-4-20250514": "claude-sonnet-4-6",
    "claude-opus-4-20250514": "claude-opus-4-7",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-5": "claude-sonnet-4-6",
    "claude-opus-4-5": "claude-opus-4-7",
    "claude-opus-4-1": "claude-opus-4-7",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5",
}


def to_camel_model(model: str) -> str:
    return ANTHROPIC_MODEL_MAP.get(model, model)


def _blocks_to_openai(content) -> object:
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif t == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
            else:
                url = src.get("url", "")
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts if parts else ""


def to_openai_messages(messages: list, system: str = "") -> list:
    out = []
    if system:
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
        out.append({"role": "system", "content": system})
    for m in messages:
        out.append({"role": m.get("role", "user"), "content": _blocks_to_openai(m.get("content", ""))})
    return out
