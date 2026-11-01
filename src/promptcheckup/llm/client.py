"""A minimal OpenAI-compatible chat client with retries and token accounting."""

import json
import os
import time
from dataclasses import dataclass
from http.client import HTTPException
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMError(RuntimeError):
    """An LLM request failed or returned an invalid response."""


def _token_count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLM:
    """Call a configured chat endpoint, counting only successful calls."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 60,
        max_retries: int = 2,
        backoff_s: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.usage = Usage()
        self._usage_lock = Lock()

    def __call__(self, prompt: str) -> str:
        """Send one user prompt at temperature 0, so judgments are repeatable."""
        headers = {"Content-Type": "application/json"}
        key = ""
        if self.api_key_env:
            try:
                key = os.environ[self.api_key_env]
            except KeyError:
                raise LLMError(
                    f"missing API key environment variable {self.api_key_env!r}"
                ) from None
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            status = None
            try:
                try:
                    response = urlopen(request, timeout=self.timeout_s)
                except HTTPError as error:
                    response = error
                with response:
                    status = response.status
                    body = response.read().decode("utf-8", errors="replace")
            except (URLError, OSError, HTTPException):
                message = "LLM connection error or timeout"
                if status is not None:
                    message = f"LLM HTTP {status}: connection error or timeout"
                retry = True
            except ValueError:
                # Header validation errors can include the Authorization value.
                raise LLMError("invalid LLM request URL or headers") from None
            else:
                safe_body = body.replace(key, "[REDACTED]") if key else body
                detail = f"HTTP {status}: {safe_body[:300]}"
                if 200 <= status < 300:
                    break
                message = f"LLM {detail}"
                retry = status == 429 or 500 <= status < 600
            if not retry or attempt == self.max_retries:
                raise LLMError(message) from None
            time.sleep(self.backoff_s * 2**attempt)

        try:
            result = json.loads(body)
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError(f"invalid LLM response ({detail})") from None
        # Usage is bookkeeping: a missing or odd count must not discard a valid answer.
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        prompt_tokens = _token_count(usage.get("prompt_tokens"))
        completion_tokens = _token_count(usage.get("completion_tokens"))

        with self._usage_lock:
            self.usage.calls += 1
            self.usage.prompt_tokens += prompt_tokens
            self.usage.completion_tokens += completion_tokens
        return content
