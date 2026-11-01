from dataclasses import FrozenInstanceError

import pytest

from promptcheckup.cli.config import ConfigError, load_config

SCORE_CONFIG = """
[agent]
entry = "agent:run"
[prompts]
files = ["prompts/system.md"]
[scoring]
mode = "score"
score = "agent:score"
"""
JUDGE_CONFIG = (
    SCORE_CONFIG.replace('mode = "score"', 'mode = "judge"')
    + """
criteria = "Be correct."
[llm]
base_url = "http://localhost:1234/v1"
model = "local"
"""
)


def write_config(tmp_path, text):
    path = tmp_path / "promptcheckup.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_holdout_defaults_to_half_and_accepts_valid_share(tmp_path):
    assert load_config(write_config(tmp_path, SCORE_CONFIG)).run.holdout == 0.5
    config = load_config(write_config(tmp_path, SCORE_CONFIG + "\n[run]\nholdout = 0.7\n"))
    assert config.run.holdout == 0.7


@pytest.mark.parametrize("value", ["0", "1", "-0.1", "true", '"0.5"', "nan", "inf"])
def test_holdout_rejects_invalid_share(tmp_path, value):
    with pytest.raises(ConfigError, match="run.holdout must be between 0 and 1"):
        load_config(write_config(tmp_path, SCORE_CONFIG + f"\n[run]\nholdout = {value}\n"))


def test_valid_config_fills_defaults_and_is_frozen(tmp_path):
    config = load_config(write_config(tmp_path, SCORE_CONFIG))

    assert config.agent.timeout_s == 60
    assert config.agent.cmd == ""
    assert config.agent.description == ""
    assert config.agent.example_input == {}
    assert config.run.samples == 2
    assert config.run.max_calls == 400
    assert config.run.cache_ttl_hours == 24
    assert config.run.agent_version == config.run.redact == ""
    assert config.run.min_pairs == 20
    assert config.llm is None
    with pytest.raises(FrozenInstanceError):
        config.run.samples = 4


def test_relative_paths_resolve_against_config_directory(tmp_path):
    config = load_config(write_config(tmp_path, SCORE_CONFIG))

    assert config.root == tmp_path
    assert config.prompts.files == [tmp_path / "prompts/system.md"]
    assert config.cases.paths == [tmp_path / "cases/*.jsonl"]


def test_judge_config_loads_llm_defaults(tmp_path):
    config = load_config(write_config(tmp_path, JUDGE_CONFIG))

    assert config.scoring.criteria == "Be correct."
    assert config.llm.api_key_env == "OPENAI_API_KEY"
    assert config.llm.temperature is None


@pytest.mark.parametrize("value", ["-0.1", "2.5", "true", '"0"', "nan"])
def test_llm_temperature_must_be_a_number_from_0_to_2(tmp_path, value):
    text = JUDGE_CONFIG + f"temperature = {value}\n"

    with pytest.raises(ConfigError, match="llm.temperature must be a number from 0 to 2"):
        load_config(write_config(tmp_path, text))


def test_llm_temperature_accepts_zero(tmp_path):
    config = load_config(write_config(tmp_path, JUDGE_CONFIG + "temperature = 0\n"))

    assert config.llm.temperature == 0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            SCORE_CONFIG.replace('entry = "agent:run"', ""),
            "exactly one of agent.entry or agent.cmd is required",
        ),
        (
            SCORE_CONFIG.replace(
                'entry = "agent:run"', 'entry = "agent:run"\ncmd = "python agent.py"'
            ),
            "exactly one of agent.entry or agent.cmd is required",
        ),
        (
            SCORE_CONFIG.replace('mode = "score"', 'mode = "other"'),
            'scoring.mode must be "score" or "judge"',
        ),
        (
            SCORE_CONFIG.replace('score = "agent:score"', ""),
            'scoring.score is required when mode = "score"',
        ),
        (
            JUDGE_CONFIG.replace('criteria = "Be correct."', ""),
            'scoring.criteria is required when mode = "judge"',
        ),
        (JUDGE_CONFIG.split("[llm]")[0], 'llm is required when mode = "judge"'),
        (
            JUDGE_CONFIG.replace('base_url = "http://localhost:1234/v1"', ""),
            'llm.base_url is required when mode = "judge"',
        ),
        (JUDGE_CONFIG.replace('model = "local"', ""), 'llm.model is required when mode = "judge"'),
        (
            SCORE_CONFIG.replace('["prompts/system.md"]', "[]"),
            "prompts.files must be a non-empty list of paths",
        ),
        (
            SCORE_CONFIG.replace('["prompts/system.md"]', '"prompts/system.md"'),
            "prompts.files must be a non-empty list of paths",
        ),
        (SCORE_CONFIG + "\n[run]\nsamples = 0\n", "run.samples must be at least 1"),
        (
            JUDGE_CONFIG + "\n[run]\nsamples = 1\n",
            'run.samples must be at least 2 when mode = "judge"',
        ),
        (SCORE_CONFIG + "\n[run]\nmin_pairs = 4\n", "run.min_pairs must be at least 5"),
    ],
)
def test_validation_rules_have_exact_messages(tmp_path, text, message):
    with pytest.raises(ConfigError) as caught:
        load_config(write_config(tmp_path, text))

    assert str(caught.value) == message


@pytest.mark.parametrize("text", ["[agent", "agent = 4", SCORE_CONFIG + "[run]\nsamples = 'two'"])
def test_invalid_toml_and_shapes_are_config_errors(tmp_path, text):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, text))


def test_missing_config_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="could not read config"):
        load_config(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ('entry = "agent:run"', "entry = 3", "agent.entry"),
        ('score = "agent:score"', "score = true", "scoring.score"),
        ('criteria = "Be correct."', 'criteria = ["correct"]', "scoring.criteria"),
        ('model = "local"', "model = 7", "llm.model"),
        ('base_url = "http://localhost:1234/v1"', "base_url = 3", "llm.base_url"),
    ],
)
def test_config_string_fields_reject_wrong_types(tmp_path, old, new, key):
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, JUDGE_CONFIG.replace(old, new)))


@pytest.mark.parametrize(
    ("setting", "key"),
    [
        ("max_calls = -1", "run.max_calls"),
        ("max_calls = true", "run.max_calls"),
        ("samples = true", "run.samples"),
        ("min_pairs = 5.5", "run.min_pairs"),
        ("cache_ttl_hours = nan", "run.cache_ttl_hours"),
    ],
)
def test_config_invalid_numeric_settings_are_named(tmp_path, setting, key):
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, SCORE_CONFIG + f"\n[run]\n{setting}\n"))


def test_config_requires_unique_resolved_prompt_paths(tmp_path):
    text = SCORE_CONFIG.replace('["prompts/system.md"]', '["system.md", "./system.md"]')
    with pytest.raises(ConfigError, match="prompts.files must contain unique paths"):
        load_config(write_config(tmp_path, text))


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ('entry = "agent:run"', 'cmd = "   "', "agent.cmd"),
        ('criteria = "Be correct."', 'criteria = "   "', "scoring.criteria"),
        ('model = "local"', 'model = "   "', "llm.model"),
    ],
)
def test_required_text_cannot_be_only_whitespace(tmp_path, old, new, key):
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path, JUDGE_CONFIG.replace(old, new)))
