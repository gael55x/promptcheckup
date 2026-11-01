import json
import warnings
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from promptcheckup import record
from promptcheckup.data import Case, case_id, load_cases
from promptcheckup.data import record as data_record


def test_duplicate_recordings_remain_loadable_across_decorators(tmp_path):
    path = tmp_path / "recorded.jsonl"
    calls = []

    def handle(case):
        calls.append(case)
        return {"answer": len(calls)}

    first = record(path)(handle)
    first({"question": "same"})
    first({"question": "same"})
    second = record(path)(handle)
    second({"question": "same"})
    assert len(calls) == 3
    assert len(path.read_text().splitlines()) == 1
    assert len(load_cases([path.name], tmp_path)) == 1


def test_record_captures_input_before_function_mutates_it(tmp_path):
    path = tmp_path / "recorded.jsonl"

    @record(path)
    def handle(case):
        case["messages"].append("answer")
        return case["messages"]

    original = {"messages": ["question"]}
    assert handle(original) == ["question", "answer"]
    saved = load_cases([path.name], tmp_path)
    assert saved[0].input == {"messages": ["question"]}
    assert saved[0].id == case_id({"messages": ["question"]})


def test_record_preserves_metadata_arguments_and_original_output(tmp_path):
    path = tmp_path / "nested" / "recorded.jsonl"
    output = {"answer": "yes"}

    @record(str(path))
    def handle(case: dict, suffix: str, *, enabled: bool = False) -> dict:
        """Handle a production request."""
        assert case == {"question": "hello"}
        assert suffix == "!"
        assert enabled
        return output

    result = handle({"question": "hello"}, "!", enabled=True)

    assert record is data_record
    assert result is output
    assert handle.__name__ == "handle"
    assert handle.__doc__ == "Handle a production request."
    assert handle.__annotations__ == handle.__wrapped__.__annotations__
    assert load_cases(["nested/*.jsonl"], tmp_path) == [
        Case(case_id({"question": "hello"}), {"question": "hello"}, "recorded", output)
    ]


def test_record_redacts_input_and_output_before_writing(tmp_path):
    path = tmp_path / "recorded.jsonl"
    case_input = {"question": "hello", "secret": "private input"}
    output = {"answer": "yes", "secret": "private output"}
    seen = []

    def redact(value):
        seen.append(value)
        return {key: item for key, item in value.items() if key != "secret"}

    @record(path, redact=redact)
    def handle(case):
        return output

    assert handle(case_input) is output
    assert seen == [case_input, output]
    assert "secret" in output
    assert "private" not in path.read_text(encoding="utf-8")
    assert load_cases(["*.jsonl"], tmp_path) == [
        Case(case_id({"question": "hello"}), {"question": "hello"}, "recorded", {"answer": "yes"})
    ]


def test_record_keeps_original_values_when_redactor_mutates_in_place(tmp_path):
    path = tmp_path / "recorded.jsonl"
    case_input = {"nested": {"secret": "private input", "safe": "question"}}
    output = {"nested": {"secret": "private output", "safe": "answer"}}

    def redact(value):
        del value["nested"]["secret"]
        return value

    @record(path, redact=redact)
    def handle(case):
        return output

    assert handle(case_input) is output
    assert case_input["nested"]["secret"] == "private input"
    assert output["nested"]["secret"] == "private output"
    assert load_cases(["*.jsonl"], tmp_path) == [
        Case(
            case_id({"nested": {"safe": "question"}}),
            {"nested": {"safe": "question"}},
            "recorded",
            {"nested": {"safe": "answer"}},
        )
    ]


def test_record_can_redact_unserializable_values_into_json(tmp_path):
    path = tmp_path / "recorded.jsonl"
    output = object()

    def redact(value):
        return {"safe": True} if isinstance(value, dict) else "safe output"

    @record(path, redact=redact)
    def handle(case):
        return output

    assert handle({"secret": object()}) is output
    assert load_cases(["*.jsonl"], tmp_path) == [
        Case(case_id({"safe": True}), {"safe": True}, "recorded", "safe output")
    ]


def test_record_reraises_same_production_exception_and_writes_nothing(tmp_path):
    path = tmp_path / "recorded.jsonl"
    error = ValueError("production failed")

    def redact(value):
        pytest.fail("redaction must not run after a failed production call")

    @record(path, redact=redact)
    def handle(case):
        raise error

    with pytest.raises(ValueError) as caught:
        handle({})

    assert caught.value is error
    assert not path.exists()


@pytest.mark.parametrize("field", ["input", "output"])
@pytest.mark.parametrize("kind", ["object", "circular"])
def test_record_warns_and_skips_unserializable_data(tmp_path, field, kind):
    path = tmp_path / "recorded.jsonl"
    bad = object() if kind == "object" else []
    if kind == "circular":
        bad.append(bad)
    case_input = {"bad": bad} if field == "input" else {}
    output = bad if field == "output" else {"answer": "yes"}

    @record(path)
    def handle(case):
        return output

    with pytest.warns(RuntimeWarning, match="record"):
        assert handle(case_input) is output

    assert not path.exists() or path.read_bytes() == b""


@pytest.mark.parametrize("blocked_parent", [False, True])
def test_record_warns_on_write_failure_and_returns_output(tmp_path, blocked_parent):
    path = tmp_path / "recorded.jsonl"
    if blocked_parent:
        path.write_text("existing file", encoding="utf-8")
        path = path / "child.jsonl"
    else:
        path.mkdir()
    output = {"answer": "yes"}

    @record(path)
    def handle(case):
        return output

    with pytest.warns(RuntimeWarning, match="record") as caught:
        assert handle({}) is output

    assert str(path) in str(caught[0].message)


def test_record_warns_if_redaction_fails(tmp_path):
    path = tmp_path / "recorded.jsonl"
    output = {"answer": "yes"}

    def redact(value):
        raise RuntimeError("redaction failed")

    @record(path, redact=redact)
    def handle(case):
        return output

    with pytest.warns(RuntimeWarning, match="redaction failed"):
        assert handle({}) is output

    assert not path.exists()


def test_record_warns_if_redacted_input_is_not_an_object(tmp_path):
    path = tmp_path / "recorded.jsonl"

    @record(path, redact=lambda value: [])
    def handle(case):
        return "answer"

    with pytest.warns(RuntimeWarning, match="input"):
        assert handle({}) == "answer"

    assert not path.exists()


def test_record_returns_output_even_when_warnings_are_errors(tmp_path):
    output = object()

    @record(tmp_path / "recorded.jsonl")
    def handle(case):
        return output

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert handle({}) is output


def test_record_concurrent_decorators_write_complete_lines(tmp_path):
    path = tmp_path / "recorded.jsonl"
    start = Barrier(8)

    def invoke(index):
        @record(path)
        def handle(case):
            start.wait(timeout=1)
            return {"index": index, "text": "é\n" * 8192}

        return handle({"index": index})

    with ThreadPoolExecutor(max_workers=8) as executor:
        outputs = list(executor.map(invoke, range(32)))

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 32
    assert {row["input"]["index"] for row in rows} == set(range(32))
    for row in rows:
        index = row["input"]["index"]
        assert row == {
            "id": case_id({"index": index}),
            "input": {"index": index},
            "source": "recorded",
            "output": outputs[index],
        }


def test_recording_warning_points_at_the_production_call_site(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")

    @record(blocked / "cases.jsonl")
    def handle(case):
        return "ok"

    with pytest.warns(RuntimeWarning) as caught:
        handle({"q": 1})  # the warning should point at this line, not at library code

    assert caught[0].filename == __file__
