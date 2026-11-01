import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

import pytest

from promptcheckup.llm import LLM, LLMError, Usage


def completion(content="answer", **fields):
    return {"choices": [{"message": {"content": content}}], **fields}


@dataclass
class Response:
    status: int = 200
    body: object = None
    delay_s: float = 0


@pytest.fixture
def http_server(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                server.requests.append((self.path, self.headers, body))
                response = server.responses.pop(0) if server.responses else Response()
            if response is None:
                return  # Close the connection without an HTTP response.
            time.sleep(response.delay_s)
            payload = completion() if response.body is None else response.body
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A timeout test may already have closed its connection.

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False  # server_close also waits for timed-out request handlers.
    server.requests = []
    server.responses = []
    server.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        assert not thread.is_alive()


def local_llm(server, **kwargs):
    return LLM(
        base_url=server.base_url,
        model="test-model",
        api_key_env="",
        backoff_s=0,
        **kwargs,
    )


def test_posts_chat_request_with_defaults_and_returns_content(http_server, monkeypatch):
    monkeypatch.setenv("PROMPTCHECKUP_TEST_KEY", "fake-test-key")
    llm = LLM(
        base_url=http_server.base_url + "///",
        model="test-model",
        api_key_env="PROMPTCHECKUP_TEST_KEY",
        backoff_s=0,
    )
    http_server.responses = [Response(body=completion("café ✓"))]

    assert llm("hello ✓") == "café ✓"

    path, headers, body = http_server.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer fake-test-key"
    assert body == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello ✓"}],
    }  # no temperature: current Claude and OpenAI reasoning models reject it
    assert llm.usage == Usage(calls=1)


def test_sends_no_authorization_when_no_key_variable_is_configured(http_server):
    llm = local_llm(http_server)

    llm("hello")

    _, headers, body = http_server.requests[0]
    assert "Authorization" not in headers
    assert "temperature" not in body


def test_sends_temperature_only_when_configured(http_server):
    llm = local_llm(http_server, temperature=0)

    llm("hello")

    _, _, body = http_server.requests[0]
    assert body["temperature"] == 0


def test_reads_key_at_each_call_and_reports_missing_variable(http_server, monkeypatch):
    monkeypatch.delenv("PROMPTCHECKUP_TEST_KEY", raising=False)
    llm = LLM(
        base_url=http_server.base_url,
        model="test-model",
        api_key_env="PROMPTCHECKUP_TEST_KEY",
        backoff_s=0,
    )
    with pytest.raises(LLMError, match="PROMPTCHECKUP_TEST_KEY"):
        llm("missing")
    assert http_server.requests == []
    assert llm.usage == Usage()

    for key in ("fake-first-key", "fake-second-key"):
        monkeypatch.setenv("PROMPTCHECKUP_TEST_KEY", key)
        llm("hello")
        assert http_server.requests[-1][1]["Authorization"] == f"Bearer {key}"
        assert key not in repr(llm)


def test_accumulates_optional_usage_and_keeps_instances_separate(http_server):
    http_server.responses = [
        Response(body=completion(usage={"prompt_tokens": 4, "completion_tokens": 2})),
        Response(body=completion(usage={"prompt_tokens": 3})),
        Response(body=completion(usage={"completion_tokens": 5})),
        Response(body=completion()),
    ]
    llm = local_llm(http_server)
    other = local_llm(http_server)
    assert llm.usage == Usage()

    for _ in range(4):
        llm("hello")

    assert llm.usage == Usage(calls=4, prompt_tokens=7, completion_tokens=7)
    assert other.usage == Usage()


def test_accumulates_usage_from_concurrent_calls(http_server):
    http_server.responses = [
        Response(body=completion(usage={"prompt_tokens": 3, "completion_tokens": 2}))
        for _ in range(24)
    ]
    llm = local_llm(http_server, timeout_s=1)

    with ThreadPoolExecutor(max_workers=4) as executor:
        outputs = list(executor.map(llm, ["hello"] * 24))

    assert outputs == ["answer"] * 24
    assert llm.usage == Usage(calls=24, prompt_tokens=72, completion_tokens=48)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 599])
def test_retries_transient_http_errors_and_counts_success_once(http_server, status):
    http_server.responses = [Response(status, b"try again"), Response(status, b"try again")]
    llm = local_llm(http_server)

    assert llm("hello") == "answer"

    assert len(http_server.requests) == 3
    assert all(request[2] == http_server.requests[0][2] for request in http_server.requests)
    assert llm.usage == Usage(calls=1)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_does_not_retry_other_client_errors(http_server, status):
    http_server.responses = [Response(status, b"request rejected")]
    llm = local_llm(http_server)

    with pytest.raises(LLMError, match=str(status)) as caught:
        llm("hello")

    assert "request rejected" in str(caught.value)
    assert len(http_server.requests) == 1
    assert llm.usage == Usage()


