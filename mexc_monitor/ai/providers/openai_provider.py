"""OpenAI LLM Provider — supports GPT-4o, GPT-4o-mini, etc."""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

from .base import LLMMessage, LLMProvider, LLMResponse, LLMTool


class OpenAIProvider(LLMProvider):
    """OpenAI API provider for chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _format_messages(self, messages: list[LLMMessage]) -> list[dict]:
        out = []
        for m in messages:
            d: dict = {"role": m.role}
            if m.content:
                d["content"] = m.content
            if m.tool_calls:
                d["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name and m.role == "tool":
                d["name"] = m.name
            out.append(d)
        return out

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body: dict = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = self.tools_to_dict(tools)

        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            data = r.json()

        choice = data["choices"][0]
        msg = choice.get("message", {})
        return LLMResponse(
            content=msg.get("content", ""),
            tool_calls=msg.get("tool_calls", []),
            finish_reason=choice.get("finish_reason", ""),
            usage=data.get("usage", {}),
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        body: dict = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = self.tools_to_dict(tools)

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
