import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from promptcheckup.data import Case, CaseError, append_cases, case_id, load_cases


def test_case_has_defaults_and_is_frozen():
    case = Case("one", {"question": "hello"})

    assert case.source == "manual"
    assert case.output is None
    with pytest.raises(FrozenInstanceError):
        case.id = "two"


def test_case_id_hashes_canonical_unicode_json():
    canonical = '{"a":{"x":1,"y":2},"z":"café"}'
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    assert case_id({"z": "café", "a": {"y": 2, "x": 1}}) == expected
    assert case_id({"a": {"x": 1, "y": 2}, "z": "café"}) == expected
    assert case_id({"z": "different", "a": {"x": 1, "y": 2}}) != expected


def test_load_cases_sorts_files_and_preserves_line_order(tmp_path):
    (tmp_path / "b.jsonl").write_text('{"id":"last","input":{}}\n', encoding="utf-8")
    (tmp_path / "a.jsonl").write_text(
        '\n{"id":"first","input":{},"source":"synthetic","output":[1]}\n'
        '  \n{"input":{"question":"café"}}\n',
        encoding="utf-8",
    )

    cases = load_cases(["b.jsonl", "a.jsonl"], root=tmp_path)

    assert cases == [
        Case("first", {}, "synthetic", [1]),
        Case(case_id({"question": "café"}), {"question": "café"}),
        Case("last", {}),
    ]


def test_load_cases_reads_overlapping_globs_once_and_ignores_directories(tmp_path):
    directory = tmp_path / "cases"
    directory.mkdir()
    (directory / "folder.jsonl").mkdir()
    (directory / "one.jsonl").write_text('{"input":{}}\n', encoding="utf-8")

    assert load_cases(["cases/*.jsonl", "cases/one.jsonl"], tmp_path) == [Case(case_id({}), {})]


def test_load_cases_rejects_each_pattern_without_files(tmp_path):
    (tmp_path / "one.jsonl").write_text('{"input":{}}\n', encoding="utf-8")
    (tmp_path / "directory.jsonl").mkdir()

    for pattern in ["missing/*.jsonl", "directory.jsonl"]:
        with pytest.raises(CaseError, match=pattern.replace("*", r"\*")):
            load_cases(["one.jsonl", pattern], tmp_path)


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("{broken", "JSON"),
        ("[]", "object"),
        ("null", "object"),
        ("42", "object"),
        ('{"id":"one"}', "input"),
        ('{"input":null}', "input"),
        ('{"input":[]}', "input"),
        ('{"input":"text"}', "input"),
        ('{"input":{},"id":null}', "id"),
        ('{"input":{},"id":1}', "id"),
        ('{"input":{},"source":"unknown"}', "source"),
        ('{"input":{},"source":null}', "source"),
        ('{"input":{},"source":[]}', "source"),
        ('{"input":{},"extra":true}', "extra"),
    ],
)
def test_load_cases_reports_invalid_line_with_location(tmp_path, line, reason):
    path = tmp_path / "bad.jsonl"
    path.write_text("\n" + line + "\n", encoding="utf-8")

    with pytest.raises(CaseError) as caught:
        load_cases(["bad.jsonl"], tmp_path)

    assert f"{path}:2" in str(caught.value)
    assert reason in str(caught.value)


@pytest.mark.parametrize("same_file", [False, True])
@pytest.mark.parametrize("explicit_id", [False, True])
def test_load_cases_reports_both_duplicate_locations(tmp_path, same_file, explicit_id):
    first = tmp_path / "a.jsonl"
    second = first if same_file else tmp_path / "b.jsonl"
    row = {"input": {"x": 1}}
    if explicit_id:
        row["id"] = "shared"
    line = json.dumps(row) + "\n"
    first.write_text(line, encoding="utf-8")
    with second.open("a", encoding="utf-8") as file:
        file.write("\n" + line)

    with pytest.raises(CaseError) as caught:
        load_cases(["*.jsonl"], tmp_path)

    assert f"{first}:1" in str(caught.value)
    assert f"{second}:{3 if same_file else 2}" in str(caught.value)
    assert "duplicate" in str(caught.value)


def test_load_cases_accepts_empty_files_and_patterns(tmp_path):
    (tmp_path / "empty.jsonl").write_text("\n  \n", encoding="utf-8")

    assert load_cases(["*.jsonl"], tmp_path) == []
    assert load_cases([], tmp_path) == []


def test_append_cases_creates_parents_appends_and_round_trips(tmp_path):
    path = tmp_path / "nested" / "cases.jsonl"
    cases = [
        Case("manual", {"text": "café\nhello"}),
        Case("recorded", {}, "recorded", {"answer": "yes"}),
        Case("synthetic", {}, "synthetic", False),
        Case("zero", {}, output=0),
        Case("empty", {}, output=""),
    ]

    append_cases(path, cases[:1])
    append_cases(path, (case for case in cases[1:]))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(cases)
    assert json.loads(lines[0]) == {"id": "manual", "input": cases[0].input, "source": "manual"}
    assert [json.loads(line)["output"] for line in lines[1:]] == [{"answer": "yes"}, False, 0, ""]
    assert load_cases(["nested/*.jsonl"], tmp_path) == cases


def test_append_cases_does_not_write_partial_unserializable_record(tmp_path):
    path = tmp_path / "cases.jsonl"
    append_cases(path, [Case("valid", {})])
    before = path.read_bytes()

    with pytest.raises(TypeError):
        append_cases(path, [Case("invalid", {}, output=object())])

    assert path.read_bytes() == before
