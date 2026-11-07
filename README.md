<div align="center">

# promptcheckup

**Your prompt change looked better. Was it better, or was it just an easier week?**

A pass/fail check for prompt changes. Runs your old and new prompt on the same cases and says **better**, **worse** or **not sure yet**, with a 95% confidence interval. Works in CI. No dependencies.

[![CI](https://github.com/gael55x/promptcheckup/actions/workflows/ci.yml/badge.svg)](https://github.com/gael55x/promptcheckup/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/promptcheckup?label=pypi)](https://pypi.org/project/promptcheckup/)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB.svg?logo=python&logoColor=white)](https://github.com/gael55x/promptcheckup/blob/main/pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/gael55x/promptcheckup/blob/main/LICENSE)

<!-- Animated SVG built from the real output of examples/planted/planted.py and examples/support-bot/results/report-claude-haiku-5-5.md. -->
<img src="https://raw.githubusercontent.com/gael55x/promptcheckup/main/docs/assets/demo.svg" alt="Terminal demo: comparing two different weeks picks the worse prompt; running both prompts on the same days finds the better one; a real support-bot run reports B is better, 28 of 30 cases, with a reliable judge self-check." width="760">

<table>
<tr>
<td align="center" width="25%"><h3>41.3%</h3>of the time, comparing last week's prompt with this week's picked the <b>worse</b> one<br><sub><a href="#results">simulation, 2,000 runs</a></sub></td>
<td align="center" width="25%"><h3>0 of 2,000</h3>runs where paired replay picked the worse one<br><sub><a href="#results">same simulation</a></sub></td>
<td align="center" width="25%"><h3>2.2–2.7%</h3>false "better" when both prompts are equal<br><sub><a href="#results">target 2.5%</a></sub></td>
<td align="center" width="25%"><h3>0</h3>dependencies outside the Python standard library<br><sub>any agent language</sub></td>
</tr>
</table>

</div>

## Try it in 30 seconds (no API key)

```bash
git clone https://github.com/gael55x/promptcheckup && cd promptcheckup
PYTHONPATH=src python3 examples/planted/planted.py
```

```text
Naive comparison (different weeks): B - A = -1.56  -> picks A
Paired replay (same days): mean B - A = +0.36, 95% CI [+0.17, +0.55] -> better
```

Prompt B really is better. Comparing it with last week's run of prompt A says the opposite, because the week changed, not the prompt. Running both prompts on the same days cancels the week out.

## Use it on your agent

```bash
pip install promptcheckup        # Python 3.11+
promptcheckup init               # config, example cases, two GitHub workflows
promptcheckup doctor             # checks the wiring and says what to fix
promptcheckup gate --old-ref origin/main --out report.md
```

```text
B is better: 28 of 30 cases improved (95% CI +0.78 to +0.99).
```

`gate` exits 0 for better, 1 for worse, 2 for not sure yet, so a pull request that makes the agent worse fails CI. Your agent can be a Python function or any program that reads JSON on stdin and writes JSON on stdout. See the [connection guide](https://github.com/gael55x/promptcheckup/blob/main/docs/connect-your-agent.md) for plain Python, OpenAI and Anthropic SDKs, and Node.js.

## Why not just compare the scores?

- **Last week isn't a control.** Model updates, market moves and traffic mix shift every answer. promptcheckup runs both prompts back to back on the same inputs, so those shifts cancel.
- **20 cases can lie.** It gives an interval, not a single score, and says *not sure yet* instead of guessing. It also catches a few large losses hidden among many small wins.
- **LLM judges have favourites.** Before a judge's verdict counts, the judge is checked on old prompt versus old prompt, which should come out even, and sees each pair in both orders.

<p align="center">
<img src="https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/naive_vs_paired.svg" alt="Bar chart comparing naive and paired replay in the constructed weekly-noise scenario described below." width="720">
</p>

In this simulation, each week's conditions shift every answer by the same amount, and that shift cancels when both prompts run on the same inputs. Comparing last week's prompt with this week's picked the worse prompt in 41.3% of runs. Running both on the same inputs picked the better one in 91.7%, never picked the worse one in 2,000 runs, and said not sure yet in the other 8.4%.

## Setup in detail

```bash
promptcheckup init
```

This writes `promptcheckup.toml`, a few example cases and two GitHub workflows. Edit the config so it points at your agent, your prompt files and your cases. The agent can be a Python function `run(case, prompts)` or any program that reads JSON on stdin and writes JSON on stdout. The [connection guide](https://github.com/gael55x/promptcheckup/blob/main/docs/connect-your-agent.md) has adapters for plain Python, the OpenAI and Anthropic SDKs, and Node.js.

Check the setup, then compare your working tree with `main`:

```bash
promptcheckup doctor
promptcheckup gate --old-ref origin/main --out report.md
```

The first line of the report is the verdict:

```text
B is better: 28 of 30 cases improved (95% CI +0.78 to +0.99).
```

`gate` exits with 0 for better, 1 for worse, 2 for not sure yet and 3 for errors, so it can run in CI. Before it starts, it shows how many agent and judge calls it plans to make and asks for confirmation. Pass `--yes` to skip the question.

### Other commands

Each runs as `promptcheckup <command>`.

| Command | What it does |
|---|---|
| `checkup` | The weekly check. Compares this week's answers with the latest saved snapshot to catch silent model or provider changes, groups failing cases, and proposes one small prompt edit. The edit is written from some of your cases and kept only if it passes the gate on the rest. |
| `evolve` | Repeats the proposal step for several rounds, learning on one part of your cases and gating on a held-out part, at a stricter confidence than a single gate. |
| `propose` | Proposes and gates one edit, without the drift check. |
| `doctor` | Checks the config, prompts, cases, agent, scoring, judge and budget, and says what to fix. |

`checkup` and `propose` hold out half of your cases by default (`run.holdout`). A gate needs 20 cases by default, so a proposal needs about 40 cases before it can be tested. With fewer, the report shows the proposed edit without testing or writing it, and `doctor` warns you. Which cases are held out depends only on a hash of their IDs.

The check-up report says "Stable" only when the drift comparison ran without errors. If every agent call fails, the report says so and the command exits with 3.

None of these merge anything. Edits that pass are written to your prompt files, and the workflow templates open a pull request for you to review.

### In your tests

```python
from promptcheckup.testing import assert_better, assert_no_lookahead

def test_new_prompt_is_better():
    assert_better(run, cases, old_prompts, new_prompts, score=score)

def test_agent_cannot_see_the_future():
    assert_no_lookahead(build_input, history, decision_times, time_key="date")
```

`assert_no_lookahead` is for agents that work on time series. For each decision time it rescales the later numbers, changes each one at random, and removes the later rows, and it fails if the agent's input changes. It catches common leaks; it is a check, not a proof.

### Scoring

If you can compute how good an answer is, pass a `score(case, output)` function. For a trading agent that might be next-day profit and loss. Otherwise, write criteria and let a model judge pairs of answers. The judge sees both answers in both orders, and before its verdict counts it is checked against itself on old prompt versus old prompt, which should come out even. The gate report also shows how often the judge gave the same answer in both orders.

## How the verdict is made

For each case, promptcheckup subtracts the old prompt's score from the new prompt's score. It then computes a 95% confidence interval for the average difference using a paired t-test.

- If the whole interval is above zero, the verdict is better.
- If the whole interval is below zero, the verdict is worse.
- Otherwise the verdict is not sure yet. It also says not sure yet with fewer than 20 cases (`run.min_pairs`, at least 5), when a few large losses are hidden among many small gains, or when any run failed.

Both prompts run on each case back to back, each with its own copy of the input. Cached answers are reused only when the cache holds both prompts' answers for that case.

More detail is in [DESIGN.md](https://github.com/gael55x/promptcheckup/blob/main/DESIGN.md).

## Results

These come from seeded simulations of the shipped code and a few runs against real models. The commands to reproduce them are at the end of this section.

**False "better" verdicts.** When the two prompts were equally good, the gate said better 2.2–2.7% of the time on normal, heavy-tailed and judge-score noise, against a 2.5% target. On trading-style P&L with rare crashes, a plain t-test said better 8.9–12.7% of the time. With the skew guard it was 2.0% at 20 cases and almost never at 50 or more. The worst case we know of is a judge that calls every case a clear win or loss: at 50 cases it gives a false "better" 3.2% of the time, computed exactly in the test suite.

<img src="https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/false_positive.svg" alt="False better rate by method, noise type and number of cases" width="720">

**How many cases you need.** The chance of detecting a real improvement depends on its size, measured in standard deviations of the per-case noise. The table assumes normally distributed, independent per-case differences, with 2,000 simulations per cell.

| Improvement | 20 cases | 50 cases | 120 cases |
|---|---|---|---|
| Large (0.8 SD) | 82.3% | 94.7% | 98.2% |
| Medium (0.5 SD) | 49.8% | 88.1% | 99.1% |
| Small (0.2 SD) | 10.8% | 27.6% | 58.5% |

<img src="https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/power.svg" alt="Detection rate by improvement size and number of cases" width="720">

**A simulated year of weekly check-ups.** This ran 200 repeats of 52 weeks through the real check-up code, with 50 fixed cases, 2 samples and five planted incidents; proposals were gated on 25 held-out cases. It assumes Gaussian noise, fixed cases, scripted proposals and effects of known size, with no real model.

| Incident | Result in that week |
|---|---|
| Provider change that made answers worse (0.5 SD) | flagged in 87.5% of years |
| Provider change with no effect on quality | flagged in 2.0% |
| Unreviewed regression after the real fix was kept (2 SD drop) | flagged in 100.0% (181/181 years) |
| Unreviewed regression when the real fix was not kept (1 SD drop) | flagged in 84.2% (16/19 years) |
| Real fix proposed (+1 SD) | kept in 90.5% (181/200) |
| Useless edit proposed | rejected in 97.5% (195/200) |

An unreviewed regression is a bigger drop when the real fix had been kept earlier that year, so the table splits it by size. Quiet weeks raised 216 false alarms in 9,200 assessable weeks (2.3%), about one a year; the 9,400 quiet weeks include 200 first weeks without a baseline. That is this simulation's result, not a production expectation. The useless-edit row comes from one week per year, so it is noisy. Gating that same edit another 2,000 times on the 25 held-out cases wrongly kept it 48 times (2.4%).

<img src="https://raw.githubusercontent.com/gael55x/promptcheckup/main/benchmarks/results/checkup_timeline.svg" alt="Weekly drift flags and proposal outcomes over 200 simulated years" width="720">

**Real models.** The saved reports were made with promptcheckup 0.0.1, before this release; they show how the judges behaved, and do not validate 1.1.0. In the support-bot example, Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5 each judged the same 30 cases, and each passed the self-check with 100.0% order consistency. A full gate with Haiku as the judge took 180 judge calls (58,330 input and 40,108 output tokens).

**Trading example.** A model-driven trader compared a trend-following policy with the same policy plus a risk rule, on 60 cases from a synthetic market. The new policy won 26 cases and lost 13, but the gate said not sure yet (+12.76 bp per case, 95% CI −11.04 to +36.57). Applying both policies to all 2,085 eligible ticker-days in this generated market gives a mean of +1.37 bp, still too small to tell apart from zero. An inconclusive verdict does not show that the policy has no edge.

### What this means in practice

- A "better" verdict is reliable when your cases are independent and look like your real traffic. In every simulation the false "better" rate stayed near the 2.5% target, at most 3.2%. A rare large loss that never appears in your cases can't be caught, so draw cases from recorded traffic.
- "Not sure yet" usually means you need more cases. Under the power table's assumptions, 20 cases only reliably catch large improvements; plan on about 50 for a medium one and well over 120 for a small one. The report says why, which may also be failed runs or the call budget.
- Compare prompts on the same inputs. Comparing across weeks picked the worse prompt in 41.3% of runs.
- Every variant you test on the same cases adds about a 2.5% chance of a false "better". Ten independent tries give about a 22% chance of at least one, and never more than 25%. Use fresh cases, raise `run.confidence`, or let `evolve` set a stricter bar. The trial ledger in `.promptcheckup/trials.jsonl` counts the distinct variants tried on the same cases and warns you; running the same variant again isn't counted.
- Expect the occasional false drift alarm. Look at the cases that changed most in the report before acting on it.
- In trading, an edge of a few basis points is small next to daily noise, so even thousands of cases may not settle it. Paired replay removes the noise both prompts share; it can't make an unrepresentative sample representative.

```bash
PYTHONPATH=src python benchmarks/run_gate_benchmark.py
PYTHONPATH=src python benchmarks/run_checkup_simulation.py --repeats 200 --null-trials 2000
python benchmarks/charts.py
```

## Examples

- [planted](https://github.com/gael55x/promptcheckup/tree/main/examples/planted): naive versus paired comparison, with no API key needed.
- [support-bot](https://github.com/gael55x/promptcheckup/tree/main/examples/support-bot): a judge-mode quickstart, with saved reports from three hosted judges.
- [trading](https://github.com/gael55x/promptcheckup/tree/main/examples/trading): a model-driven trader on a seeded synthetic market. It ships with recorded model replies, so no key is needed, and includes a test that changing future prices leaves its input unchanged.
- [connect](https://github.com/gael55x/promptcheckup/tree/main/examples/connect): tested adapters for plain Python, the OpenAI and Anthropic SDKs, Node.js, and the test helpers.

## Limitations

- Small improvements need many cases. See the table above.
- Live tools such as web search can return different results on the two runs. Running them back to back helps, and when the noise is still too high the gate says not sure yet.
- The gate can't fix a biased set of cases. Pick cases before you look at results, from recorded traffic or with a fixed rule. The interval also assumes the cases are independent, and a rare large loss that isn't in your cases can't be seen.
- Instructions inside an agent's output can still influence the judge. Escaping only stops an output from breaking out of its block.
- The weekly check-up only notices an unreviewed prompt regression after it has been merged, so run `gate` on pull requests.

## FAQ

**Does it send my data anywhere?** Only to the model endpoint you configure, and to whatever your own agent calls. In score mode, `gate` makes no model calls of its own; `checkup`, `propose` and `evolve` still call the model to find failure patterns and write proposals. Your `redact` function runs on case inputs, agent outputs and error text before anything is stored or sent, including answers from older snapshots. Prompts and judge criteria are sent unchanged.

**Will it change my prompts on its own?** It writes an edit to your prompt files only after the edit passes the gate. It never merges, deploys or pushes.

**Which judge model should I use?** One at least as capable as your agent's own model. Any OpenAI-compatible endpoint works, including Anthropic's at `https://api.anthropic.com/v1`.

**Is this financial advice?** No. It is a research and engineering tool.

## Credits

promptcheckup builds on ideas from these papers. It implements them from the papers' descriptions and copies no code.

- [MemTrial](https://arxiv.org/abs/2610.11732) (Wu, Cai and Wang): comparing decisions on the same inputs so shared noise cancels.
- [EvolveTrade](https://arxiv.org/abs/2609.17632) (Kim, Choi, Kang and Hwang): rewriting an agent's prompt from its own decisions and outcomes.
- [SHARP](https://arxiv.org/abs/2605.06822) (Chen et al.): small, attributable, validated policy edits.
- [ATLAS](https://arxiv.org/abs/2510.15949) (Papadakis et al.) and [GEPA](https://arxiv.org/abs/2507.19457) (Agrawal et al.): earlier work on feedback-driven prompt optimization.

To cite promptcheckup, use [CITATION.cff](https://github.com/gael55x/promptcheckup/blob/main/CITATION.cff) or GitHub's "Cite this repository" button.

## Contributing

Issues and pull requests are welcome.

```bash
git clone https://github.com/gael55x/promptcheckup && cd promptcheckup
python3 -m venv .venv
.venv/bin/pip install pytest==9.1.1 ruff==0.17.1
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Changes are made test-first and use [Conventional Commits](https://www.conventionalcommits.org/). Release notes are in the [changelog](https://github.com/gael55x/promptcheckup/blob/main/CHANGELOG.md).

## License

[Apache License 2.0](https://github.com/gael55x/promptcheckup/blob/main/LICENSE)
