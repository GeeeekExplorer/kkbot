"""OpenAI-compatible LLM provider."""

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from openai import AsyncOpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _mark_last_user(messages: list[dict]) -> list[dict]:
    """Add cache_control to the last user message before sending to LLM."""
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            msg = dict(result[i])
            content = msg.get("content", "")
            if isinstance(content, str):
                msg["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list) and content:
                content = [dict(c) for c in content]
                content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
                msg["content"] = content
            result[i] = msg
            break
    return result


class LLMProvider:
    def __init__(self, api_key: str, api_base: str, model: str, max_tokens: int = 4096):
        self._client = AsyncOpenAI(api_key=api_key or "sk-placeholder", base_url=api_base)
        self.model, self.max_tokens = model, max_tokens

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _mark_last_user(messages),
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("LLM error: {}", e)
            return LLMResponse(content=f"Error: {e}", finish_reason="error")

        choice = resp.choices[0]
        msg = choice.message
        tool_calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
        )
