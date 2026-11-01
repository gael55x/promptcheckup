"""Redact outputs and error text before evidence leaves the agent boundary."""

from copy import deepcopy
from dataclasses import replace

from promptcheckup.errors import errors_as, fallback
from promptcheckup.runner.agents import AgentError


def redacted_value(redact, value):
    if redact is None:
        return value
    with errors_as(AgentError, {Exception: "run.redact failed"}):
        return redact(deepcopy(value))


def redacted_error(redact, error) -> str:
    if redact is None:
        return str(error)
    return fallback(
        lambda: _error_text(redact, str(error)),
        lambda error: "error text withheld: redaction failed",
        Exception,
    )


def _error_text(redact, text):
    safe = redact(text)
    if not isinstance(safe, str):
        raise ValueError("redacted error must be text")
    return safe


def redacting_agent(agent, redact):
    if redact is None:
        return agent

    def run(case, prompts, timeout_s):
        with errors_as(AgentError, {Exception: lambda error: redacted_error(redact, error)}):
            return redacted_value(redact, agent(case, prompts, timeout_s))

    return run


def redacted_run(redact, run):
    if run.error is not None:
        return replace(run, error=redacted_error(redact, run.error))
    return replace(run, output=redacted_value(redact, run.output))
