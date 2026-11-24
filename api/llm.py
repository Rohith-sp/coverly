"""Groq chat client (OpenAI-compatible endpoint) with JSON mode, key rotation, rate limiting
and retries. PRD §10.3, docs/PLAN.md §3. No SDK: one shared `httpx.AsyncClient` is passed in.
"""

import asyncio
import json
import logging
import random
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from config import Settings, get_settings

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
TEMPERATURE = 0.1
MAX_TRIES = 3  # per HTTP request, retries only on 429 / 5xx / timeouts / network errors
BACKOFF_BASE_S = 1.0  # 1 s, 2 s, ... plus up to 1 s jitter
MAX_RETRY_AFTER_S = 60.0

# Groq free-tier assumptions (per key, ~30 requests/min on most models). Verify in the Groq
# console; two keys roughly double it, but the bucket stays conservative and process-wide.
RATE_LIMIT_RPM = 30
RATE_LIMIT_BURST = 5

Tier = Literal["large", "small"]


class LLMError(Exception):
    """Base class for LLM failures."""


class LLMConfigError(LLMError):
    """No API key or no model configured."""


class LLMRequestError(LLMError):
    """HTTP request failed (non-retryable status, or retries exhausted)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMValidationError(LLMError):
    """Model output was not valid JSON for the schema, twice."""

    def __init__(self, message: str, tokens: int) -> None:
        super().__init__(message)
        self.tokens = tokens


async def _sleep(seconds: float) -> None:
    """Indirection so tests can skip real waiting."""
    await asyncio.sleep(seconds)


class TokenBucket:
    """Async token bucket. Reservation happens without awaiting, so it is race-free on one loop."""

    def __init__(self, rate_per_min: float, burst: int) -> None:
        self.rate = rate_per_min / 60.0
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()

    async def acquire(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        self.tokens -= 1.0  # may go negative: later callers queue behind earlier reservations
        if self.tokens < 0:
            await _sleep(-self.tokens / self.rate)


_bucket = TokenBucket(RATE_LIMIT_RPM, RATE_LIMIT_BURST)
_key_index = 0


def _model_for(tier: Tier, settings: Settings) -> str:
    model = settings.llm_model_large if tier == "large" else settings.llm_model_small
    if not model:
        raise LLMConfigError(f"LLM_MODEL_{tier.upper()} is not set")
    return model


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER_S)
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    return BACKOFF_BASE_S * 2.0 ** (attempt - 1) + random.uniform(0, 1)  # noqa: S311  # jitter


async def _post(
    client: httpx.AsyncClient, keys: list[str], body: dict[str, object], tier: Tier
) -> dict[str, object]:
    """POST with rate limiting, key rotation on 429 and bounded retries. Returns the JSON body."""
    global _key_index
    for attempt in range(1, MAX_TRIES + 1):
        await _bucket.acquire()
        key = keys[_key_index % len(keys)]
        start = time.monotonic()
        wait: float | None = None
        try:
            resp = await client.post(
                GROQ_URL,
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                timeout=TIMEOUT,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            reason = f"{type(exc).__name__}"
            status: int | None = None
        else:
            status = resp.status_code
            if status < 400:
                try:
                    data = resp.json()
                except json.JSONDecodeError as exc:
                    raise LLMRequestError("Groq returned a non-JSON body", status) from exc
                if not isinstance(data, dict):
                    raise LLMRequestError("Groq returned a non-object body", status)
                ms = int((time.monotonic() - start) * 1000)
                tokens = _content_and_tokens(data)[1]
                log.info("llm call tier=%s tokens=%s ms=%d attempt=%d", tier, tokens, ms, attempt)
                return data
            reason = f"HTTP {status}"
            if status == 429:
                _key_index += 1  # rotate to the other key
                wait = _retry_after(resp)
            elif status < 500:
                raise LLMRequestError(f"Groq returned {status}: {resp.text[:200]}", status)
        if attempt == MAX_TRIES:
            raise LLMRequestError(f"Groq failed after {MAX_TRIES} tries: {reason}", status)
        delay = wait if wait is not None else _backoff(attempt)
        log.warning(
            "llm retry tier=%s reason=%s attempt=%d wait_s=%.1f", tier, reason, attempt, delay
        )
        await _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _content_and_tokens(data: dict[str, object]) -> tuple[str, int]:
    usage = data.get("usage")
    tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
    content = ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            content = message["content"]
    return content, int(tokens) if isinstance(tokens, int) else 0


async def complete_json[T: BaseModel](
    client: httpx.AsyncClient,
    model: Tier,
    system: str,
    user: str,
    schema: type[T],
    settings: Settings | None = None,
) -> tuple[T, int]:
    """Chat completion in JSON mode, validated against `schema`.

    Returns (object, total tokens). Invalid output is retried once (tokens of both calls count),
    then `LLMValidationError` is raised.
    """
    settings = settings or get_settings()
    keys = settings.groq_api_keys
    if not keys:
        raise LLMConfigError("no GROQ_API_KEY_1 / GROQ_API_KEY_2 configured")
    body: dict[str, object] = {
        "model": _model_for(model, settings),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": TEMPERATURE,
    }
    total = 0
    error = ""
    for _ in range(2):
        data = await _post(client, keys, body, model)
        content, tokens = _content_and_tokens(data)
        total += tokens
        try:
            return schema.model_validate(json.loads(content)), total
        except (json.JSONDecodeError, ValidationError) as exc:
            error = str(exc)[:200]
            log.warning("llm invalid output tier=%s schema=%s: %s", model, schema.__name__, error)
    raise LLMValidationError(f"invalid {schema.__name__} output twice: {error}", total)
