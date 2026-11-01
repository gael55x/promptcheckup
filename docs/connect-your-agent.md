# Connect your agent

Install with Python 3.11 or later: `python -m pip install git+https://github.com/gael55x/promptcheckup`.
The package is not on PyPI yet. It needs no runtime dependencies.

For a first gate without a provider account, use the [plain example](../examples/connect/plain/).
After installing, run `sh examples/connect/plain/first-gate.sh` from a clone of this repository. It runs doctor, compares
an intentionally wrong greeting with the correct one over 20 manual cases, and writes
`result.json`. Expect `B is better:`. Those toy cases prove the wiring; replace them
with representative tasks and a meaningful score before judging your own agent.

[examples/connect/plain/first-gate.sh](../examples/connect/plain/first-gate.sh)

```sh
#!/bin/sh
set -eu
cd "$(dirname "$0")"
python -m promptcheckup.cli.main doctor --no-llm
python -m promptcheckup.cli.main gate --old old-system.md --new prompts/system.md --yes --json result.json
```

## 1. The one contract

A Python agent exposes `def run(case: dict, prompts: dict[str, str]) -> Any`.
It receives one case's input and the complete prompt mapping, and returns a JSON-serializable
value. Use the supplied prompt text on every call. The gate passes old and new text
under the same names; reopening the files would make both runs use the new prompt.

Prompt names are the paths in `prompts.files`, relative to the directory containing
`promptcheckup.toml`, such as `prompts/system.md`. Paths are normalized by the loader;
doctor prints the exact keys your agent receives. The working directory and Python
import path also resolve from that config directory.

A command agent receives the same data as one JSON object on stdin:

[examples/connect/node_cmd/input.json](../examples/connect/node_cmd/input.json)

```json
{"case": {"question": "Ada"}, "prompts": {"prompts/system.md": "Hello,"}}
```

It writes one JSON value on stdout, such as this response. Send logs to stderr.

[examples/connect/node_cmd/output.json](../examples/connect/node_cmd/output.json)

```json
{"answer":"Hello, Ada!"}
```

Set `agent.cmd` instead of `agent.entry`. The command is split into arguments and run
without a shell; it must read stdin itself. Both adapters use `agent.timeout_s`.

## 2. If your agent reads its prompt from a file today

Before, the function opens the prompt on every call:

[examples/connect/plain/before.py](../examples/connect/plain/before.py)

```python
from pathlib import Path

from plain_agent import answer


def run(case: dict) -> dict:
    prompt = Path("prompts/system.md").read_text(encoding="utf-8")
    return answer(case["question"], prompt)
```

After, the adapter is three lines: accept `prompts`, select the text, and pass it to
your existing agent. Keep your agent loop and tools; change where its prompt comes from.

[examples/connect/plain/after.py](../examples/connect/plain/after.py)

```python
from typing import Any

from plain_agent import answer


def run(case: dict, prompts: dict[str, str]) -> Any:
    prompt = prompts["prompts/system.md"]
    return answer(case["question"], prompt)
```

## 3. Examples

Each directory includes its agent, prompts, cases, README and `promptcheckup.toml`.
Copy the complete directory to try it. Point your own config at your existing agent
when adapting it. No SDK is added to promptcheckup's dependencies.

### Plain Python

The [plain example](../examples/connect/plain/) needs only Python. Its score is 1 for
the expected greeting and 0 otherwise; higher is better.

[examples/connect/plain/plain_agent.py](../examples/connect/plain/plain_agent.py)

```python
from typing import Any


def answer(question: str, prompt: str) -> dict:
    return {"answer": f"{prompt.strip()} {question}!"}


def run(case: dict, prompts: dict[str, str]) -> Any:
    prompt = prompts["prompts/system.md"]
    return answer(case["question"], prompt)


def score(case: dict, output: Any) -> float:
    return float(isinstance(output, dict) and output.get("answer") == f"Hello, {case['question']}!")
```

### OpenAI Python SDK

Use this in an application that already has the OpenAI Python SDK installed.
`OpenAI()` reads the SDK's expected `OPENAI_API_KEY` environment variable.
The system message comes from `prompts`, and the adapter returns text, not the SDK's
response object. See the [official chat completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create).

[examples/connect/openai_sdk/openai_agent.py](../examples/connect/openai_sdk/openai_agent.py)

```python
from openai import OpenAI


def run(case: dict, prompts: dict[str, str]) -> str:
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": prompts["prompts/system.md"]},
            {"role": "user", "content": case["question"]},
        ],
    )
    return response.choices[0].message.content
```

The [OpenAI config](../examples/connect/openai_sdk/promptcheckup.toml) uses `gpt-5-mini`
for the judge too. Run `promptcheckup doctor` from that example directory.

### Anthropic Python SDK

