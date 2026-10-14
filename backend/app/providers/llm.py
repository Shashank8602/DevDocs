"""Chat LLM provider wrapper. Nothing else in the app talks to Groq or Gemini directly.

Both providers speak the OpenAI chat-completions protocol (via the `openai` SDK), so `LLM_PROVIDER`
only changes base URL, key, model and the name of the output-token parameter. Every call first draws
from the shared rate limiter of its provider key (`groq_llm` / `gemini_llm`); `stream` yields only
the final answer's content tokens, never reasoning tokens."""
import logging
from collections.abc import AsyncIterator
from typing import Protocol

import httpx
import openai

from app.config import settings
from app.providers.rate_limit import RateLimiter, estimate_tokens, get_limiter

log = logging.getLogger("devdocs.llm")

PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1", "key_var": "GROQ_API_KEY",
        "max_tokens_param": "max_completion_tokens",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_var": "GEMINI_API_KEY",
        "max_tokens_param": "max_tokens",
    },
}


class LLMError(Exception):
    """The LLM call failed. The message is internal; users only see a generic failure."""


class LLMProvider(Protocol):
    provider: str
    model: str

    def stream(self, messages: list[dict[str, str]], max_output_tokens: int | None = None) -> AsyncIterator[str]: ...

    def wait_estimate(self, messages: list[dict[str, str]], max_output_tokens: int | None = None) -> float: ...


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(m["content"]) + 4 for m in messages)


class OpenAICompatLLMProvider:
    def __init__(
        self, provider: str, api_key: str, model: str, max_output_tokens: int, max_retries: int,
        timeout: float, limiter: RateLimiter | None = None, transport: httpx.AsyncBaseTransport | None = None,
        reasoning_effort: str = "low", temperature: float = 0.2,
    ):
        self.provider, self.model, self.api_key = provider, model, api_key
        self.reasoning_effort, self.temperature = reasoning_effort, temperature
        self.max_output_tokens, self.max_retries, self.timeout = max_output_tokens, max_retries, timeout
        self.limiter = limiter or RateLimiter()  # unlimited unless one is supplied
        self.transport = transport  # tests inject a mock transport; None uses the network

    def wait_estimate(self, messages: list[dict[str, str]], max_output_tokens: int | None = None) -> float:
        """Seconds the rate limiter would make the next call wait (the chat shows it instead of stalling)."""
        return self.limiter.wait_estimate(
            requests=1, tokens=estimate_prompt_tokens(messages) + (max_output_tokens or self.max_output_tokens),
        )

    async def stream(
        self, messages: list[dict[str, str]], max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        spec = PROVIDERS[self.provider]
        if not self.api_key:
            raise LLMError(f"{spec['key_var']} is not set")
        # A stream's real cost is unknown up front, so admission reserves prompt tokens + the output
        # ceiling; once the stream ends the reservation is corrected to what the call really used, so a
        # short answer does not keep the next question waiting for tokens it never spent.
        max_out = max_output_tokens or self.max_output_tokens
        prompt_tokens = estimate_prompt_tokens(messages)
        reservation = await self.limiter.acquire(requests=1, tokens=prompt_tokens + max_out)
        used: int | None = None  # provider-reported total tokens, when the stream sends them
        produced = 0  # characters streamed, for the fallback estimate
        http_client = httpx.AsyncClient(transport=self.transport) if self.transport else None
        client = openai.AsyncOpenAI(
            api_key=self.api_key, base_url=spec["base_url"], max_retries=self.max_retries,
            timeout=self.timeout, http_client=http_client,
        )
        try:
            response = await client.chat.completions.create(
                model=self.model, messages=messages, stream=True, reasoning_effort=self.reasoning_effort,
                stream_options={"include_usage": True},
                temperature=self.temperature, **{spec["max_tokens_param"]: max_out},
            )
            async for chunk in response:
                if getattr(chunk, "usage", None) is not None:
                    used = chunk.usage.total_tokens
                if not chunk.choices:
                    continue
                text = chunk.choices[0].delta.content
                if text:  # reasoning arrives in a separate field and is never forwarded
                    produced += len(text)
                    yield text
        except openai.OpenAIError as exc:
            raise LLMError(f"{self.provider} request failed: {exc!r}") from exc
        finally:
            self.limiter.settle(reservation, used if used is not None else prompt_tokens + produced // 4)
            await client.close()


class RoutingLLMProvider:
    """Spreads load over two models of the same provider. Free-tier limits (Groq: tokens per minute) are
    per model, so when the primary model would make this call wait longer than `max_wait` seconds and the
    fallback would wait less, the call goes to the fallback instead of stalling. Both stay on one
    provider; nothing here ever switches to a different vendor."""

    def __init__(self, primary: OpenAICompatLLMProvider, fallback: OpenAICompatLLMProvider, max_wait: float):
        self.primary, self.fallback, self.max_wait = primary, fallback, max_wait
        self.provider, self.model = primary.provider, primary.model

    def _pick(self, messages, max_output_tokens) -> OpenAICompatLLMProvider:
        wait = self.primary.wait_estimate(messages, max_output_tokens)
        if wait <= self.max_wait:
            return self.primary
        if self.fallback.wait_estimate(messages, max_output_tokens) < wait:
            log.info("primary model %s would wait %.0fs; using %s", self.primary.model, wait, self.fallback.model)
            return self.fallback
        return self.primary

    def wait_estimate(self, messages: list[dict[str, str]], max_output_tokens: int | None = None) -> float:
        return self._pick(messages, max_output_tokens).wait_estimate(messages, max_output_tokens)

    def stream(self, messages: list[dict[str, str]], max_output_tokens: int | None = None) -> AsyncIterator[str]:
        return self._pick(messages, max_output_tokens).stream(messages, max_output_tokens)


_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        name = settings.llm_provider.strip().lower()
        if name not in PROVIDERS:
            raise LLMError(f"LLM_PROVIDER must be one of {sorted(PROVIDERS)}, got {name!r}")
        key, model = (
            (settings.groq_api_key, settings.groq_llm_model) if name == "groq"
            else (settings.gemini_api_key, settings.gemini_llm_model)
        )
        def build(model_name: str, limiter_key: str) -> OpenAICompatLLMProvider:
            return OpenAICompatLLMProvider(
                name, key, model_name, settings.llm_max_output_tokens, settings.llm_max_retries,
                settings.llm_timeout_seconds, limiter=get_limiter(limiter_key),
                reasoning_effort=settings.llm_reasoning_effort, temperature=settings.llm_temperature,
            )

        primary = build(model, f"{name}_llm")
        fallback_model = settings.groq_fallback_model.strip() if name == "groq" else ""
        _provider = (
            RoutingLLMProvider(primary, build(fallback_model, "groq_llm_fallback"), settings.llm_fallback_wait_seconds)
            if fallback_model and fallback_model != model else primary
        )
    return _provider


def set_llm_provider(provider: LLMProvider | None) -> None:
    """Swap the provider (tests use a fake; `None` goes back to the configured one)."""
    global _provider
    _provider = provider
