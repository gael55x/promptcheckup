"""Adapt Python functions and JSON stdin/stdout commands with per-call timeouts."""

import importlib
import json
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

Agent = Callable[[dict[str, Any], dict[str, str]], Any]


class AgentError(RuntimeError):
    """An agent could not be loaded, called or return JSON-serializable output."""


def python_agent(entry: str) -> Agent:
    """Load an agent from a package.module:function entrypoint."""
    parts = entry.split(":")
    if len(parts) != 2 or not all(parts):
        raise AgentError(f"invalid entry {entry!r}; expected package.module:function")
    module_name, attribute = parts
    try:
        module = importlib.import_module(module_name)
    except Exception as e:
        raise AgentError(f"could not import agent module {module_name!r}: {e}") from e
    try:
        agent = getattr(module, attribute)
    except AttributeError as e:
        raise AgentError(f"agent module {module_name!r} has no attribute {attribute!r}") from e
    if not callable(agent):
        raise AgentError(f"agent {entry!r} is not callable")
    return agent


@dataclass(frozen=True)
class _CommandAgent:
    cmd: tuple[str, ...]

    def __call__(
        self,
        case_input: dict[str, Any],
        prompts: dict[str, str],
        *,
        _timeout_s: float | None = None,
    ) -> Any:
        try:
            result = subprocess.run(
                self.cmd,
                input=json.dumps({"case": case_input, "prompts": prompts}),
                capture_output=True,
                text=True,
                encoding="utf-8",  # not the OS locale: agents in any language speak UTF-8 JSON
                timeout=_timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise AgentError(f"command agent timed out after {_timeout_s:g}s") from e
        except OSError as e:
            raise AgentError(f"could not run command agent: {e}") from e
        if result.returncode:
            raise AgentError(
                f"command agent exited with status {result.returncode}: {result.stderr[-500:]}"
            )
        try:
            return json.loads(result.stdout)
        except ValueError as e:
            raise AgentError(f"command agent returned invalid JSON: {result.stdout[:200]!r}") from e


def command_agent(cmd: Sequence[str]) -> Agent:
    """Adapt an argv command that reads a case and prompts as JSON on stdin."""
    return _CommandAgent(tuple(cmd))


def call_agent(
    agent: Agent,
    case_input: dict[str, Any],
    prompts: dict[str, str],
    *,
    timeout_s: float,
) -> Any:
    """Run an agent within its timeout and require JSON-serializable output."""
    if isinstance(agent, _CommandAgent):
        output = agent(case_input, prompts, _timeout_s=timeout_s)
    else:

        def run():
            try:
                return agent(case_input, prompts)
            except Exception as e:
                # Wrap here so an agent's own TimeoutError is not a deadline expiry.
                raise AgentError(f"Python agent failed: {e}") from e

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(run)
            output = future.result(timeout=timeout_s)
        except TimeoutError as e:
            raise AgentError(f"Python agent timed out after {timeout_s:g}s") from e
        finally:
            # A running Python thread cannot be killed; do not wait after a timeout.
            executor.shutdown(wait=False, cancel_futures=True)
    try:
        json.dumps(output)
    except Exception as e:
        raise AgentError("agent output is not JSON-serializable") from e
    return output
