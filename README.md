<div align="center">

# promptcheckup

**Know whether a prompt change actually made your AI agent better.**

[![CI](https://github.com/gael55x/promptcheckup/actions/workflows/ci.yml/badge.svg)](https://github.com/gael55x/promptcheckup/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/promptcheckup.svg)](https://pypi.org/project/promptcheckup/)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB.svg?logo=python&logoColor=white)](https://github.com/gael55x/promptcheckup/blob/main/pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/gael55x/promptcheckup/blob/main/LICENSE)

promptcheckup replays the same cases through your old and new prompt,<br>
compares them case by case, and answers **better**, **worse** or **not sure yet**.

<img src="https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/naive_vs_paired.svg" alt="Bar chart: comparing prompts on different weeks picked the worse prompt 41.3% of the time; paired replay picked it 0% of the time." width="720">

[Quick start](#quick-start) · [Evidence](#evidence) · [Daily use](#daily-use) · [How it works](#how-it-works) · [Connect your agent](https://github.com/gael55x/promptcheckup/blob/main/docs/connect-your-agent.md) · [FAQ](#faq)

</div>

## Why

Most prompt changes are judged by running the new prompt for a while and comparing its average score with the old one. That mostly measures *when* each prompt ran: a rising market week, an easier batch of questions, a quiet provider update. In the simulation above, that comparison picked the **worse** prompt 41% of the time.

promptcheckup is built on one idea: **compare both prompts on identical inputs**, and only claim an improvement the data can support. It works with any agent, in any language, and needs nothing beyond the Python standard library.

## Who it is for

| You are | You use it to |
|---|---|
| **An AI or agentic engineer** | Gate every prompt pull request, catch silent model or provider regressions every week, and check prompt changes inside the test suite you already run. |
| **A fintech engineer** | Get an auditable verdict for every prompt change, with prompt hashes, call counts and a report in the pull request, and redaction before anything is stored or sent. |
| **A quant** | Stop trusting lucky backtests. The skew guard refuses "better" when a few large losses hide among small gains, the trial ledger counts how many variants you've tried, and `assert_no_lookahead` proves an agent can't see the future. |

It pays off when an agent is in real use, its prompts change, and "better" can be scored or written as clear criteria. It's the wrong tool for prompts that change every hour, for tasks with no agreed definition of a good answer, or for comparing models on general capability.

## Installation

Python 3.11 or later. No runtime dependencies.

```bash
pip install promptcheckup
```

## Quick start

```bash
promptcheckup init      # config, example cases and two GitHub workflows
```

Edit `promptcheckup.toml` to point at your agent (a Python function `run(case, prompts)`, or any command that reads JSON on stdin), your prompt files and your cases. The [Connect your agent](https://github.com/gael55x/promptcheckup/blob/main/docs/connect-your-agent.md) guide has copy-paste adapters for plain Python, the OpenAI and Anthropic SDKs, and Node.js. Then check the wiring and gate your first change:

```bash
promptcheckup doctor    # checks the wiring, one line per check
promptcheckup gate --old-ref origin/main --out report.md
```

The gate prints the planned calls and asks before spending anything (`--yes` skips the question in CI). The report's first line is the verdict:

```text
B is better: 28 of 30 cases improved (95% CI +0.78 to +0.99).
```

| Exit code | Meaning |
|---|---|
| 0 | better (`checkup`: stable) |
| 1 | worse (`checkup`: drift) |
| 2 | not sure yet |
| 3 | configuration or runtime error, with a one-line message |

Prefer the library? The statistics alone take one call:

```python
from promptcheckup import decide

verdict = decide([new - old for old, new in zip(old_scores, new_scores)])
print(verdict.decision, verdict.reason)
```

## Evidence

Every number below comes from a committed file or a command anyone can rerun.

| Risk | Without promptcheckup | With promptcheckup |
|---|---|---|
| Comparing prompts run on different weeks | picks the **worse** prompt **41.3%** of the time | **0%** with paired replay |
| No real change, crash-skewed P&L | a plain test says "better" **9–13%** of the time | **2.0–2.7%**, against a 2.5% target |
| A lucky trading sample | a win count crowns the new policy, 26 to 13 | **not sure yet** (+12.76 bp, 95% CI −11.04 to +36.57); the true edge is about zero |
| Silent provider regression | unnoticed until users complain | caught in **87.5%** of incident weeks, about 1 false alarm a year |
| Prompt merged without review | a fresh baseline hides the regression | caught in **98.5%** of incident weeks |
| A prompt that memorises its test cases | ships | `evolve` rejects it on held-out cases |
| An untested judge | trusted blindly | self-checked on every run; three hosted judges passed at **100%** consistency |

Sources: [gate benchmark](#benchmarks), [simulated year](#benchmarks), [trading example](https://github.com/gael55x/promptcheckup/tree/main/examples/trading), [evolve test](https://github.com/gael55x/promptcheckup/blob/main/tests/checkup/test_evolve.py), [judge reports](https://github.com/gael55x/promptcheckup/tree/main/examples/support-bot/results).

A full gate on 30 cases with Claude Haiku 5.5 as the judge took **180 judge calls** (58,330 input and 40,108 output tokens).

## Daily use

### Gate every prompt pull request

`promptcheckup init` writes `.github/workflows/promptcheckup-gate.yml`. On every pull request that touches a prompt file, it compares the new prompts with the base branch and comments the report on the pull request. In judge mode the judge is first checked against itself, comparing the old prompt with the old prompt, so a biased judge can't produce a verdict.

### A weekly check-up

```bash
promptcheckup checkup --out checkup.md --proposal-out proposal.md
```

Each run:
- compares this week's answers with last week's, so a silent model, tool or provider change shows up as **drift** (exit code 1). If the prompt changed since the last check-up, it compares with the previous prompt instead, so an unreviewed merge is caught too;
- groups failing cases into at most three patterns;
- proposes one small, cited prompt edit, kept only if it passes the same paired gate.

With no cases yet, the first run generates 20 labelled synthetic starter cases. The weekly workflow posts every report to one long-running issue and opens a pull request for an edit that passed. It never merges.

### Let it improve the prompt on its own

```bash
promptcheckup evolve --rounds 5 --out evolve.md
```

`evolve` repeats the proposal step: find failures, propose a small edit, gate it, keep it if it wins, and go again. It is built not to fool itself:
- **It learns and judges on different cases.** Cases are split by a fixed rule on their IDs, never by results. Proposals only see the learning cases, and every edit is gated on the held-out ones.
- **The bar rises with every extra round.** Each round must pass at `1 − 0.05 / rounds` confidence (99% for 5 rounds), so the chance of keeping any false improvement over the whole run stays near 5%.
- **It stops at the first round that doesn't pass.** It writes the final prompt files and a lineage report (each round's reasoning, cited cases, held-out statistics and diff), and never merges or deploys.

Every gated comparison is recorded in `.promptcheckup/trials.jsonl`. When several variants have been tried on the same cases, the report says how many, and how many false "better" verdicts that many tries would produce by chance. Set `run.confidence` to raise the bar everywhere.

### In your test suite

```python
from promptcheckup.testing import assert_better, assert_no_lookahead

def test_new_prompt_is_better():
    assert_better(run, cases, old_prompts, new_prompts, score=score)

def test_agent_cannot_see_the_future():
    assert_no_lookahead(build_input, history, decision_times, time_key="date")
```

`assert_better` and `assert_not_worse` run the same gate as the CLI. `assert_no_lookahead` changes every number after each decision time and fails if the agent's input changes. They raise plain `AssertionError`, so any test runner works.

### With coding agents

Add this to your `AGENTS.md` or `CLAUDE.md`, so a coding agent proves its own prompt edits:

```markdown
After editing prompts, run `promptcheckup doctor` and fix every failure.
Then run `promptcheckup gate --old-ref origin/main --yes --json result.json`.
Report the gate report's first line verbatim and its exit code: 0 better, 1 worse,
2 not sure yet, 3 configuration/runtime error. Never describe "not sure yet" as a pass.
```

## How it works

1. **Replay.** Run every case through prompt A and prompt B, back to back, so both see the same conditions.
2. **Pair.** For each case, take B's score minus A's score. Anything the two runs share cancels.
3. **Decide.** Compute a paired 95% t confidence interval of the mean difference, using an exact Student t quantile:
   - **better** if it sits entirely above zero;
   - **worse** if it sits entirely below zero;
   - **not sure yet** otherwise, with fewer than 20 cases, or when the differences are skewed toward a few large losses.

Scores come from your own `score(case, output)` function or from an LLM judge. The judge compares both answers in both orders and is checked against itself on every run. Agent outputs are redacted before they are cached, stored or sent, and untrusted data can't close its prompt block to inject instructions. The full architecture and every design decision are in [DESIGN.md](https://github.com/gael55x/promptcheckup/blob/main/DESIGN.md).

## Examples

| Example | What it shows | Needs a key |
|---|---|---|
| [Planted](https://github.com/gael55x/promptcheckup/tree/main/examples/planted) | A naive comparison picks the wrong prompt; paired replay picks the right one. | No |
| [Support bot](https://github.com/gael55x/promptcheckup/tree/main/examples/support-bot) | A five-minute judge-mode quickstart, with real reports from three hosted judges. | For a new judge run |
| [Trading](https://github.com/gael55x/promptcheckup/tree/main/examples/trading) | An LLM trader on a seeded synthetic market. Model replies are recorded, every case sees only past data (proven by a test), and cases are chosen by a fixed rule. | No |
| [Connect your agent](https://github.com/gael55x/promptcheckup/tree/main/examples/connect) | Tested adapters for plain Python, the OpenAI and Anthropic SDKs, Node.js, and the test-suite helpers. | No |

## Benchmarks

Seeded simulations of the shipped code, reproducible with the commands below.

**False "better" verdicts when nothing changed.** The target is 2.5%. A plain t-interval fails on crash-skewed P&L; the skew guard holds the rate there too.

![False "better" rate by method, noise type and case count](https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/false_positive.svg)

**Detection power.** Being conservative has a cost: plan on about 50 cases to reliably detect a medium-sized improvement.

![Detection rate by improvement size and case count](https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/power.svg)

**A simulated year of weekly check-ups.** 200 seeded years of 52 weeks through the real `checkup()`, with 50 cases and five planted incidents:

| Planted incident | Result in the incident week |
|---|---|
| Harmful provider change (0.5 noise SD) | drift flagged in **87.5%** of years |
| Harmless provider change | falsely flagged in **2.0%** |
| Prompt regression merged without review | flagged in **98.5%** |
| Real fix proposed | kept in **92.5%** |
| Useless fix proposed | rejected in **95.0%**; gated 2,000 more times, it was falsely kept **2.05%** of the time |

Quiet weeks raised a false drift alarm **2.3%** of the time, about one per year. The incidents are planted effects of known size, with plain-function model stubs.

![Weekly drift flags and proposal outcomes over 200 simulated years](https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/checkup_timeline.svg)

```bash
PYTHONPATH=src python benchmarks/run_gate_benchmark.py                    # about 40 seconds
PYTHONPATH=src python benchmarks/run_checkup_simulation.py --repeats 200  # about 30 seconds
python benchmarks/charts.py                                               # renders the charts
```

## Known limitations

- **Small effects need many cases.** "Not sure yet" usually means *collect more cases*; the power chart shows how many.
- **Live tools add noise.** Tools such as web search can give A and B different results. Replaying both back to back limits this, and the gate falls back to "not sure yet" rather than a wrong verdict.
- **Case choice matters.** Choose cases before looking at results, from recorded traffic or a fixed rule. The gate can't correct a sample picked because it flatters a change.
- **The weekly check-up finds an unreviewed regression after it is live.** Run the gate on every pull request before merging.

The complete list, and what is deliberately out of scope, is in [DESIGN.md](https://github.com/gael55x/promptcheckup/blob/main/DESIGN.md).

## FAQ

#### Why does it say "not sure yet" so often?

That is the point. A wrong "better" ships a worse agent. "Not sure yet" means the evidence can't separate the prompts yet; add cases or run again.

#### Which judge model should I use?

One at least as capable as your agent's own model on the task. Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5 all reached the right verdict on the support-bot example with a reliable self-check. Sonnet and Opus counted all 30 cases as improved, where Haiku counted 28: a smaller judge is cheaper but less decisive, so plan on more cases. Any OpenAI-compatible endpoint works, including Anthropic's at `https://api.anthropic.com/v1`.

#### Does it work for trading agents?

Yes. The skew guard, the trial ledger and the look-ahead check come from trading: strategies that make small gains most days and take a rare large loss look great on short samples. promptcheckup is a research and engineering tool, not financial advice.

#### Does it send my data anywhere?

Only to the LLM endpoint you configure, for judging, grading and proposals, and only after your `redact` function has run. The gate in score mode makes no network calls at all. Nothing is ever sent to the authors.

#### Will it change my prompts on its own?

It writes proposed edits to your working tree only after they pass the gate, and the workflows open a pull request for a person to review. It never merges, deploys or pushes to your main branch.

## Acknowledgements

promptcheckup builds on ideas from these papers. It implements them from the paper descriptions and copies no code:

- **[MemTrial](https://arxiv.org/abs/2610.11732)** (Wu, Cai and Wang): comparing decisions on the same inputs so shared noise cancels.
- **[EvolveTrade](https://arxiv.org/abs/2609.17632)** (Kim, Choi, Kang and Hwang): rewriting an agent's prompt from its own decision traces and outcomes.
- **[SHARP](https://arxiv.org/abs/2605.06822)** (Chen et al.): small, attributable, validated policy edits.
- **[ATLAS](https://arxiv.org/abs/2510.15949)** (Papadakis et al.) and **GEPA** (Agrawal et al.): prior work on feedback-driven prompt optimization.

## Citation

If promptcheckup helps your work, please cite it. The metadata is in [CITATION.cff](https://github.com/gael55x/promptcheckup/blob/main/CITATION.cff), and GitHub's "Cite this repository" button formats it for you.

## Contributing

Issues and pull requests are welcome. To set up a development environment:

```bash
git clone https://github.com/gael55x/promptcheckup && cd promptcheckup
python3 -m venv .venv
.venv/bin/pip install pytest==9.1.1 ruff==0.17.1
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Changes follow test-driven development and [Conventional Commits](https://www.conventionalcommits.org/). See the [changelog](https://github.com/gael55x/promptcheckup/blob/main/CHANGELOG.md) for release history.

## License

Licensed under the [Apache License, Version 2.0](https://github.com/gael55x/promptcheckup/blob/main/LICENSE).