@pytest.mark.parametrize("max_retries", [0, 1, 2])
def test_stops_at_retry_limit_and_truncates_final_http_body(http_server, max_retries):
    body = "é" * 300 + "omitted-tail"
    http_server.responses = [Response(503, body.encode("utf-8"))] * (max_retries + 2)
    llm = local_llm(http_server, max_retries=max_retries)

    with pytest.raises(LLMError, match="503") as caught:
        llm("hello")

    assert body[:300] in str(caught.value)
    assert "omitted-tail" not in str(caught.value)
    assert len(http_server.requests) == max_retries + 1
    assert llm.usage == Usage()


def test_retries_disconnected_connections(http_server):
    http_server.responses = [None, None]
    llm = local_llm(http_server)

    assert llm("hello") == "answer"
    assert len(http_server.requests) == 3
    assert llm.usage == Usage(calls=1)


def test_reports_exhausted_connection_errors(http_server):
    http_server.responses = [None, None, None]
    llm = local_llm(http_server)

    with pytest.raises(LLMError, match="connection"):
        llm("hello")

    assert len(http_server.requests) == 3
    assert llm.usage == Usage()


def test_retries_timeout_then_succeeds(http_server):
    http_server.responses = [Response(delay_s=0.15)]
    llm = local_llm(http_server, timeout_s=0.05)

    assert llm("hello") == "answer"
    assert len(http_server.requests) == 2
    assert llm.usage == Usage(calls=1)


def test_reports_exhausted_timeouts(http_server):
    http_server.responses = [Response(delay_s=0.15)] * 3
    llm = local_llm(http_server, timeout_s=0.05)
    before = time.monotonic()

    with pytest.raises(LLMError, match="timeout|timed out"):
        llm("hello")

    assert time.monotonic() - before < 1
    assert len(http_server.requests) == 3
    assert llm.usage == Usage()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"null",
        [],
        {},
        {"choices": []},
        {"choices": None},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {}}]},
        completion(None),
        completion(7),
        completion(["answer"]),
    ],
)
def test_rejects_invalid_completion_without_retry_or_usage(http_server, body):
    http_server.responses = [Response(body=body)]
    llm = local_llm(http_server)

    with pytest.raises(LLMError):
        llm("hello")

    assert len(http_server.requests) == 1
    assert llm.usage == Usage()


def test_accepts_empty_string_content(http_server):
    http_server.responses = [Response(body=completion(""))]

    assert local_llm(http_server)("hello") == ""


@pytest.mark.parametrize("status", [200, 401, 503])
def test_errors_never_expose_key_echoed_by_server(http_server, monkeypatch, status):
    key = "fake-secret-key"
    monkeypatch.setenv("PROMPTCHECKUP_TEST_KEY", key)
    http_server.responses = [Response(status, f"invalid response with {key}".encode())] * 3
    llm = LLM(
        base_url=http_server.base_url,
        model="test-model",
        api_key_env="PROMPTCHECKUP_TEST_KEY",
        backoff_s=0,
    )

    with pytest.raises(LLMError) as caught:
        llm("hello")

    assert key not in str(caught.value)
    assert key not in repr(caught.value)
    assert key not in "".join(traceback.format_exception(caught.value))
    assert key not in repr(llm)


def test_keeps_a_successful_answer_when_usage_counts_are_null(http_server):
    # Some OpenAI-compatible servers report usage fields as null. The answer is valid
    # and already paid for; missing counts must not throw it away.
    http_server.responses = [
        Response(body=completion("ok", usage={"prompt_tokens": None, "completion_tokens": None}))
    ]
    llm = local_llm(http_server)

    assert llm("hi") == "ok"
    assert llm.usage == Usage(calls=1, prompt_tokens=0, completion_tokens=0)


def test_negative_retry_count_is_rejected_up_front():
    with pytest.raises(ValueError, match="max_retries"):
        LLM(base_url="http://127.0.0.1:1", model="m", api_key_env="", max_retries=-1)


def test_key_with_a_line_break_is_rejected_before_any_request(http_server, monkeypatch):
    # A line break would be header injection; rejecting it also means no error can echo it.
    monkeypatch.setenv("PROMPTCHECKUP_TEST_KEY", "fake\r\nX-Injected: 1")
    llm = LLM(base_url=http_server.base_url, model="m", api_key_env="PROMPTCHECKUP_TEST_KEY")

    with pytest.raises(LLMError, match="line break") as caught:
        llm("hello")

    assert http_server.requests == []
    assert "fake" not in str(caught.value)
