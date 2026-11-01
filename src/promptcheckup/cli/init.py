"""Write packaged starter files without replacing existing project files."""

from importlib.resources import files
from pathlib import Path

from promptcheckup.cli.config import ConfigError
from promptcheckup.errors import errors_as, fallback

TEMPLATES = {
    "promptcheckup.toml": "promptcheckup.toml",
    "cases/example.jsonl": "example.jsonl",
    ".github/workflows/promptcheckup-gate.yml": "promptcheckup-gate.yml",
    ".github/workflows/promptcheckup-checkup.yml": "promptcheckup-checkup.yml",
}


def initialize(directory: Path) -> int:
    """Create the starter files from package resources, reporting every skip."""
    with errors_as(ConfigError, {OSError: lambda error: f"could not initialize project: {error}"}):
        for destination, template in TEMPLATES.items():
            content = (
                files("promptcheckup.cli")
                .joinpath("templates", template)
                .read_text(encoding="utf-8")
            )
            written = write_new(directory / destination, content)
            print(f"{'Wrote' if written else 'Skipped'} {destination}")
    print("Recorded cases may contain PII. Configure run.redact before using private data.")
    print("Keep .promptcheckup/cache/ out of version control.")
    return 0


def write_new(path, content) -> bool:
    """Create a file with its parents, or return False if it already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return fallback(lambda: _create(path, content), lambda error: False, FileExistsError)


def _create(path, content) -> bool:
    with path.open("x", encoding="utf-8") as file:
        file.write(content)
    return True