Use this in an application that already has the Anthropic Python SDK installed.
`Anthropic()` reads `ANTHROPIC_API_KEY`. The messages API takes the system prompt in
its top-level `system` argument. See the [official messages reference](https://platform.claude.com/docs/en/api/messages/create).

[examples/connect/anthropic_sdk/anthropic_agent.py](../examples/connect/anthropic_sdk/anthropic_agent.py)

```python
from anthropic import Anthropic


def run(case: dict, prompts: dict[str, str]) -> str:
    client = Anthropic()
    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=prompts["prompts/system.md"],
        messages=[{"role": "user", "content": case["question"]}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
```

The [Anthropic config](../examples/connect/anthropic_sdk/promptcheckup.toml) uses
`claude-haiku-5-5` for the judge too. Run `promptcheckup doctor` from that directory.
Both SDK examples are compiled and their configs validated in tests without importing
the SDKs or contacting providers. Their three manual sample cases check wiring;
add representative cases before trusting a gate.

### Node.js command agent

This example uses only Node's standard library. Its [config](../examples/connect/node_cmd/promptcheckup.toml)
sets `cmd = "node agent.mjs"`. From that directory, run `node agent.mjs < input.json`
to see the protocol, then `promptcheckup doctor --no-llm` to check the whole connection.
The [score function](../examples/connect/node_cmd/node_score.py) is Python even though
the agent is JavaScript.

[examples/connect/node_cmd/agent.mjs](../examples/connect/node_cmd/agent.mjs)

```javascript
import { readFileSync } from "node:fs";

const { case: input, prompts } = JSON.parse(readFileSync(0, "utf8"));
const output = { answer: `${prompts["prompts/system.md"].trim()} ${input.question}!` };
process.stdout.write(JSON.stringify(output) + "\n");
```

## 4. Record real cases

Decorate your production entry point, leaving the replay adapter undecorated so replay
does not record itself. Reading current files in that production wrapper is fine;
the replay adapter still receives its prompts explicitly.

[examples/connect/plain/recording.py](../examples/connect/plain/recording.py)

```python
from pathlib import Path

from plain_agent import run

from promptcheckup import record


def redact(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "email"}


@record("cases/recorded.jsonl", redact=redact)
def handle(case: dict) -> dict:
    prompts = {"prompts/system.md": Path("prompts/system.md").read_text(encoding="utf-8")}
    return run(case, prompts)


if __name__ == "__main__":
    print(handle({"question": "Ada", "email": "ada@example.invalid"}))
```

Run `python recording.py` from the plain example directory. `@record` appends input and
output to `cases/recorded.jsonl`, labelled `recorded`. The redactor runs on copies before
writing either value. This example removes only a top-level email field: adapt it to
all sensitive fields in your own data, including free text. Set `run.redact` to your
`module:function` as well when inputs or outputs need redaction before judge calls
and reports. Doctor respects it for its preview and judge inputs.

## 5. Choose how answers are scored

Use a score when correctness is computable. `score(case, output)` must handle any
agent output and return a finite number; higher is better. The plain example's
function above is wired by this complete config:

[examples/connect/plain/promptcheckup.toml](../examples/connect/plain/promptcheckup.toml)

```toml
[agent]
entry = "plain_agent:run"
description = "Greets a person using the supplied greeting prompt"
example_input = { question = "Ada" }
timeout_s = 30

[prompts]
files = ["prompts/system.md"]

[cases]
paths = ["cases/*.jsonl"]

[scoring]
mode = "score"
score = "plain_agent:score"

[run]
samples = 2
min_pairs = 20
max_calls = 400
```

Otherwise choose `mode = "judge"` and write concrete `criteria`: what would make an
answer pass or fail? The judge compares both answers against those criteria, swaps
order, and checks itself before the gate trusts a verdict. Doctor makes just one
pass/fail grade to check connectivity; it does not certify judge reliability or
agent quality. An `llm` line can be `ok` with `passed=False`: the connection worked
but that answer failed the criterion.

Here is a complete judge config using Claude through Anthropic's OpenAI-compatible
endpoint, `base_url = "https://api.anthropic.com/v1"`:

[examples/connect/anthropic_sdk/promptcheckup.toml](../examples/connect/anthropic_sdk/promptcheckup.toml)

```toml
[agent]
entry = "anthropic_agent:run"
description = "Answers questions through the Anthropic messages API"
example_input = { question = "What is 2 + 2?" }
timeout_s = 60

[prompts]
files = ["prompts/system.md"]

[cases]
paths = ["cases/*.jsonl"]

[scoring]
mode = "judge"
criteria = "Answer the question accurately and directly. Do not invent facts."

[llm]
base_url = "https://api.anthropic.com/v1"
model = "claude-haiku-5-5"
api_key_env = "ANTHROPIC_API_KEY"

[run]
samples = 2
min_pairs = 20
max_calls = 400
```

The client sends no temperature by default, because current Claude and OpenAI reasoning
models reject it. Leave `llm.temperature` unset unless your model supports it. Judges
may reason before the JSON verdict; promptcheckup accepts a final JSON object, including
one inside a final JSON fence. Invalid verdicts are errors, never guessed from prose.
See [Anthropic's compatibility documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk).

**Which judge model?** Pick one that is at least as capable as the agent's own model on your task. In our own check (a 30-case billing-support gate, with a harder variant that cited a wrong policy for some questions), Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5 all reached the right verdict, passed the self-check, and caught every wrong citation. Sonnet and Opus were the most decisive. Haiku hedged on a few cases, which widens the confidence interval, so plan on more cases with a smaller judge. One test is not a benchmark: the self-check runs on every gate and tells you when a judge is unreliable for your criteria.
A weekly check-up needs an `[llm]` table even when the gate uses a score function.

## 6. Check the wiring

Run `promptcheckup doctor` from your project, or pass `--config path/to/promptcheckup.toml`.
It prints its planned cost first and makes two real agent calls plus at most one judge
call, without a confirmation prompt. `--no-llm` skips the judge only; an SDK agent still
calls its provider twice. Score mode without `[llm]` needs no judge; with `[llm]`, doctor
checks that connection once and includes it in the cost line.

The plain example with `--no-llm` prints this (elapsed time varies):

[examples/connect/doctor-output.txt](../examples/connect/doctor-output.txt)

```text
doctor makes 2 agent calls and 0 judge calls
ok    config     loaded promptcheckup.toml
ok    prompts    prompts/system.md
ok    cases      20 cases, 0% synthetic
ok    agent      plain_agent:run ran in 0.001s; output: {"answer": "Hello, Ada!"}
ok    prompt-use the output changed with the supplied prompts
ok    score      plain_agent:score returned 1.0
skip  llm        skipped by --no-llm
ok    budget     gate: 80 calls; check-up: 122 calls
```

Exit 0 means no checks failed; warnings are allowed. Exit 3 means a failure. Every
check has one line, and errors also produce one stderr summary without a traceback.
Dependent checks say `skip` and do no work; independent checks still run. Key checks
show only the environment variable's name and whether it is set, never its value.

| Warning | What to do |
|---|---|
| No cases yet | Run `promptcheckup checkup` to generate 20 labelled starter cases, or capture real calls with `@record`. Doctor uses `agent.example_input` and writes no cases. |
| Fewer than `run.min_pairs` | Add cases: every gate verdict remains “not sure yet” below that minimum. The line also shows the synthetic share; replace starter data with representative real inputs. |
| Output did not change | Check that your agent uses the `prompts` argument instead of reading files. The probe replaces every prompt's text in memory for one call. Identical output is a strong clue, not proof: a stochastic agent may change its answer for unrelated reasons. If the agent fails on the probe text, doctor warns instead: that shows it reads the supplied prompts, and real runs never send the probe. |
| Gate or check-up exceeds `max_calls` | Raise the budget or reduce cases/samples deliberately. The estimate uses a cold cache, and check-up includes starter generation when needed. Its verdict would stop at “not sure yet” when the cap is reached. |

Doctor always attempts its fixed diagnostic calls even when `run.max_calls` is smaller;
that setting caps gate/check-up runs. A missing case file is allowed at this stage;
an existing empty or malformed case file is a failure that needs fixing.
The Python adapter enforces a waiting deadline but cannot kill a running thread;
use a command adapter when you need process termination at timeout.

## 7. Run it in CI

Run `promptcheckup init`. It writes these two workflows and never overwrites existing files:

- `.github/workflows/promptcheckup-gate.yml` compares a prompt PR with its base and comments with the report.
- `.github/workflows/promptcheckup-checkup.yml` runs weekly (or manually), preserves snapshots,
  updates a check-up issue, and opens a proposal PR only for an edit that passed the gate.

Match the prompt path filters and snapshot hash paths to your `prompts.files`. Make sure
CI installs your agent's existing dependencies and has the environment named in your
config available. The repository must allow GitHub Actions to create pull requests
(Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create
and approve pull requests) for the weekly proposal PR. Proposals are never merged
automatically. Review the gate's verdict: the shipped template posts reports with
`continue-on-error`, so a green workflow alone is not evidence of a better prompt.

## 8. For coding agents

Paste this block into your existing `AGENTS.md` or `CLAUDE.md`:

[examples/connect/coding-agent-instructions.md](../examples/connect/coding-agent-instructions.md)

```markdown
After editing prompts, run `promptcheckup doctor` and fix every failure.
Resolve or explain warnings, especially an agent that ignores the `prompts` argument.
Then run `promptcheckup gate --old-ref origin/main --yes --json result.json`.
Report the gate report's first line verbatim and its exit code: 0 better, 1 worse,
2 not sure yet, 3 configuration/runtime error. Never describe "not sure yet" as a pass.
Keep the old and new prompts on the same cases. Do not merge automatically.
```
