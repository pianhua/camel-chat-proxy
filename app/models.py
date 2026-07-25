"""OpenAI 兼容数据模型。"""

from typing import List, Optional
from pydantic import BaseModel


class OpenAIMessage(BaseModel):
    role: str
    content: Optional[str] = None


class OpenAIRequest(BaseModel):
    model: str = "claude-opus-4-7"
    messages: List[OpenAIMessage]
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    web_search: bool = False


class OpenAIChoice(BaseModel):
    index: int = 0
    message: Optional[OpenAIMessage] = None
    delta: Optional[OpenAIMessage] = None
    finish_reason: Optional[str] = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = 0
    model: str
    choices: List[OpenAIChoice]
    usage: Optional[OpenAIUsage] = None


# Anthropic models
class AnthropicMessage(BaseModel):
    role: str
    content: str


class AnthropicRequest(BaseModel):
    model: str = "claude-opus-4-7"
    messages: List[AnthropicMessage]
    max_tokens: int = 4096
    stream: bool = False
    system: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
