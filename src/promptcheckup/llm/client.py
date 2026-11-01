"""A minimal chat-completions client: one request, bounded retries, strict parsing."""

import json
import os
import time
from dataclasses import dataclass
from http.client import HTTPException
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from promptcheckup.errors import errors_as, fallback

TRANSIENT_ERRORS = (URLError, OSError, HTTPException)  # connection failures and timeouts
INVALID_RESPONSE = (ValueError, KeyError, IndexError, TypeError)


class LLMError(RuntimeError):
    """An LLM request failed or returned an invalid response."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class _Reply:
    status: int | None  # None: the connection failed or timed out
    body: str = ""


class LLM:
    """Call a chat-completions endpoint, counting successful calls.

    Temperature is sent only when given: current Claude and OpenAI reasoning models reject it.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 60,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        temperature: float | None = None,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or more")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.temperature = temperature
        self.usage = Usage()
        self._usage_lock = Lock()

    def __call__(self, prompt: str) -> str:
        key = _api_key(self.api_key_env)
        request = _request(self.base_url, self.model, key, prompt, self.temperature)
        reply = _send(request, self.timeout_s, self.max_retries, self.backoff_s)
        if not _succeeded(reply):
            raise LLMError(_failure(reply, key))
        content, usage = _parsed(reply, key)
        self._add_usage(usage)
        return content

    def _add_usage(self, usage: Usage) -> None:
        with self._usage_lock:
            self.usage.calls += usage.calls
            self.usage.prompt_tokens += usage.prompt_tokens
            self.usage.completion_tokens += usage.completion_tokens


def _api_key(api_key_env: str) -> str:
    if not api_key_env:
        return ""  # local servers such as Ollama need no key
    key = os.environ.get(api_key_env)
    if key is None:
        raise LLMError(f"missing API key environment variable {api_key_env!r}")
    if "\r" in key or "\n" in key:
        raise LLMError(f"API key in {api_key_env!r} contains a line break")
    return key


def _request(
    base_url: str, model: str, key: str, prompt: str, temperature: float | None
) -> Request:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        payload["temperature"] = temperature
    with errors_as(LLMError, {ValueError: f"invalid LLM base URL {base_url!r}"}):
        return Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )


def _send(request: Request, timeout_s: float, max_retries: int, backoff_s: float) -> _Reply:
    for attempt in range(max_retries):
        reply = _attempt(request, timeout_s)
        if _succeeded(reply) or not _retryable(reply):
            return reply
        time.sleep(backoff_s * 2**attempt)
    return _attempt(request, timeout_s)


def _attempt(request: Request, timeout_s: float) -> _Reply:
    return fallback(
        lambda: _exchange(request, timeout_s), lambda error: _Reply(None), *TRANSIENT_ERRORS
    )


def _exchange(request: Request, timeout_s: float) -> _Reply:
    # An HTTP error status is still a reply: read its body like any other.
    response = fallback(lambda: urlopen(request, timeout=timeout_s), lambda error: error, HTTPError)
    with response:
        return _Reply(response.status, response.read().decode("utf-8", errors="replace"))


def _succeeded(reply: _Reply) -> bool:
    return reply.status is not None and 200 <= reply.status < 300


def _retryable(reply: _Reply) -> bool:
    return reply.status is None or reply.status == 429 or reply.status >= 500


def _failure(reply: _Reply, key: str) -> str:
    if reply.status is None:
        return "LLM connection error or timeout"
    body = reply.body.replace(key, "[REDACTED]") if key else reply.body
    return f"LLM HTTP {reply.status}: {body[:300]}"


def _parsed(reply: _Reply, key: str) -> tuple[str, Usage]:
    invalid = f"invalid LLM response ({_failure(reply, key)})"
    with errors_as(LLMError, dict.fromkeys(INVALID_RESPONSE, invalid)):
        result = json.loads(reply.body)
        content = result["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise LLMError(invalid)
    # Usage is bookkeeping: a missing or odd count must not discard a valid answer.
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return content, Usage(
        1, _token_count(usage.get("prompt_tokens")), _token_count(usage.get("completion_tokens"))
    )


def _token_count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0
