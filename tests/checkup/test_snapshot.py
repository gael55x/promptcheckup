import json

import pytest

from promptcheckup.checkup.snapshot import load_latest_snapshot, load_snapshot, save_snapshot
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


def dated_snapshot(directory, key, created_at):
    save_snapshot(directory, key, [Run("c1", "A", 0, 1)], "v1")
    path = directory / f".promptcheckup/snapshots/{key}.json"
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "created_at": created_at}))


def test_latest_snapshot_uses_created_at_not_filename_or_write_order(tmp_path):
    dated_snapshot(tmp_path, "a", "2026-10-02T00:00:00+00:00")
    dated_snapshot(tmp_path, "z", "2026-10-01T00:00:00+00:00")
    assert load_latest_snapshot(tmp_path, excluding="current") == (
        "a",
        load_snapshot(tmp_path, "a"),
    )


def test_latest_snapshot_excludes_current_hash(tmp_path):
    dated_snapshot(tmp_path, "old", "2026-10-01T00:00:00+00:00")
    dated_snapshot(tmp_path, "current", "2026-10-02T00:00:00+00:00")
    assert load_latest_snapshot(tmp_path, excluding="current")[0] == "old"


@pytest.mark.parametrize("damage", ["broken", "{}", '{"created_at": "bad"}', "[]"])
def test_latest_snapshot_skips_damaged_files(tmp_path, damage):
    dated_snapshot(tmp_path, "old", "2026-10-01T00:00:00+00:00")
    (tmp_path / ".promptcheckup/snapshots/damaged.json").write_text(damage)
    assert load_latest_snapshot(tmp_path, excluding="current")[0] == "old"


def test_latest_snapshot_compares_timestamp_offsets(tmp_path):
    dated_snapshot(tmp_path, "newer", "2026-10-01T23:00:00+00:00")
    dated_snapshot(tmp_path, "older", "2026-10-02T00:00:00+02:00")
    assert load_latest_snapshot(tmp_path, excluding="current")[0] == "newer"


def test_latest_snapshot_is_none_without_eligible_evidence(tmp_path):
    assert load_latest_snapshot(tmp_path, excluding="current") is None
    dated_snapshot(tmp_path, "current", "2026-10-01T00:00:00+00:00")
    assert load_latest_snapshot(tmp_path, excluding="current") is None
    (tmp_path / ".promptcheckup/snapshots/damaged.json").write_text("broken")
    assert load_latest_snapshot(tmp_path, excluding="current") is None
