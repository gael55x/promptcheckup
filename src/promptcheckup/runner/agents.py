"""Agents behind one protocol: (case_input, prompts, timeout_s) -> JSON-serializable output."""

import importlib
import json
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from promptcheckup.errors import errors_as

Agent = Callable[[dict[str, Any], dict[str, str], float], Any]
UserFunction = Callable[[dict[str, Any], dict[str, str]], Any]


class AgentError(RuntimeError):
    """An agent could not be loaded, run within its timeout, or return JSON output."""


def function_agent(function: UserFunction) -> Agent:
    """Adapt an in-process `function(case_input, prompts)` with a per-call timeout."""
    return lambda case_input, prompts, timeout_s: _with_timeout(
        function, (case_input, prompts), timeout_s
    )


def python_agent(entry: str) -> Agent:
    """Load `package.module:function` and adapt it like `function_agent`."""
    return function_agent(_load_function(entry))


def command_agent(cmd: Sequence[str]) -> Agent:
    """Adapt an argv command that reads {"case", "prompts"} JSON on stdin, prints JSON."""
    argv = tuple(cmd)
    return lambda case_input, prompts, timeout_s: _run_command(
        argv, {"case": case_input, "prompts": prompts}, timeout_s
    )


def call_agent(
    agent: Agent, case_input: dict[str, Any], prompts: dict[str, str], *, timeout_s: float
) -> Any:
    """Run any agent and require output that can be stored and compared as JSON."""
    output = agent(case_input, prompts, timeout_s)
    not_json = "agent output is not JSON-serializable"
    with errors_as(AgentError, {TypeError: not_json, ValueError: not_json}):
        json.dumps(output)
    return output


def _load_function(entry: str) -> UserFunction:
    module_name, _, attribute = entry.partition(":")
    if not module_name or not attribute or ":" in attribute:
        raise AgentError(f"invalid entry {entry!r}; expected package.module:function")
    with errors_as(AgentError, {Exception: f"could not import {module_name!r}: {{error}}"}):
        module = importlib.import_module(module_name)
    function = getattr(module, attribute, None)
    if not callable(function):
        raise AgentError(f"agent {entry!r} is missing or not callable")
    return function


def _with_timeout(function: UserFunction, args: tuple, timeout_s: float) -> Any:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_guarded, function, args)
    executor.shutdown(wait=False)  # a running thread cannot be killed; never wait on it
    with errors_as(AgentError, {TimeoutError: f"Python agent timed out after {timeout_s:g}s"}):
        return future.result(timeout=timeout_s)


def _guarded(function: UserFunction, args: tuple) -> Any:
    # Inside the thread, so an agent's own TimeoutError is never mistaken for the deadline.
    with errors_as(AgentError, {Exception: "Python agent failed: {error}"}):
        return function(*args)


def _run_command(argv: tuple[str, ...], payload: dict, timeout_s: float) -> Any:
    failures = {
        subprocess.TimeoutExpired: f"command agent timed out after {timeout_s:g}s",
        OSError: "could not run command agent: {error}",
    }
    with errors_as(AgentError, failures):
        result = subprocess.run(
            argv,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",  # not the OS locale: agents in any language speak UTF-8 JSON
            timeout=timeout_s,
        )
    if result.returncode:
        raise AgentError(
            f"command agent exited with status {result.returncode}: {result.stderr[-500:]}"
        )
    with errors_as(
        AgentError, {ValueError: f"command agent returned invalid JSON: {result.stdout[:200]!r}"}
    ):
        return json.loads(result.stdout)
