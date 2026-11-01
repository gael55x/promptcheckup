"""Read explicit prompt files or a Git revision at the command-line boundary."""

import os
import subprocess
from pathlib import Path

from promptcheckup.cli.config import ConfigError
from promptcheckup.errors import errors_as

SOURCE_RULES = (
    (
        lambda n, ref, old, new: not ref or not ref.startswith("-"),
        "--old-ref must not start with '-'",
    ),
    (lambda n, ref, old, new: bool(ref) != bool(old), "provide --old-ref or both --old and --new"),
    (lambda n, ref, old, new: not ref or not new, "--new cannot be used with --old-ref"),
    (lambda n, ref, old, new: bool(ref) or bool(new), "--new is required with --old"),
    (
        lambda n, ref, old, new: bool(ref) or len(old or []) == n,
        "--old must supply one file per prompts.files entry",
    ),
    (
        lambda n, ref, old, new: bool(ref) or len(new or []) == n,
        "--new must supply one file per prompts.files entry",
    ),
)


def load_prompts(config, *, old_ref=None, old=None, new=None) -> tuple[dict, dict]:
    """Map either source onto the configured, project-relative prompt names."""
    _validate_sources(len(config.prompts.files), old_ref, old, new)
    names = [Path(os.path.relpath(path, config.root)).as_posix() for path in config.prompts.files]
    if old_ref:
        previous = [_git_prompt(config.root, old_ref, path) for path in config.prompts.files]
        current = [_read_prompt(path) for path in config.prompts.files]
    else:
        previous = [_read_prompt(Path(path)) for path in old]
        current = [_read_prompt(Path(path)) for path in new]
    return dict(zip(names, previous, strict=True)), dict(zip(names, current, strict=True))


def _validate_sources(count, old_ref, old, new) -> None:
    for predicate, message in SOURCE_RULES:
        if not predicate(count, old_ref, old, new):
            raise ConfigError(message)


def _read_prompt(path: Path) -> str:
    with errors_as(
        ConfigError,
        {
            OSError: lambda error: f"could not read prompt {path}: {error}",
            UnicodeError: lambda error: f"invalid prompt {path}: {error}",
        },
    ):
        return path.read_text(encoding="utf-8")


def _git_prompt(root: Path, ref: str, path: Path) -> str:
    repo = Path(_git(root, ["rev-parse", "--show-toplevel"]).strip())
    with errors_as(ConfigError, {ValueError: f"prompt {path} is outside the git repository"}):
        relative = path.relative_to(repo).as_posix()
    return _git(repo, ["show", f"{ref}:{relative}"])


def _git(root, args) -> str:
    failures = {
        subprocess.CalledProcessError: lambda error: f"git failed: {error.stderr.strip()}",
        subprocess.TimeoutExpired: "git timed out reading old prompts",
        OSError: lambda error: f"could not run git: {error}",
        UnicodeError: lambda error: f"invalid git prompt text: {error}",
    }
    with errors_as(ConfigError, failures):
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        ).stdout
