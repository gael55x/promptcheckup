# Test-suite helpers

With promptcheckup and pytest available, run `pytest examples/connect/testing` from
the repository root. Both examples use only deterministic Python functions.

- `test_prompt_change.py` gates a greeting prompt with a computable score.
- `test_no_lookahead.py` checks that a history view uses only available rows.

These files are also the examples in the [connection guide](../../../docs/connect-your-agent.md#gate-prompt-changes-in-your-test-suite).
