from promptcheckup.testing import assert_no_lookahead


def build_input(history, decision_time):
    return [row for row in history if row["date"] <= decision_time]


def test_no_lookahead():
    history = [
        {"date": "2026-10-01", "price": 10.0},
        {"date": "2026-10-02", "price": 12.0},
        {"date": "2026-10-03", "price": 11.0},
    ]
    assert_no_lookahead(build_input, history, [row["date"] for row in history], time_key="date")
