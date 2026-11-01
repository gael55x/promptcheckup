<div align="center">

# promptcheckup

**Know whether a prompt change actually made your AI agent better.**

[![CI](https://github.com/gael55x/promptcheckup/actions/workflows/ci.yml/badge.svg)](https://github.com/gael55x/promptcheckup/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Status: pre-release](https://img.shields.io/badge/status-pre--release-orange.svg)](#roadmap)

promptcheckup replays the same cases through your old and new prompt,<br>
compares them case by case, and answers **better**, **worse** or **not sure yet**.

<img src="benchmarks/results/naive_vs_paired.svg" alt="Bar chart: comparing prompts on different weeks picked the worse prompt 41.3% of the time; paired replay picked it 0% of the time." width="720">

[Quick start](#quick-start) · [How it works](#how-it-works) · [Benchmarks](#benchmarks) · [Roadmap](#roadmap) · [FAQ](#faq)

</div>

> [!NOTE]
> **Early development.** The statistical gate is built, tested and benchmarked. The agent runner, LLM judge, CLI and GitHub workflows are next.

## Highlights

| | |
|---|---|
| **Paired replay** | Both prompts run on the same inputs, so shared noise (the day's market, an easy question) cancels out instead of deciding the result. |
| **Conservative verdicts** | "Better" only when a 95% confidence interval clearly excludes zero. With no real difference, it said "better" 2.0–2.7% of the time across every benchmarked noise type. |
| **Built for skewed outcomes** | A skew guard refuses "better" when a few large losses hide among small gains, the classic trap in trading-agent results. |
| **Zero dependencies** | Pure Python standard library, including an exact Student t quantile. No SciPy, no NumPy. |
| **Any agent** *(in progress)* | One decorator or a shell command connects any agent, in any language. |

## Why

Most prompt changes are judged by running the new prompt for a while and comparing its average score with the old one. That mostly measures *when* each prompt ran: a rising market week, an easier batch of questions, a quiet provider update.

In the simulation above, that comparison picked the **worse** prompt in 41% of runs.

promptcheckup is built on one idea: **compare both prompts on identical inputs**, and only claim an improvement the data can support.

## Installation

Not on PyPI yet. Install from GitHub (Python 3.11+):

```bash
pip install git+https://github.com/gael55x/promptcheckup
```

## Quick start

Score the same cases with both prompts, then pass the per-case differences (new minus old) to the gate:

```python
from promptcheckup import decide

# Scores for the same 20 test cases, run once with the old prompt and once with the new one.
old = [0.62, 0.55, 0.71, 0.40, 0.58, 0.66, 0.49, 0.53, 0.60, 0.44,
       0.57, 0.68, 0.51, 0.47, 0.63, 0.59, 0.42, 0.65, 0.56, 0.50]
new = [0.70, 0.61, 0.74, 0.52, 0.57, 0.75, 0.58, 0.60, 0.69, 0.47,
       0.66, 0.71, 0.60, 0.55, 0.64, 0.68, 0.50, 0.73, 0.62, 0.58]

verdict = decide([b - a for a, b in zip(old, new)])

print(verdict.decision)
print(f"mean change {verdict.mean:+.3f}, 95% CI [{verdict.ci_low:+.3f}, {verdict.ci_high:+.3f}]")
print(f"{verdict.wins} improved, {verdict.losses} got worse, {verdict.ties} unchanged")
```

```text
better
mean change +0.067, 95% CI [+0.052, +0.082]
19 improved, 1 got worse, 0 unchanged
```

**Things to know:**
- **Every verdict explains itself** in `verdict.reason`.
- **Too few cases:** with fewer than 20 cases you get `inconclusive`, with the reason "need at least 20 cases".
- **Judge scores:** for LLM-judge preferences in [−1, 1], pass `skew_guard=False`. Those scores are bounded, so they can't hide a rare large loss.

To see paired replay beat a naive comparison, run the [planted example](examples/planted/). It needs no API key.

## How it works

1. **Replay.** Run every case through prompt A and prompt B, back-to-back, so both see the same conditions.
2. **Pair.** For each case, take B's score minus A's score. Anything the two runs share cancels.
3. **Decide.** Compute a paired 95% t confidence interval of the mean difference:
   - **better** if it sits entirely above zero;
   - **worse** if it sits entirely below zero;
   - **not sure yet** otherwise, or when the differences are skewed toward a few large losses.

Today, step 3 is shipped as `decide()`. Steps 1 and 2 are being built as the `runner` package. See [DESIGN.md](DESIGN.md) for the full architecture.

## Benchmarks

Seeded simulations of the shipped gate. All numbers can be reproduced with the commands at the end of this section.

**False "better" verdicts when nothing changed.** The target is 2.5%. Plain statistics fail on crash-skewed P&L; the skew guard holds it there too:

![False "better" rate by method, noise type and case count](benchmarks/results/false_positive.svg)

**Detection power.** Being conservative has a cost. Plan on about 50 cases to reliably detect a medium-sized improvement:

![Detection rate by improvement size and case count](benchmarks/results/power.svg)

```bash
PYTHONPATH=src python benchmarks/run_gate_benchmark.py   # about 40 seconds, writes CSVs
python benchmarks/charts.py                              # renders the SVG charts
```

## Roadmap

| Milestone | Status |
|---|---|
| Statistical gate with skew guard, benchmarks, planted example | **Done** |
| Cases, `@record` decorator, agent runner (Python and any-language command), replay cache | In progress |
| LLM judge with order-swapping and a self-check | Planned |
| `promptcheckup gate` / `checkup` CLI, weekly check-up, GitHub workflow templates | Planned |
| Trading-agent and support-bot examples; validation on real agents | Planned |

The full plan, including what is deliberately out of scope, is in [DESIGN.md](DESIGN.md).

## FAQ

#### Why does it say "not sure yet" so often?

That is the point. A wrong "better" ships a worse agent. "Not sure yet" means *collect more cases*. The [power chart](#benchmarks) shows how many you need for each size of improvement.

#### Why not just compare average scores?

Because the two averages usually come from different conditions. Paired replay removes that. See the [planted example](examples/planted/) for a demonstration.

#### Does it work for trading agents?

Yes. That is where the skew guard comes from: strategies that make small gains most days and take a rare large loss look great on short samples. promptcheckup is a research and engineering tool, not financial advice.

#### Does it send my data anywhere?

No. The gate runs locally and has no network code. Later components will call only the LLM endpoint you configure.

## Acknowledgements

promptcheckup builds on ideas from these papers:

- **[EvolveTrade](https://arxiv.org/abs/2609.17632):** rewriting an agent's prompt from its own decision traces and outcomes.
- **[MemTrial](https://arxiv.org/abs/2610.11732):** comparing decisions on the same inputs so shared noise cancels.
- **[SHARP](https://arxiv.org/abs/2605.06822):** small, attributable, validated policy edits.
- **[ATLAS](https://arxiv.org/abs/2510.15949)** and **GEPA:** prior work on feedback-driven prompt optimization.

## Contributing

Issues and pull requests are welcome. To set up a development environment:

```bash
git clone https://github.com/gael55x/promptcheckup && cd promptcheckup
python3 -m venv .venv
.venv/bin/pip install pytest==9.1.1 ruff==0.17.1
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Changes follow test-driven development and [Conventional Commits](https://www.conventionalcommits.org/).

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
