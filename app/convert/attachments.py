"""统一附件管线：图片/文件 → CaMeL 可用的 data URL 或文本。"""

import base64
import os
import re
from typing import Optional

import httpx

_MIME_BY_EXT = {
    ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
    ".py": "text/x-python", ".js": "text/javascript", ".html": "text/html",
    ".css": "text/css", ".csv": "text/csv", ".xml": "text/xml",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".pdf": "application/pdf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def is_image_mime(mime: str) -> bool:
    return (mime or "").lower().startswith("image/")


def _is_local_path(url: str) -> bool:
    return (
        url.startswith("file:///")
        or bool(re.match(r"^[A-Za-z]:[/\\]", url))
        or (url.startswith("/") and not url.startswith("//"))
    )


def _read_local(path: str) -> tuple[str, bytes, str] | None:
    """返回 (文件名, 内容, mime)；失败 None。"""
    if path.startswith("file:///"):
        path = path[8:]
    path = os.path.normpath(path)
    if not os.path.exists(path):
        print(f"[Attach] Local file not found: {path}")
        return None
    name = os.path.basename(path) or "file"
    mime = _MIME_BY_EXT.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
    with open(path, "rb") as f:
        return name, f.read(), mime


async def _read_remote(http: httpx.AsyncClient, url: str) -> tuple[str, bytes, str] | None:
    try:
        r = await http.get(url, timeout=30.0, follow_redirects=True)
        if r.status_code != 200:
            return None
        name = url.split("/")[-1].split("?")[0] or "file"
        mime = r.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        return name, r.content, mime
    except Exception as e:
        print(f"[Attach] Download failed {url}: {e}")
        return None


async def resolve_image_to_data_url(http: Optional[httpx.AsyncClient], url: str) -> Optional[str]:
    """http URL / data URL / 本地路径 → data:image/...;base64,..."""
    if url.startswith("data:"):
        return url
    got = _read_local(url) if _is_local_path(url) else (await _read_remote(http, url) if http else None)
    if not got:
        return None
    name, content, mime = got
    if not is_image_mime(mime):
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


async def resolve_file_to_text(http, camel, url: str) -> Optional[str]:
    """文件 → 经 /api/chat/parse-file 解析为文本。图片返回 None（走 resolve_image_to_data_url）。"""
    got = _read_local(url) if _is_local_path(url) else await _read_remote(http, url)
    if not got:
        return None
    name, content, mime = got
    if is_image_mime(mime):
        return None
    parsed = await camel.parse_file(http, name, content, mime)
    return parsed.get("text") or None
