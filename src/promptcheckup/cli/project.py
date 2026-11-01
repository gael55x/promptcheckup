"""Project plumbing shared by the commands: agent, cases, cache, redactor and confirmation."""

import json
import shlex
import sys
from contextlib import ExitStack, chdir, contextmanager
from pathlib import Path

from promptcheckup.cli.config import ConfigError
from promptcheckup.cli.init import write_new
from promptcheckup.data import CaseError, load_cases
from promptcheckup.errors import errors_as, fallback
from promptcheckup.runner import Cache, command_agent, load_function, python_agent


def project_cases(config, *, allow_none: bool = False) -> list:
    """Load every case under cases.paths; with allow_none, no matching file means none yet."""
    patterns = [str(path.relative_to(path.anchor)) for path in config.cases.paths]
    anchor = Path(config.root.anchor)  # cases.paths are absolute once the config is loaded
    if allow_none and not any(_matches(anchor, pattern) for pattern in patterns):
        return []
    with errors_as(ConfigError, {CaseError: lambda error: f"cases.paths: {error}"}):
        cases = load_cases(patterns, anchor)
    if not cases:
        raise ConfigError("cases.paths must contain at least one case")
    return cases


def _matches(anchor: Path, pattern: str) -> bool:
    return any(path.is_file() for path in anchor.glob(pattern))


@contextmanager
def project_context(root):
    """Run inside the project, so agents, score functions and relative paths resolve there."""
    with ExitStack() as stack:
        stack.enter_context(chdir(root))
        sys.path.insert(0, str(root))
        stack.callback(sys.path.remove, str(root))
        yield


def project_agent(config):
    if config.agent.entry.strip():
        return python_agent(config.agent.entry)
    with errors_as(ConfigError, {ValueError: lambda error: f"agent.cmd: {error}"}):
        return command_agent(shlex.split(config.agent.cmd))


def project_redactor(config):
    return load_function(config.run.redact) if config.run.redact else None


def project_cache(config) -> Cache:
    directory = config.root / ".promptcheckup"
    write_new(directory / ".gitignore", "*\n")
    # A newly configured redactor must never reuse previously unredacted outputs.
    version = json.dumps([config.run.agent_version, config.run.redact])
    return Cache(directory / "cache", ttl_hours=config.run.cache_ttl_hours, agent_version=version)


def confirm(yes: bool) -> bool:
    """Ask on stderr, so stdout carries only the report; a closed stdin means no."""
    if yes:
        return True
    print("Proceed? [y/N] ", end="", file=sys.stderr, flush=True)
    answer = fallback(input, lambda error: "", EOFError)
    return answer.strip().lower() in ("y", "yes")
