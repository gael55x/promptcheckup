"""Load project configuration, validate once, and resolve project-relative paths."""

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from promptcheckup.errors import errors_as

TEXT_FIELDS = (
    "agent.entry",
    "agent.cmd",
    "agent.description",
    "scoring.mode",
    "scoring.score",
    "scoring.criteria",
    "llm.base_url",
    "llm.model",
    "llm.api_key_env",
    "run.agent_version",
    "run.redact",
)
RULES = tuple(
    (lambda c, key=key: _text_field(c, key), f"{key} must be a string") for key in TEXT_FIELDS
) + (
    (
        lambda c: bool(c.agent.entry.strip()) != bool(c.agent.cmd.strip()),
        "exactly one of agent.entry or agent.cmd is required",
    ),
    (lambda c: c.scoring.mode in ("score", "judge"), 'scoring.mode must be "score" or "judge"'),
    (
        lambda c: c.scoring.mode != "score" or bool(c.scoring.score.strip()),
        'scoring.score is required when mode = "score"',
    ),
    (
        lambda c: c.scoring.mode != "judge" or bool(c.scoring.criteria.strip()),
        'scoring.criteria is required when mode = "judge"',
    ),
    (
        lambda c: c.scoring.mode != "judge" or c.llm is not None,
        'llm is required when mode = "judge"',
    ),
    (
        lambda c: c.scoring.mode != "judge" or bool(c.llm.base_url.strip()),
        'llm.base_url is required when mode = "judge"',
    ),
    (
        lambda c: c.scoring.mode != "judge" or bool(c.llm.model.strip()),
        'llm.model is required when mode = "judge"',
    ),
    (lambda c: _path_list(c.prompts.files), "prompts.files must be a non-empty list of paths"),
    (
        lambda c: (
            len({(c.root / path).resolve() for path in c.prompts.files}) == len(c.prompts.files)
        ),
        "prompts.files must contain unique paths",
    ),
    (lambda c: _path_list(c.cases.paths), "cases.paths must be a non-empty list of paths"),
    (lambda c: type(c.run.samples) is int and c.run.samples >= 1, "run.samples must be at least 1"),
    (
        lambda c: c.scoring.mode != "judge" or c.run.samples >= 2,
        'run.samples must be at least 2 when mode = "judge"',
    ),
    (
        lambda c: type(c.run.min_pairs) is int and c.run.min_pairs >= 5,
        "run.min_pairs must be at least 5",
    ),
    (
        lambda c: type(c.run.max_calls) is int and c.run.max_calls >= 0,
        "run.max_calls must be a non-negative integer",
    ),
    (
        lambda c: c.llm is None or c.llm.temperature is None or _temperature(c.llm.temperature),
        "llm.temperature must be a number from 0 to 2",
    ),
    (lambda c: _positive(c.agent.timeout_s), "agent.timeout_s must be positive"),
    (lambda c: _positive(c.run.cache_ttl_hours), "run.cache_ttl_hours must be positive"),
)


class ConfigError(ValueError):
    """Invalid project configuration or an unreadable project file."""


@dataclass(frozen=True)
class AgentConfig:
    entry: str = ""
    cmd: str = ""
    description: str = ""
    example_input: dict = field(default_factory=dict)
    timeout_s: float = 60


@dataclass(frozen=True)
class PromptsConfig:
    files: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class CasesConfig:
    paths: list[Path] = field(default_factory=lambda: ["cases/*.jsonl"])


@dataclass(frozen=True)
class ScoringConfig:
    mode: str = "judge"
    score: str = ""
    criteria: str = ""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float | None = None  # omitted unless set; many current models reject it


@dataclass(frozen=True)
class RunConfig:
    samples: int = 2
    max_calls: int = 400
    cache_ttl_hours: float = 24
    agent_version: str = ""
    min_pairs: int = 20
    redact: str = ""


@dataclass(frozen=True)
class Config:
    root: Path
    agent: AgentConfig
    prompts: PromptsConfig
    cases: CasesConfig
    scoring: ScoringConfig
    llm: LLMConfig | None
    run: RunConfig


SECTIONS = {
    "agent": AgentConfig,
    "prompts": PromptsConfig,
    "cases": CasesConfig,
    "scoring": ScoringConfig,
    "llm": LLMConfig,
    "run": RunConfig,
}


def load_config(path: Path) -> Config:
    """Read TOML into frozen values; paths are relative to the TOML file, not cwd."""
    data = _read_config(path)
    config = _config(data, path.resolve().parent)
    _validate(config)
    return _resolved(config)


def _read_config(path: Path) -> dict:
    with errors_as(
        ConfigError,
        {
            OSError: lambda error: f"could not read config {path}: {error}",
            ValueError: lambda error: f"invalid config {path}: {error}",
        },
    ):
        return tomllib.loads(path.read_text(encoding="utf-8"))


def _config(data: dict, root: Path) -> Config:
    values = {name: _section(name, kind, data.get(name, {})) for name, kind in SECTIONS.items()}
    if "llm" not in data:
        values["llm"] = None
    return Config(root=root, **values)


def _section(name, kind, value):
    with errors_as(ConfigError, {TypeError: lambda error: f"invalid {name}: {error}"}):
        return kind(**value)


def _validate(config: Config) -> None:
    for predicate, message in RULES:
        if not predicate(config):
            raise ConfigError(message)


def _path_list(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _text_field(config, key) -> bool:
    section, field = key.split(".")
    value = getattr(config, section)
    return value is None or isinstance(getattr(value, field), str)


def _positive(value) -> bool:
    return type(value) in (int, float) and 0 < value < float("inf")


def _temperature(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= 2


def _resolved(config: Config) -> Config:
    return replace(
        config,
        prompts=PromptsConfig([(config.root / path).resolve() for path in config.prompts.files]),
        cases=CasesConfig([(config.root / path).resolve() for path in config.cases.paths]),
    )
