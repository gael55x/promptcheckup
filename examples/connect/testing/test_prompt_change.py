from promptcheckup.testing import assert_better


def run(case, prompts):
    return f"{prompts['system']} {case['name']}!"


def score(case, output):
    return float(output == f"Hello, {case['name']}!")


def test_prompt_change():
    cases = [{"name": f"Person {index}"} for index in range(20)]
    result = assert_better(run, cases, {"system": "Goodbye,"}, {"system": "Hello,"}, score=score)
    assert result.verdict.wins == 20
