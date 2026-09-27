"""Pluggable LLM providers over plain HTTP (httpx): Ollama (local), Anthropic, OpenAI, OpenRouter, Groq, Gemini."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

DEFAULT_MODELS = {
    "ollama": "llama3.1",
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4.1-mini",
    "openrouter": "anthropic/claude-sonnet-5",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
}
KEY_NAMES = {"anthropic": "ai_key_anthropic", "openai": "ai_key_openai", "openrouter": "ai_key_openrouter",
             "groq": "ai_key_groq", "gemini": "ai_key_gemini"}
# Groq free tier has low tokens-per-minute limits, so keep its chunks small.
CONTEXT_TOKENS = {"ollama": 6000, "anthropic": 60000, "openai": 30000, "openrouter": 30000, "groq": 8000,
                  "gemini": 60000}
# OpenAI-compatible endpoints
BASE_URLS = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1",
             "groq": "https://api.groq.com/openai/v1",
             "gemini": "https://generativelanguage.googleapis.com/v1beta/openai"}


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult: ...


class _Base:
    name = "base"

    def __init__(self, model: str, timeout: float = 180.0) -> None:
        self.model = model
        self.timeout = timeout


class OllamaProvider(_Base):
    name = "ollama"

    def __init__(self, model: str, url: str) -> None:
        super().__init__(model, timeout=600.0)
        self.url = url.rstrip("/")

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.url}/api/chat", json={
                "model": self.model, "stream": False, "format": "json", "options": {"num_predict": max_tokens},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
        return LLMResult(d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0))


class AnthropicProvider(_Base):
    name = "anthropic"

    def __init__(self, model: str, key: str) -> None:
        super().__init__(model)
        self.key = key

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage", {})
        return LLMResult(text, u.get("input_tokens", 0), u.get("output_tokens", 0))


class OpenAICompatProvider(_Base):
    name = "openai"

    def __init__(self, model: str, key: str, base_url: str = "https://api.openai.com/v1", name: str = "openai") -> None:
        super().__init__(model)
        self.key = key
        self.base_url = base_url
        self.name = name

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.key}"},
                             json={"model": self.model, "max_tokens": max_tokens,
                                   "response_format": {"type": "json_object"},
                                   "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
        u = d.get("usage", {})
        return LLMResult(d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0),
                         u.get("completion_tokens", 0))


def make_provider(provider: str, model: str, *, key: str | None, ollama_url: str) -> Any:
    model = model or DEFAULT_MODELS[provider]
    if provider == "ollama":
        return OllamaProvider(model, ollama_url)
    if not key:
        raise ValueError("ai_key_missing")
    if provider == "anthropic":
        return AnthropicProvider(model, key)
    if provider in BASE_URLS:
        return OpenAICompatProvider(model, key, BASE_URLS[provider], provider)
    raise ValueError("unknown_provider")
