# Support bot: a first judge gate

A small SaaS billing bot answers the same 30 questions with fixed text. The prompt
controls whether it adds a policy citation. The one-line change is:

Old (`prompts/old-system.md`):

```text
You answer customer billing questions politely.
```

New (`prompts/system.md`):

```text
You answer customer billing questions politely. Always cite the relevant policy by number.
```

From a fresh clone, use Python 3.11 or later. Install once from the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cd examples/support-bot
promptcheckup doctor --no-llm
```

That checks the bot without a key. For the hosted judge, set your provider key in the
environment variable named by `llm.api_key_env`, then run:

```bash
promptcheckup doctor
```

Doctor makes one judge call and does not ask for confirmation. The default judge is
Claude Haiku; the config includes a commented OpenAI alternative. Provider usage is
billed separately. Doctor checks connectivity, not judge reliability.

Run the gate and confirm its call plan:

```bash
promptcheckup gate --old prompts/old-system.md --new prompts/system.md --out report.md
```

Open `report.md`. These are the real hosted-judge results already saved here; a new
run may differ. These reports were produced by the earlier version 0.0.1 to check
the judges on these criteria, not to validate 1.1.0. The interval measures paired
judge preference, not customer outcomes.

| Judge / report | Verdict line | 95% CI | Self-check |
| --- | --- | --- | --- |
| [Haiku](results/report-claude-haiku-5-5.md) | B is better: 28 of 30 cases improved (95% CI +0.78 to +0.99). | +0.78 to +0.99 | reliable; consistency 100.0% |
| [Sonnet](results/report-claude-sonnet-5-5.md) | B is better: 30 of 30 cases improved (95% CI +1.00 to +1.00). | +1.00 to +1.00 | reliable; consistency 100.0% |
| [Opus](results/report-claude-opus-5-5.md) | B is better: 30 of 30 cases improved (95% CI +1.00 to +1.00). | +1.00 to +1.00 | reliable; consistency 100.0% |

The bot is deterministic and the change is planted. This demonstrates the wiring
and the judges on these criteria, not a production gain or independent verification
of the billing policies. It does not test an LLM answering customers, conversation
follow-ups, live tools, or agent memory. The self-check detects some judge failures;
it cannot certify that every answer is correct.

Next, [connect your own agent](../../docs/connect-your-agent.md) and replace the toy
questions and criteria with representative tasks and your actual policies.
