# Trading: paired replay with a risk rule

An EvolveTrade-style LLM trader sizes one position from recent prices. A policy
prompt makes the decision; Python scores next-day profit and loss after costs.
The gate compares both policies on the same ticker and day.

The market is synthetic on purpose: 15 fictional tickers, `SYN01` through `SYN15`,
over 160 trading days. A model may remember real historical prices, leaking the
answer; real price data also has redistribution limits. Fictional data avoids
both issues. There are no real securities or live prices here.

Each ticker has its own drift and volatility. Calm periods have mild momentum;
stressed spells have triple volatility. About 1% of days crash by 6–12%. The CSV
and 60 cases are already included. To regenerate them later, from this directory
after installing promptcheckup, run:

```bash
python market.py
```

The 60 cases follow a fixed rule committed in the repo, independent of prices and
results: for ticker index `i`, choose `30 + (step * 32 + i * 8) % 128` for steps
0 through 3, then sort the days. This gives four days per ticker, 32 days apart,
with an eight-day stagger that wraps within the window. Day numbers start at zero.
Seed 20261101 generates prices.
Stressed spells are days 40–59 and 100–119. Ordinary daily moves are capped
at ±5.5%; crashes are drawn separately. This is a small toy market, not a fitted
market model.

## Policies

- [A: follow the trend](prompts/old-policy.md). Long on a positive 5-day return,
  short on a negative one. Absolute size is `min(1, abs(return_5d) / 0.05)`.
- [B: add a risk rule](prompts/policy.md). Use the same sizing, but stay flat when
  20-day daily volatility exceeds 2% or the last day fell more than 4%. Otherwise
  halve size after any daily drop of at least 3% in the window.

Python computes the statistics; the model interprets the policy. It sees the last
20 closes, ending on the decision day, and no later price. A check scales every
future price for each of the 2085 decision days and requires the model's prompt to
stay byte-identical; another check detects a single future close added to the window.
The 20-day return and sample standard deviation
use 20 daily returns from 21 closes, ending on the case day. Volatility is not
annualized. The prompt also supplies the last and worst daily returns in that window.

The score, in basis points, is
`10000 * (position * next_day_return - 0.001 * abs(position))`.
Ten basis points is the full one-day round-trip cost, charged even when the price
does not move. Each case starts fresh; positions do not carry between cases.

**Timing.** The decision uses the close of day *t* and earns the move from that
close to the close of day *t*+1. This synthetic market has no overnight gaps, so
filling at the decision-day close equals filling at the next open. With real prices,
fill at the next open instead, or the backtest assumes a fill it could not get.

## Run the gate

From a fresh clone, use Python 3.11 or later. From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cd examples/trading
```

Recordings for Claude Haiku 5.5 are included, so this runs with no key. Leave
`TRADER_RECORD` unset:

```bash
promptcheckup gate --old prompts/old-policy.md --new prompts/policy.md
```

The gate reads replies from disk and computes P&L locally; it needs no judge.
`better`, `worse`, and `inconclusive` are all valid outcomes. Before recording,
`promptcheckup doctor --no-llm` fails its agent check with instructions to run
`record.py`. After recording, doctor's changed-prompt probe still warns: its
temporary policy has no recording. That does not prevent the gate.

## Record your own model

Set your provider key in the environment variable named by `TRADER_API_KEY_ENV`,
then run from this directory:

```bash
python record.py
```

The script prints the model, endpoint and planned call count, then asks
`Proceed? [y/N]`. A fresh recording is 120 model calls, one for each policy/case.
`python record.py --yes` skips confirmation. Provider usage is billed separately.
Existing recordings are skipped, so rerunning resumes an interrupted batch.
No key is printed. The defaults are:

| Setting | Default |
| --- | --- |
| `TRADER_BASE_URL` | `https://api.anthropic.com/v1` |
| `TRADER_MODEL` | `claude-haiku-5-5` |
| `TRADER_API_KEY_ENV` | `ANTHROPIC_API_KEY` (the variable's name, not its value) |
| `TRADER_RECORDINGS` | `recordings/` beside `trader.py` |

Each reply is saved as `<directory>/<model>/<prompt_hash>/<case_id>.json`, holding
its raw `reply` text. The hash covers the complete prompt mapping, and the case ID
hashes the input. Recordings are kept per model, so recording a second model never
reuses the first one's replies; set `TRADER_MODEL` to the same value when replaying.
Do not reuse recordings after changing the market or the prompt-building code.
promptcheckup caches agent outputs separately, keyed by `run.agent_version`: set it
to the model name when you switch models, so the gate does not reuse cached outputs.

Setting `TRADER_RECORD=1` also lets individual agent calls record missing replies.
Leave it unset for an offline gate. The client uses a 110-second timeout and retries
rate limits and server errors; each missing case is recorded once. Invalid replies are saved
before validation and raise an error. Inspect and remove an invalid recording
before recording that case again; damaged files never silently trigger a call.

## Results

Recorded with Claude Haiku 5.5: 120 calls, one per policy and case. See the
[full report](results/report-claude-haiku-5-5.md), produced by the earlier version
0.0.1. It checks the recorded policies, rather than validating release 1.1.0.

| Check | Result |
| --- | --- |
| Gate on the 60 recorded cases | **Not sure yet.** Mean +12.76 bp per case, 95% CI -11.04 to +36.57. B won 26, lost 13, tied 21. |
| Did the model follow each policy? | A: 60 of 60, B: 60 of 60 recorded positions are within 0.02 of each policy's rule (`check_policies.py` `TOLERANCE`). |
| Both policies as code, on all 2085 ticker-days | **Not sure yet.** Mean +1.37 bp, 95% CI -2.61 to +5.36. |

On the sample, B looked better: twice as many wins as losses, and +12.8 bp a case.
But one case, where A was fully short into a 5.5% rally and B stayed flat, carries
about 9 bp of that average on its own. Applied to every eligible day of this
generated market, B's average edge is +1.37 bp and can't be told apart from zero.
A win count would have picked B; the gate said "not sure yet". That doesn't show
the rule has no edge, only that this data can't settle it. Crashes in this market
arrive at random, so "stay flat after a drop" has little to predict. A market whose
crashes cluster could reward the rule.

All 60 recorded positions for each policy were within 0.02 of its rule written as
code, so the verdict reflects the policies rather than the model wandering from them.
Reproduce both checks without a key:

```bash
python check_policies.py
```

Tests pin every number in this table to the committed recordings and code.

## What this shows

Pairing keeps the ticker, day, next-day return and cost rule identical for A and B.
It avoids comparing a lucky market week with an unlucky one. Unlike the additive
noise in the [planted example](../planted/), market returns do not cancel completely
when policies choose different exposures. The fixed recordings make a replay
repeatable; they do not remove uncertainty about other markets.

The score gate's skew guard remains enabled. If differences lean toward rare large
losses and a loss exceeds the average gain, it can turn an apparent improvement
into `inconclusive`; the reverse applies to apparent regressions. It may not
trigger on these cases and cannot protect against an unseen crash.

Limits: one synthetic market, one model per recording set, 60 cases, and one model
sample per policy/case. Overlapping lookbacks and shared regimes can make cases
dependent; the interval does not correct for this. There is no portfolio, borrowing
cost, variable slippage, live execution, memory or multi-turn replay. The risk rule
can miss gains as well as avoid losses. This is a research tool, not financial advice.
