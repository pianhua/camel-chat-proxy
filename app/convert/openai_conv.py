"""OpenAI 消息格式 → CaMeL payload 转换。"""

import re

from app.camel.client import new_uuid
from .attachments import resolve_image_to_data_url, resolve_file_to_text

OPENAI_SIZE_TO_CAMEL = {
    "1024x1024": "1:1",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
}

# 实测不读取 messages 数组历史（只认最后一条 user + system）的模型，需折叠消息保上下文。
# 依据 2026-08-06 浏览器实测：claude-sonnet-4-6 / claude-haiku-4-5 忽略数组历史，
# 其余模型（opus 4.6/4.7/4.8、sonnet-5、fable-5、kimi-k3、gpt-5.5、gemini 系、deepseek-v4-pro）完整读取。
COMPACT_MODELS = {"claude-sonnet-4-6", "claude-haiku-4-5"}

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


def _needs_compact(model: str, message_mode: str) -> bool:
    """auto：按实测特例模型集合决定；native：保持数组；compact：一律折叠。"""
    if message_mode == "native":
        return False
    if message_mode == "compact":
        return True
    return model in COMPACT_MODELS


def fold_to_text(convo: list) -> str:
    """把 user/assistant 消息折叠成纯文本（DS2API 风格 <User>:/<Assistant>: 角色标记）。
    供不读数组历史的特例模型使用，历史作为文本注入最后一条 user。"""
    blocks = []
    for m in convo:
        role = m.get("role")
        tag = {"user": "User", "assistant": "Assistant"}.get(role, str(role))
        blocks.append(f"<{tag}>:\n{m.get('content', '')}")
    return "\n\n".join(blocks)


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


async def build_payload(http, camel, messages: list, model: str, session_id: str, web_search: bool = False,
                        sampling: dict | None = None, message_mode: str = "auto") -> dict:
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

    if _needs_compact(model, message_mode) and len(convo) > 1:
        convo = _compact_convo(convo, system)

    payload = {
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
        "useCamelGroup": False,
        "camelGroup": "default",
    }
    # 专家模式采样参数（浏览器实测 /api/chat/completion 支持 sampling 对象）
    if sampling:
        payload["sampling"] = sampling
    return payload


def _compact_convo(convo: list, system: str) -> list:
    """折叠消息：system 独立保留，user/assistant 历史文本化后并入最后一条 user。
    特例模型只读最后一条 user + system，折叠后可恢复完整上下文（已实测验证）。"""
    others = [m for m in convo if m["role"] != "system"]
    if not others:
        return convo
    # 保证最后一条是 user：若末尾是 assistant，则并入历史一起文本化
    if others[-1]["role"] == "user":
        last = others[-1]
        history = others[:-1]
        last_text = last["content"]
    else:
        last = None
        history = others
        last_text = ""
    folded = fold_to_text(history)
    parts = []
    if folded:
        parts.append("<对话历史>\n" + folded)
    if last_text:
        parts.append("<当前消息>\n" + last_text)
    text = "\n\n".join(parts)
    out = [{"role": "user", "content": text}]
    if system:
        out.insert(0, {"role": "system", "content": system})
    return out
