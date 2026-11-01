# Plain Python

From a checkout with promptcheckup installed, run `sh examples/connect/plain/first-gate.sh`.
It checks the wiring and compares the two greetings over 20 manual toy cases. Expect
`B is better:` and a `result.json` in this directory. Replace the toy cases and score
with representative tasks and a meaningful outcome before judging your own agent.

`before.py` reads a file itself; `after.py` is the three-line adapter that accepts
prompt text. `recording.py` records a real call with its top-level email field removed.
Run it from this directory with `python recording.py`.

See [Connect your agent](../../../docs/connect-your-agent.md).
