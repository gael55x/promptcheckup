import json

import pytest

from promptcheckup.checkup.snapshot import load_snapshot, save_snapshot
from promptcheckup.runner import Run


def test_snapshot_round_trip(tmp_path):
    runs = [Run("c1", "A", 0, {"answer": 3}), Run("c2", "A", 0, error="failed")]
    save_snapshot(tmp_path, "abc", runs, "model-v1")
    result = load_snapshot(tmp_path, "abc")
    assert result.runs == runs
    assert result.agent_version == "model-v1"
    assert result.created_at
    assert json.loads((tmp_path / ".promptcheckup/snapshots/abc.json").read_text())["runs"]


def test_missing_snapshot_is_none(tmp_path):
    assert load_snapshot(tmp_path, "missing") is None


@pytest.mark.parametrize(
    "text",
    [
        "broken",
        "{}",
        "[]",
        '{"created_at": 1, "agent_version": 3}',
        '{"created_at": 1, "agent_version": "", "runs": [{}]}',
        '{"created_at": 1, "agent_version": "", "runs": [{"case_id": 2}]}',
    ],
)
def test_damaged_snapshot_is_none(tmp_path, text):
    directory = tmp_path / ".promptcheckup/snapshots"
    directory.mkdir(parents=True)
    (directory / "abc.json").write_text(text)
    assert load_snapshot(tmp_path, "abc") is None
