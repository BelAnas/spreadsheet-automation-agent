"""The language-model providers.

`mock` is the default: it needs no key and no network, so the project runs for anyone and
the tests are deterministic. `openai` talks to any OpenAI-compatible endpoint (OpenAI,
Azure OpenAI, or a local model served by Ollama) and counts what it spends.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# USD per million tokens (input, output). Used only to report the cost of a run.
PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1-mini": (0.40, 1.60)}


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def cost_usd(self, model: str) -> float | None:
        """None when the model's price is not known (a local model costs nothing but electricity)."""
        if model not in PRICES:
            return None
        price_in, price_out = PRICES[model]
        return round((self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000, 6)


@dataclass
class MockLLM:
    """Answers 'I cannot tell' to every question, so the pipeline falls back to its rules.

    It does not pretend to understand anything: offline, an unknown column stays unmapped
    and the summary comes from the template. That keeps offline results honest.
    """
    name: str = "mock"
    model: str = "none"
    usage: Usage = field(default_factory=Usage)
    is_mock: bool = True

    def complete(self, system: str, user: str, max_tokens: int = 200) -> str:
        return ""


@dataclass
class OpenAILLM:
    model: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key: str | None = None
    name: str = "openai"
    usage: Usage = field(default_factory=Usage)
    is_mock: bool = False
    timeout: float = 120.0

    def __post_init__(self) -> None:
        from openai import OpenAI  # imported here so the offline path never needs the package configured

        # A local server ignores the key, but the client refuses to start without one.
        self._client = OpenAI(api_key=self.api_key or "not-needed", base_url=self.base_url or None,
                              timeout=self.timeout, max_retries=1)

    def complete(self, system: str, user: str, max_tokens: int = 200) -> str:
        response = self._client.chat.completions.create(
            model=self.model, temperature=0, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        self.usage.calls += 1
        if response.usage:
            self.usage.input_tokens += response.usage.prompt_tokens
            self.usage.output_tokens += response.usage.completion_tokens
        return (response.choices[0].message.content or "").strip()


def make_llm(provider: str | None = None, model: str | None = None) -> MockLLM | OpenAILLM:
    """Build the provider from arguments, then from the environment. The default is the mock."""
    provider = (provider or os.environ.get("LLM_PROVIDER") or "mock").lower()
    if provider == "mock":
        return MockLLM()
    if provider != "openai":
        raise ValueError(f"unknown LLM provider '{provider}' (use 'mock' or 'openai')")
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    key = os.environ.get("OPENAI_API_KEY") or None
    if not key and not base_url:
        raise ValueError("LLM_PROVIDER=openai needs OPENAI_API_KEY, or OPENAI_BASE_URL for a local model")
    return OpenAILLM(model=model or os.environ.get("LLM_MODEL") or "gpt-4o-mini", base_url=base_url, api_key=key)
