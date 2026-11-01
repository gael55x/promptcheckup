import json

import pytest

from promptcheckup.cli.config import ConfigError, load_config
from promptcheckup.cli.main import main
from promptcheckup.data import Case, append_cases
from tests.cli.test_config import SCORE_CONFIG, write_config


def test_run_confidence_defaults_to_point_ninety_five(tmp_path):
    assert load_config(write_config(tmp_path, SCORE_CONFIG)).run.confidence == 0.95


@pytest.mark.parametrize("value", ['"0.95"', "true", "0.499", "1", "nan"])
def test_run_confidence_validation_has_exact_message(tmp_path, value):
    with pytest.raises(ConfigError) as error:
        load_config(write_config(tmp_path, SCORE_CONFIG + f"\n[run]\nconfidence = {value}\n"))
    assert str(error.value) == "run.confidence must be between 0.5 and 0.999"


@pytest.mark.parametrize("value", [0.5, 0.9, 0.999])
def test_run_confidence_accepts_supported_range(tmp_path, value):
    assert (
        load_config(
            write_config(tmp_path, SCORE_CONFIG + f"\n[run]\nconfidence = {value}\n")
        ).run.confidence
        == value
    )


def test_gate_honours_confidence_for_borderline_effect_and_records_trials(tmp_path, capsys):
    module = "borderline_" + tmp_path.name.replace("-", "_")
    (tmp_path / f"{module}.py").write_text(
        'def run(case, prompts):\n    return float(prompts["system"]) * case["effect"]\n'
        "def score(case, output):\n    return output\n"
    )
    base = (
        f'[agent]\nentry = "{module}:run"\n[prompts]\nfiles = ["system"]\n'
        f'[scoring]\nmode = "score"\nscore = "{module}:score"\n'
        '[cases]\npaths = ["cases.jsonl"]\n[run]\nsamples = 1\n'
    )
    append_cases(tmp_path / "cases.jsonl", [Case(f"c{i}", {"effect": i - 6.5}) for i in range(20)])
    (tmp_path / "old").write_text("0")
    (tmp_path / "new").write_text("1")
    config = write_config(tmp_path, base + "confidence = 0.9\n")
    argv = [
        "gate",
        "--config",
        str(config),
        "--old",
        str(tmp_path / "old"),
        "--new",
        str(tmp_path / "new"),
        "--yes",
    ]
    assert main(argv) == 0
    assert "90% CI" in capsys.readouterr().out
    config.write_text(base + "confidence = 0.99\n")
    assert main(argv) == 2
    assert "99% CI" in capsys.readouterr().out
    (tmp_path / "new").write_text("1.01")
    assert main(argv) == 2
    assert "Trials on these cases: 2 distinct variants" in capsys.readouterr().out
    rows = [
        json.loads(line)
        for line in (tmp_path / ".promptcheckup/trials.jsonl").read_text().splitlines()
    ]
    assert [row["confidence"] for row in rows] == [0.9, 0.99, 0.99]
    assert [row["decision"] for row in rows] == ["better", "inconclusive", "inconclusive"]
