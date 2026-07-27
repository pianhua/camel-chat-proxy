"""OpenAI 消息格式 → CaMeL payload 转换。"""

import re
from typing import Optional

from app.camel.client import new_uuid
from .attachments import resolve_image_to_data_url, resolve_file_to_text

OPENAI_SIZE_TO_CAMEL = {
    "1024x1024": "1:1",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
}

_MD_IMAGE_RE = re.compile(r"!\[.*?\]\((https?://[^\)]+)\)")
_FILE_RE = re.compile(r"\[📎.*?\]\(([^\)]+)\)")


def extract_system(messages: list) -> str:
    parts = []
    for m in messages:
        if m.get("role") == "system":
            c = m.get("content", "")
            parts.append(c if isinstance(c, str) else " ".join(
                i.get("text", "") for i in c if isinstance(i, dict) and i.get("type") == "text"
            ))
    return "\n".join(p for p in parts if p)


async def _convert_one(http, camel, msg: dict) -> tuple[dict, bool, str]:
    """单条 user/assistant 消息 → CaMeL 格式。返回 (消息, 是否含附件, 追加附件前的纯文本)。不修改入参。"""
    role = msg["role"]
    content = msg.get("content", "")
    attachments: list[str] = []  # data URL 图片

    if isinstance(content, list):
        texts, images, files = [], [], []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text":
                texts.append(item.get("text", ""))
            elif t == "image_url":
                u = (item.get("image_url") or {}).get("url", "")
                if u:
                    images.append(u)
            elif t == "file_url":
                u = (item.get("file_url") or {}).get("url", "")
                if u:
                    files.append(u)
        text = "\n".join(t for t in texts if t)
    else:
        text = content
        images = _MD_IMAGE_RE.findall(text) if role == "user" else []
        files = _FILE_RE.findall(text) if role == "user" else []

    for u in images:
        data_url = await resolve_image_to_data_url(http, u)
        if data_url:
            attachments.append(data_url)
    file_texts = []
    for u in files:
        ft = await resolve_file_to_text(http, camel, u)
        if ft:
            file_texts.append((u.split("/")[-1].split("?")[0] or "file", ft))
        else:
            data_url = await resolve_image_to_data_url(http, u)
            if data_url:
                attachments.append(data_url)

    pure_text = text
    if attachments:
        for i, du in enumerate(attachments):
            text += f"\n[file: image-{i + 1}.png]"
        text += "\n" + "\n".join(f"![本地图片]({du})" for du in attachments)
    for name, ft in file_texts:
        text += f"\n\n[文件内容: {name}]\n{ft}"

    return {"role": role, "content": text}, bool(attachments or file_texts), pure_text


async def build_payload(http, camel, messages: list, model: str, session_id: str, web_search: bool = False) -> dict:
    """OpenAI messages → CaMeL completion payload（完整新对象，不污染入参）。"""
    system = extract_system(messages)
    convo, has_attach, user_text = [], False, ""
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        converted, had, pure_text = await _convert_one(http, camel, m)
        convo.append(converted)
        has_attach = has_attach or had
        if converted["role"] == "user":
            user_text = pure_text
    if system:
        convo.insert(0, {"role": "system", "content": system})

    return {
        "messages": convo,
        "model": model,
        "providerId": "CaMeL",
        "sessionId": session_id,
        "userMessageId": new_uuid(),
        "assistantMessageId": new_uuid(),
        "userText": user_text,
        "hasAttachments": has_attach,
        "hasCustomSystemPrompt": bool(system),
        "webSearch": web_search,
    }
