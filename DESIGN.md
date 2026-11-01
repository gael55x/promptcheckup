# promptcheckup: design for v1.0

**Status:** design for v1.0, written 2026-11-01. The statistical gate, cases and replay, the LLM judge, the PR gate and the weekly check-up are built, with their CLI commands (section 15).

---

## 1. What it is

`promptcheckup` is a **Python library with a CLI**. It ships with **two GitHub workflow templates** that run the CLI for you.
- It is not a framework: it never owns your agent loop.
- It is not a hosted application: there is no server and no dashboard.

You give it three things:
- your agent, as a Python function or any command;
- your prompt files;
- some cases.

In return it tells you, honestly, whether a prompt change made the agent better. It also gives you a weekly check-up of how your agent is doing.

**The one promise:**
> Your agent's prompt improves from real outcomes, and nothing changes unless a paired replay proves it's better.

## 2. Where the ideas come from

| Idea | Source | License |
|---|---|---|
| Proposer: an LLM rewrites the agent's prompt from decision traces and outcomes | EvolveTrade, [arXiv 2609.17632](https://arxiv.org/abs/2609.17632) | CC BY 4.0 |
| Gate: compare decisions on the same inputs so shared noise cancels | MemTrial, [arXiv 2610.11732](https://arxiv.org/abs/2610.11732) | CC BY 4.0 |
| Small, attributable rule edits with validation; audit trail | SHARP, [arXiv 2605.06822](https://arxiv.org/abs/2605.06822) | arXiv non-exclusive |
| Prior art: feedback-driven prompt optimization for a trading agent | ATLAS, [arXiv 2510.15949](https://arxiv.org/abs/2510.15949) | not checked |
| Prior art: reflective prompt evolution | GEPA (Agrawal et al., 2025) | not checked |

**Rules that follow from this table:**
- Implement everything from the paper descriptions only.
- **Never copy or adapt code from `moderncavemann/MemTrial`.** It has no license file, which means all rights are reserved.
- Do not copy text or figures from SHARP.
- Credit every row above in the README and in `CITATION.cff`.
- License this project under **Apache-2.0**, which includes a patent grant.
- I did not search patent databases. Do a quick search on Google Patents before the public launch.

## 3. Design principles

1. **Value on every use.** Every run produces at least one concrete finding, or a two-second "stable, nothing changed".
2. **Conservative gate.** When the evidence is unclear, the verdict is "not sure yet". A false "better" is the worst bug this product can have.
3. **The user's infrastructure, the user's repo.** Nothing leaves the user's machine or CI except calls to the LLM endpoint they configured. There is no telemetry. Prompts are files in git, so rollback is `git revert`.
4. **One-line integration.** The agent stays unchanged, except that it must read its prompt from a file instead of a hard-coded string.
5. **KISS and YAGNI.** A feature is added only when a real user needs it. Section 12 lists what is deliberately left out, and what would trigger adding each item.

## 4. Touchpoints

| When | Command | What the user gets | Target |
|---|---|---|---|
| First run, with no data | `promptcheckup init`, then `promptcheckup checkup` | Starter cases, the first failure patterns, the 3 worst answers, and the judge's self-check result | Under 5 minutes |
| Every prompt edit (PR) | `promptcheckup gate` (CI comments on the PR) | A verdict, the evidence, and the 2–3 cases that changed most, shown side by side | Under 3 minutes at default size |
| Weekly (cron) | `promptcheckup checkup` (CI opens an issue) | A drift check, failure patterns, and a fix PR if one passes the gate | Readable in 30 seconds |
| Before the first run, or when something breaks | `promptcheckup doctor` | Every piece of wiring checked in order, one line each, with the fix: config, prompts, cases, agent, prompt use, score, judge, budget | Seconds; 2 agent calls and at most 1 judge call |
| Overnight, on demand | `promptcheckup evolve` | A prompt that beat the current one on held-out cases, round by round, with the full lineage, ready for review | One PR to review in the morning |

**Every report starts with one plain sentence.** Statistics come after it. The headline is built from the verdict data by a fixed template, not written by an LLM. Only the failure-pattern descriptions come from the LLM. Examples:
- "B is better: it stopped refusing refund questions (7 cases)."
- "Your agent got worse this week without a prompt change (likely a provider update): 6 of 20 cases regressed."
- "Stable. No drift and no new failure patterns."

**Before any run, the CLI prints the planned call count and asks for confirmation.** In CI, `--yes` skips the prompt. Cost is always capped by `max_calls`.

## 5. Architecture

```
your app ──(@record, optional)──▶ cases/*.jsonl ◀── starter.py (synthetic, labelled)
                                        │
             ┌──────────────────────────┼──────────────────────────┐
             ▼                          ▼                          ▼
  gate (prompt A vs B)      checkup: drift (now vs snapshot)   checkup: patterns + propose
             │                          │                          │
             └───────── replay.py ──────┴──── judge.py / score ────┘
                                        │
                                     gate.py  (pure statistics)
                                        │
                                    report.py ──▶ markdown ──▶ (workflow posts comment / issue / PR)
```

The library never talks to GitHub. It writes markdown files. The workflow templates post them using the `gh` CLI.

### Package layout (one reason to change per module)

Modules are grouped into subpackages by concern. Each subpackage re-exports its public names in `__init__.py`. Users import the stable API from `promptcheckup` itself; the subpackage paths are internal.

**Rules:**
- A subpackage is created when its first module is written, never as an empty placeholder.
- Dependencies point one way, down this table: `stats` imports nothing from the project; `cli` may import everything.
- Tests mirror the layout: `tests/<subpackage>/test_<module>.py`. Each example has its own folder with a README and a test under `tests/examples/`.

### Code style (functional, declarative, modular)

These rules hold for every module. CI enforces the first one.

1. **Small functions, one job each.** McCabe complexity is at most 5 (ruff `C901`, checked in CI, no waivers). Split anything bigger into named steps.
2. **Orchestrators read as pipelines.** A function that coordinates work is a short top-to-bottom sequence of named steps. It holds no parsing, validation or branching detail of its own.
3. **Data over control flow.** Validation rules, dispatch and mappings live in tables (dicts and tuples) at module level. No `if`/`elif` chains over types or strings.
4. **Errors are handled in exactly two ways, both from `promptcheckup.errors`:**
   - **`errors_as(ErrorType, {ExceptionType: message})`** translates an exception into our own error, once, at the boundary. A message is a fixed string or a function of the original exception; it is never treated as a template.
   - **`fallback(operation, on_error, *exceptions)`** turns a failure into data when that is the point. Examples: an errored run, a failed HTTP reply that will be retried, or a cache miss.
   - The only failures that may be *ignored* are:
     - a recording failure, which becomes a warning so production is never broken;
     - a damaged cache entry, which is re-run.
   - There are no `try` blocks anywhere else in `src/`.
5. **Pure core, I/O at the edges.** Computation takes values and returns values. Reading, writing, network and subprocess calls sit in small edge functions. There is no mutable module-level state; constants are `UPPER_CASE` at the top.
6. **Classes only for data or real state.** Frozen dataclasses for values; a class only when it owns state, such as `Cache` or `LLM`.
7. **One protocol, no `isinstance` dispatch on our own types.** For example, every agent has the same shape: `(case_input, prompts, timeout_s) -> output`.
8. **Names say what you get.** Verbs for actions (`load_cases`, `replay`), nouns for values (`paired_scores`).

`runner/agents.py` is the reference module for this style.

| Subpackage | Module | Responsibility |
|---|---|---|
| `stats/` | `gate.py` | Pure function: paired per-case differences in, `Verdict` out (**built**) |
| `data/` | `cases.py` | Read and write case JSONL; validate it; track the source of each case (`recorded` / `synthetic` / `manual`) |
| | `record.py` | The `@record` decorator: apply `redact()`, then append `{input, output}` to JSONL |
| `runner/` | `agents.py` | Run the agent: as a Python `module:function`, or as a command over stdin/stdout. Enforce the timeout |
| | `replay.py` | Run a prompt set over the cases with k samples, A and B back-to-back. Enforce the call cap. Read and write the output cache |
| `llm/` | `client.py` | A minimal OpenAI-compatible chat client (base URL, model, API-key env var) using `urllib`; no temperature unless configured; retries, timeouts, token usage |
| | `prompting.py` | Embed untrusted data in prompts safely (escaped `<` `>`); parse the JSON object that ends a reply and validate it against a schema table |
| | `grade.py` | `grade(case, out)`: pass/fail with a one-line reason against the user's criteria |
| | `judge.py` | `compare(case, out_a, out_b)` against the user's criteria; order-swapping; paired preference scores; self-check |
| | `starter.py` | Generate synthetic starter cases from the prompt, the agent description and one example input |
| | `patterns.py` | Group failing cases into at most 3 named patterns, citing only provided case IDs |
| | `propose.py` | Generate one small edit from the prompts and the failing cases, with a rationale citing case IDs; reject edits over the size limit |
| `checkup/` | `evaluate.py` | The shared A-vs-B evaluation: replay, score (score or judge mode), decide, downgrade |
| | `failures.py` | Select current failures: judge grades, or the bottom 20% of scores; errored runs always count |
| | `snapshot.py` | Save and load the last check-up's runs per prompt hash; a damaged snapshot is a miss |
| | `checkup.py` | The check-up and proposal pipelines: named steps over one frozen state and a shared call budget |
| | `evolve.py` | Iterate proposal rounds, learning from one part of the cases and gating on a held-out part (7.9) |
| | `ledger.py` | Record every gated trial per case set; count distinct variants (7.10) |
| | `report.py` | Turn a `Verdict`, a check-up result or an evolve result into markdown |
| `cli/` | `config.py` | Load and validate `promptcheckup.toml` |
| | `project.py` | Shared project plumbing: agent, cases, cache, redactor, confirmation |
| | `prompts.py` | Read prompt sets from files or a git ref |
| | `gate.py`, `checkup.py`, `doctor.py`, `init.py` | One module per command |
| | `main.py` | Argument parsing only: `init`, `gate`, `checkup`, `propose`, `evolve`, `doctor` |
| | `templates/` | Workflow YAML, example config and example cases written by `init` |
| (package root) | `testing.py` | Test-suite helpers: `gate`, `assert_better`, `assert_not_worse`, `assert_no_lookahead` (6.5). Imports like `cli`; nothing imports it |

```
src/promptcheckup/
  __init__.py          public API
  stats/  data/  runner/  llm/      built
  checkup/  cli/                    planned, created with their first module
tests/
  stats/  data/  runner/  llm/      one test file per module
  examples/test_planted.py
  test_public_api.py                the package-root API contract
examples/
  planted/             README.md, planted.py
```

**Dependencies:** none at runtime. Python 3.11+ (for `tomllib`) and its standard library only, including `urllib` for the LLM client. Dev dependencies are `pytest` and `ruff`.

**Size target:** about 1,500 lines of library code. If it goes beyond that before v1.0, look for speculative scope.

## 6. Interfaces

### 6.1 What the user writes

```python
# Python agent: called once per case per sample
def run(case: dict, prompts: dict[str, str]) -> Any:  # output must be JSON-serializable
    ...


# Optional: a computable outcome (trading P&L, tests passed, exact match).
# Without it, the LLM judge is used. Must score ANY output, not only recorded ones.
def score(case: dict, output: Any) -> float: ...


# Optional: strip PII before anything is written to disk or sent to the judge
def redact(obj: Any) -> Any: ...
```

**Agents in other languages** use the command adapter. The agent's command (`agent_cmd`) receives `{"case": ..., "prompts": {...}}` as JSON on stdin and must print the output as JSON on stdout.

### 6.2 Recording (optional)

```python
from promptcheckup import record


@record("cases/recorded.jsonl", redact=my_redact)
def handle(case: dict) -> dict: ...
```

`@record` only captures the function's input and output. It does **not** intercept tool calls (see section 12).

### 6.3 Config: `promptcheckup.toml`, written by `init`

```toml
[agent]
entry = "app.agent:run"            # or: cmd = "node agent.js"
description = "Answers customer billing questions for a SaaS product"
example_input = { question = "Why was I charged twice?" }
timeout_s = 60

[prompts]
files = ["prompts/system.md", "prompts/tools.md"]

[cases]
paths = ["cases/*.jsonl"]

[scoring]
mode = "judge"                     # or "score"
score = "app.eval:score"           # used when mode = "score"
criteria = """
Correct, cites the relevant policy, never promises refunds the policy doesn't allow.
"""

[llm]                              # used by judge / starter / propose only
base_url = "https://api.openai.com/v1"
model = "gpt-5-mini"
api_key_env = "OPENAI_API_KEY"
# temperature = 0                 # optional; sent only if set (section 9)

[run]
samples = 2
max_calls = 400
cache_ttl_hours = 24
agent_version = ""                 # optional, e.g. "gpt-5-mini@2026-10"; shown in drift reports
min_pairs = 20
confidence = 0.95                  # base confidence for every gate; evolve raises it per round
redact = ""                        # optional "module:function"
```

### 6.4 CLI

```bash
promptcheckup init                                   # writes config, workflows, cases/example.jsonl
promptcheckup gate --old-ref origin/main            # old prompts from git, new from working tree
promptcheckup gate --old prompts/a.md --new prompts/b.md
promptcheckup checkup                               # drift + patterns + maybe a proposal
promptcheckup propose                               # proposal only; gate it yourself
promptcheckup evolve --rounds 5 --holdout 0.3       # iterate proposals, gated on held-out cases
promptcheckup doctor                                # check the wiring before a real run
```

**Common options:**
- `--yes`
- `--out report.md`
- `--json result.json`

**Exit codes:**
- `0`: better or stable
- `1`: worse or drift detected
- `2`: inconclusive (for `evolve`: no round passed)
- `3`: configuration or runtime error

`doctor` exits 0 when no check failed (warnings allowed) and 3 otherwise. `evolve` exits 0 when at least one round passed.

### 6.5 Testing helpers: `promptcheckup.testing`

For engineers who already run a test suite. Plain functions, no pytest dependency: they raise `AssertionError`, which every test runner reports.

```python
from promptcheckup.testing import assert_better, assert_no_lookahead

def test_new_prompt_is_better():
    assert_better(run, cases, old_prompts, new_prompts, score=score, min_pairs=20)

def test_agent_cannot_see_the_future():
    assert_no_lookahead(build_input, history, decision_times, time_key="date")
```

- `gate(agent, cases, old, new, *, score=None, judge=None, criteria="", samples=2, min_pairs=20, confidence=0.95, max_calls=400) -> Evaluation` runs the same `evaluate` as the CLI. The agent is a plain `run(case, prompts)` function, and cases are `Case` objects or input dicts. Exactly one of `score` or `judge` (an LLM callable, with `criteria`) is required.
- `assert_better(...)` passes on "better". `assert_not_worse(...)` passes unless the verdict is "worse". On failure, the message is the report's headline plus its stats line.
- `assert_no_lookahead(view, history, decision_times, *, time_key="time")` handles a history of rows (dicts) with a time column. For each decision time it perturbs every number in every later row and requires `view(history, t)` to return an identical result. It fails naming the first decision time whose input changed. For other data shapes, the guide shows the same pattern written by hand, as in the trading example.

## 7. Core algorithms

### 7.1 Replay

1. For each case and each sample index `s` in `0..k-1`, run the agent with prompt set A and with prompt set B.
   - Run them **back-to-back for the same case**, not as two separate batches. This keeps both runs as close in time as possible, which limits noise from live tools (section 13).
2. Outputs are cached in `.promptcheckup/cache/`. The cache key is `(prompt_hash, case_id, sample, agent_version)`, where `agent_version` is an optional config string. The library cannot see which model the agent uses internally.
   - **Cache entries expire after `cache_ttl_hours` (default 24).**
   - This matters because a pair is only fair if A and B ran under the same conditions. Reusing an old-prompt output from last week would mix in any provider changes since then and break the pairing.
   - Comparisons across time are the drift check's job (7.5), using an explicit snapshot rather than the cache.
3. If the call cap is reached, stop. The run then reports "inconclusive (budget)". It never reports a partial verdict as if it were final.
4. If an agent call errors or times out, that case is recorded as a failure for that prompt set. Errors are never silently dropped, and they are listed in the report.

### 7.2 Per-case paired difference `d_c`

**Score mode:**
- `d_c = mean_s score(B) − mean_s score(A)`.

**Judge mode (pairwise with order swap):**
- For each sample, `compare(A,B)` and `compare(B,A)` are both run.
- If B wins in both orders, the pair counts `+1`. If A wins in both, `−1`. Anything else (tie or inconsistent) counts `0`.
- `d_c` is the mean over samples.

Inconsistent judgments count as ties on purpose. This is the conservative choice.

### 7.3 Gate decision

1. If `n_cases < min_pairs` (default **20**), the verdict is **inconclusive** ("need more cases").
2. Otherwise, compute a 95% **paired t** confidence interval of `mean(d)`.
   - Values are rescaled into [−1, 1] first, so very small or very large scores neither underflow nor overflow.
   - The t quantile is computed exactly: the integer-df Student t CDF (Abramowitz & Stegun 26.7.3), inverted by bisection. No SciPy is needed. Supported confidence levels are 0.5–0.999.
   - It is deterministic, so the gate needs no seed.
3. Decide from the interval:
   - lower bound > 0: **better**
   - upper bound < 0: **worse**
   - otherwise: **inconclusive**
4. **Skew guard (score mode only).**
   - A "better" verdict is downgraded to **inconclusive** ("a few large losses could decide the outcome; add more cases") when **both** of these hold:
     - the differences have sample skewness below −0.5;
     - at least one loss is bigger than the average gain.
   - The second condition was added after a real example (19 of 20 cases improved, one tiny loss) was wrongly blocked. Small losses cannot hide a tail.
   - A "worse" verdict is downgraded the same way: skewness above +0.5 and at least one gain bigger than the average loss.
   - Judge mode turns the guard off. Its scores are bounded in [−1, 1], so a rare huge loss is impossible, and a genuine improvement (mostly wins) is itself negatively skewed.
5. **Why this design** (measured; the full numbers are in `benchmarks/`):
   - A percentile bootstrap was tried first. With 10–20 cases and no real difference, it said "better" up to 5% of the time, against a 2.5% target.
   - The plain t-interval meets the target on symmetric noise. On crash-skewed P&L (small gains most days, rare large losses) it said "better" **9–13%** of the time. A bootstrap-t (studentized bootstrap) only reduced that to 6–9%.
   - The skew guard brings crash-skewed false "better" to **2.0% at 20 cases and about 0% at 50+**, and keeps every symmetric noise type at 2.2–2.7%.
   - The cost: on symmetric data at 20 cases, the guard also blocks some real improvements. Detection of a large (0.8 SD) improvement falls from about 93% to 82%. In the trading scenario, paired replay picks the better prompt 91.7% of the time, says "not sure yet" 8.3% of the time, and never picks the worse one. Accepted, because a false "better" is the worst bug (section 3).
   - Known remaining worst cases:
     - mildly skewed data at 20 cases: about 3.2%;
     - an always-decisive judge (every case ±1) at certain case counts: up to 3.3% at 50 cases.
     - Both are covered by tests with documented limits.
6. The report also lists:
   - win, loss and tie counts;
   - the 3 cases with the largest `|d_c|`, with A and B outputs side by side;
   - prompt hashes, model IDs, call count and token usage.

### 7.4 Judge self-check (judge mode only)

1. Run the same A-vs-B procedure, but with **A against A**: two independent samples of the old prompt.
2. Compute the same confidence interval. A fair judge's interval includes 0.
3. If it excludes 0, or the judge's order-consistency rate is below 70%:
   - the verdict is **downgraded to inconclusive**;
   - the report says "the judge is unreliable for these criteria" and suggests sharper criteria or a stronger judge model.
4. If there are fewer usable cases than `min_pairs`, the judge **cannot be checked**, so it is reported as not reliable. It is never certified from too little evidence.
5. `llm/judge.py` returns this result as a `SelfCheck`. Applying the downgrade and caching per prompt hash happen in the check-up layer.

This roughly doubles judge cost, so it runs **once per prompt hash** and is cached.

### 7.5 Drift check (inside `checkup`)

1. Use the current prompt hash on the same cases.
2. Compare this run's outputs with the **snapshot from the last check-up**, using the same gate logic (7.2–7.3) and A = last snapshot.
3. **worse** means a "drift" alert. The likely cause is the provider or the environment, because the prompt didn't change. If `agent_version` changed since the snapshot, the report says so.
4. **No snapshot for this prompt yet?** The prompt changed since the last check-up. Compare with the **most recent snapshot of any prompt** instead, on the cases both share. **worse** then means "worse since the last check-up, which used a different prompt", with both prompt hashes in the report and exit code 1. This catches a prompt merged without running the gate. The cause can be the prompt or the environment; the report says both changed. With no snapshot at all, this is the first check-up.
5. Save the snapshot. In CI it persists between runs using `actions/cache`. The key includes the prompt hash, and a fallback restore key without it brings back the previous prompt's snapshots after a prompt change.

### 7.6 Failure patterns (inside `checkup`)

1. Find the failing outputs:
   - in judge mode, `grade()` each current output pass/fail with a one-line reason;
   - in score mode, take the bottom 20% of scores.
2. Make **one** LLM call that groups the failure reasons into at most 3 patterns, each with case IDs.

### 7.7 Proposal (inside `checkup`, or via `propose`)

1. One LLM call receives the prompt files, the failure patterns and up to 5 failing cases.
2. It returns edited files plus a rationale that cites case IDs. This is the EvolveTrade-style policy update.
3. The edit is rejected without gating if:
   - it changes more than 20% of the words in any file, with 30 words always allowed. Words, not lines: a prompt paragraph is often one line, so a line limit would block every edit to a short prompt. The 30-word floor lets a short prompt gain one sentence; or
   - it cites no cases.
4. Otherwise it is gated (7.3).
   - If the verdict is **better**, write the edited files into the working tree and the report to the output file. The workflow does the branch, commit and PR. The library only touches git to read the old prompts (`git show`).
   - Otherwise, the issue mentions "a proposed fix did not pass the gate", with its summary.

### 7.8 Starter cases

This runs when there are no cases yet, or the user asks for it.
1. One LLM call takes the prompt, the agent description and `example_input`.
2. It generates 20 varied inputs: typical, edge-case and adversarial.
3. They are written to `cases/starter.jsonl` with `source = "synthetic"`.
4. Reports show the share of synthetic cases and recommend replacing them with recorded cases.

### 7.9 Evolve: autonomous proposals that cannot fool themselves

A loop that proposes prompt edits and keeps the winners fails in two classic ways. It learns and tests on the same cases, so its edits overfit them. And every extra trial is another chance of a false "better": ten variants at 95% confidence give about a 40% chance of at least one. Quants call this backtest overfitting. `evolve` guards against both.

1. **Split the cases by a fixed rule,** never by results. A case is held out when the first 8 hex digits of its ID, read as a fraction, fall below `holdout_share` (default 0.3). The held-out part needs at least `min_pairs` cases, or the run stops with an error naming the numbers.
2. **Each round is one proposal pipeline run (7.6–7.7):** current runs, failures and patterns on the **learning** cases only. Then the proposal is gated against the current best on the **held-out** cases. The proposal request never contains a held-out case.
3. **The bar rises with the number of rounds.** Each round's gate uses confidence `1 − (1 − base) / rounds` (Bonferroni), capped at 0.999, the gate's supported maximum. The chance of keeping any false improvement over the whole run then stays near `1 − base`.
4. **An accepted edit becomes the new baseline.** The run stops at the first round that doesn't pass, when the budget runs out, or after `rounds`. A budget stop keeps only completed rounds.
5. **Output:** the final prompt files and a lineage report (per round: rationale, cited cases, held-out stats, diff). **It never merges or deploys.** The weekly workflow or a person opens the PR.

Limitations: the held-out set is reused across rounds, which leaks a little information. Bonferroni is conservative, and the round limit keeps the leak small. Fresh cases remain the strongest check.

### 7.10 Trial ledger

Every gated comparison is appended to `.promptcheckup/trials.jsonl`: the case set (a hash of the sorted case IDs), both prompt hashes, the decision, the confidence, the command and the time. A damaged line is skipped, like a damaged cache entry. When two or more distinct variants have been gated on the same case set, the gate report adds one line saying how many there were and how many false "better" verdicts that many trials would produce by chance (`n × (1 − confidence) / 2`). It suggests fresh cases or a stricter `run.confidence`. The ledger only reports; it never changes a verdict on its own.

## 8. GitHub integration (templates, not a packaged Action)

`init` writes two workflows:

- **`promptcheckup-gate.yml`**
  - Trigger: `pull_request` that changes any file listed under `[prompts] files`.
  - Checks out with `fetch-depth: 0`, so the base ref exists.
  - Runs `promptcheckup gate --old-ref origin/${{ github.base_ref }} --yes --out report.md`, then posts the result with `gh pr comment --body-file report.md`.
  - The gate step uses `continue-on-error: true`, so the check informs rather than blocks. Teams that want a blocking check remove that one line.
- **`promptcheckup-checkup.yml`**
  - Trigger: a weekly cron, plus manual dispatch.
  - Runs `promptcheckup checkup --yes --out checkup.md`.
  - Posts the result as **a comment on one long-running issue** labelled `promptcheckup`. The issue is found with `gh issue list --label promptcheckup --state open`, and created on the first run. This keeps one timeline per agent instead of a new issue every week.
  - If a proposal passed the gate, the workflow commits the edited files to a new branch and runs `gh pr create`.
  - It **never merges anything.**

Users add their LLM key as a repository secret. The workflows request the minimum permissions:
- `contents: write` only on the check-up job that pushes the proposal branch;
- `pull-requests: write`;
- `issues: write`.

## 9. Safeguards (all required in v1.0)

- **Call cap** (`max_calls`) on every run, and a per-call timeout.
- **Redaction:**
  - `redact()` runs before anything is written to disk or sent to the judge;
  - `init` documents that recorded cases may contain PII;
  - `.promptcheckup/cache/` is gitignored by default.
- **Never auto-merge.** Proposals are always PRs for a human to review. `evolve` is no exception: it writes files and a report, never a merge or a deployment.
- **No self-deception in autonomous runs.** `evolve` learns and gates on disjoint cases split by a fixed rule, and raises the confidence bar with the number of rounds (7.9). The trial ledger reports how many variants were tried on the same cases (7.10).
- **Errors are loud.** Agent, scorer and judge failures are reported, never skipped. Judge output that can't be parsed is an error, not a tie.
- **The judge can't be steered by the data it judges.** Case inputs and agent outputs are embedded with `<` and `>` escaped, so an output cannot close its block and inject criteria. This matters because the tool optimizes prompts against the judge, which is an open door to reward hacking.
- **Repeatable judgments.** Order swapping and the self-check measure how consistent the judge is, on every run. The client sends no temperature by default, because current Claude models and OpenAI reasoning models reject anything but their default. `llm.temperature = 0` is available for models that still accept it.
- **Reproducibility.** Every report includes the prompt hashes, model IDs and the library version.
- **Trading demo only:**
  - a seeded synthetic market with fictional tickers, so the model cannot recall prices and no third-party data ships;
  - the model sees only data up to the decision day, proven by a test that scrambles every later price and fails on a planted one-day leak;
  - cases follow a fixed rule chosen before looking at results, never a sample picked by outcome;
  - include a "research tool, not financial advice" notice.

## 10. Examples shipped in the repo

| Example | Purpose | Scoring |
|---|---|---|
| `examples/support-bot/` | The 5-minute quickstart for general agent builders | `judge` |
| `examples/trading/` | The flagship demo, an EvolveTrade-style LLM trader on a seeded synthetic market (15 fictional tickers with volatility regimes and rare crashes), with recorded model replies so it works without an API key | `score` (computed P&L with costs) |
| `examples/planted/` | No LLM needed: shows that a naive comparison picks the wrong winner while paired replay picks correctly | `score` |

The trading market is synthetic on purpose. A model may remember real historical prices, which would leak the answer into the test, and real price data has redistribution limits. Fictional tickers avoid both, and the generator is seeded so every run sees the same market. The support bot is deterministic with a planted prompt change, so it demonstrates the wiring and the judge rather than a production gain.

## 11. Test plan (TDD order)

Each step's tests are written and fail before its code is written. Tests use real functions, not mocks. The one exception is the LLM, which is replaced by a stub callable that returns fixed text.

| # | Module | Key tests |
|---|---|---|
| 1 | `gate.py` | Positive shift gives **better**; zero-mean noise gives **inconclusive**; negative shift gives **worse**; fewer than `min_pairs` cases gives **inconclusive**. Exact interval endpoints match hand computation. The t quantile matches table values from 90% to 99.95%. The verdict is the same at 1e-200 and 1e300 scale. The skew guard blocks better/worse in the skewed direction. Unsupported confidence levels are rejected. **False-positive tests:** 4,000 simulations per noise type at 20 and 30 cases. The limit is ≤3% for normal, heavy-tailed and judge noise, and ≤3.5% for three skewed P&L shapes. Exact enumeration for an always-decisive ±1 judge, at most 3.5% up to 50 cases. Done. |
| 2 | planted example | With shared per-case noise and one genuine rule, the naive comparison picks the wrong prompt **and** the paired gate picks the right one. This is the project's reason to exist. |
| 3 | `cases.py`, `record.py` | Round-trips the JSONL; `redact` runs before writing; output that isn't JSON-serializable gives a clear error |
| 4 | `agents.py` | A Python entrypoint works; a real two-line Node or Python command works through stdin/stdout; timeouts are enforced |
| 5 | `replay.py` | A second run hits the cache (call count drops); the call cap stops the run and yields inconclusive; agent errors are surfaced |
| 6 | `client.py`, `grade.py`, `judge.py` | Client: real local HTTP server, retries on 429/5xx/timeouts only, key never in errors, null usage keeps the answer. Grade and judge: order swap is applied; inconsistent judgments count 0; a position-biased stub fails the self-check; a self-check with too few cases is not reliable; unparseable output raises; injected closing tags cannot create extra prompt blocks. Done. |
| 7 | `starter.py`, `propose.py` | Starter cases match the example input's keys and are labelled synthetic; an edit changing more than 20% of lines or citing no cases is rejected |
| 8 | `checkup.py` | Drift: an injected "model change" stub produces **worse**; with no change the report says "stable"; patterns are capped at 3 |
| 9 | `report.py`, `cli.py` | Every report starts with a headline sentence; snapshot test of the markdown; exit codes as in 6.4; `init` writes valid TOML and workflow YAML |

**CI:** run `ruff` and `pytest` on push. The test suite makes no network calls.

## 12. Deliberately out of scope for v1.0

| Not in v1.0 | Add it when |
|---|---|
| Intercepting and replaying individual tool calls | A user's agent depends on non-deterministic live tools, so plain re-running doesn't reproduce the environment |
| Trace-platform imports (Langfuse first, then OTel or LangSmith) | Users ask for it; `@record` and starter cases cover getting started |
| Packaged GitHub Action / GitHub App | People keep copy-pasting the workflow templates |
| Hosted service, dashboard | Paid version, after free-tier traction |
| MCP server (`gate` / `propose` as tools for coding agents) | Users request it |
| Bandit traffic split between prompt versions | After v1.x has real users |
| TypeScript SDK | The command adapter proves insufficient |
| Pluggable optimizers (GEPA) | Someone needs a stronger proposer |
| Dollar cost estimates | Users ask; v1.0 shows calls and tokens |
| Auto-deploying evolved prompts | Never by default; a human reviews and merges |

## 13. Known limitations

These are deliberate boundaries for v1.0. Document them in the README so users aren't surprised.

1. **Stateful agents.**
   - The problem: an agent with memory can't be replayed at a single point in time unless that memory is available. LayeredMemoryTrader is an example, with its short, mid and long-term memory and its reflections.
   - What works: replay works if either
     - the case includes the memory state from that moment; or
     - the agent can rebuild its state from the case, for example from the date.
   - Consequence for us: the LayeredMemoryTrader dogfood example (section 14) must store a memory snapshot in each case.
2. **Multi-turn conversations.**
   - What works: a case can include the conversation so far.
   - What doesn't: replay cannot simulate how the user would have *reacted* to a different answer.
   - In v1.0, each turn is judged on its own. Simulating users is out of scope.
3. **Live, non-deterministic tools.**
   - The problem: tools such as web search or live prices add noise, because A and B may see different results.
   - How v1.0 limits the damage: replay runs A and B back-to-back for each case (7.1). If the noise is still too large, the gate falls back to **inconclusive** rather than giving a wrong verdict.
   - Full tool-call replay stays deferred (section 12) until a user needs it.
4. **Case selection and look-ahead.** Choose cases before seeing results, preferably recorded production inputs or a fixed sampling rule. Keeping samples because they make a change look good is selection by outcome; the gate cannot correct it. Time-series cases must only expose data available at their decision time; see [Choosing cases](docs/connect-your-agent.md#choosing-cases) and the trading example's look-ahead test.
5. **Unreviewed prompt merges.** Before 7.5 step 4 existed, a newly merged prompt had no drift baseline: in the [simulated year](benchmarks/results/checkup_simulation.csv), **0/200** unreviewed regressions were flagged. The check-up now compares a new prompt with the previous prompt's snapshot (7.5 step 4), and the simulated year flags the same 1 SD regression in **98.5%** of incident weeks. It only notices at the next weekly check-up, after the prompt is live, so run the gate before merging.

## 14. Validation before launch

The claim must be proven, not just the code. Launch only with these numbers in the README:

1. **Accuracy where the truth is known.** On the trading example (score mode), measure:
   - how often the gate's verdict matches the computed truth;
   - the inconclusive rate;
   - calls and tokens per run.
2. **Judge vs truth.** Run judge mode on the same trading cases and report how often it agrees with score mode. This is how much to trust judge mode elsewhere.
3. **Dogfood on LayeredMemoryTrader.** Point it at the existing agent's prompts and run 2–3 weekly check-ups. Each case must include that day's memory snapshot (section 13, item 1). This produces real issue screenshots and the launch article.
4. **Run real check-ups for 2+ weeks before launch.** Show actual reports in the README, not mock-ups.
5. **Simulated year.** 200 seeded repeats × 52 weeks, 50 fixed cases and 2 samples: a persistent 0.5 noise-SD provider drop was flagged **87.5%** in its week / **89.0%** through week +2; the harmless change flagged **2.0% / 7.0%**. Quiet-week false alarms were **215/9,400 (2.3%)**, or **1.075/year**. Real +1 SD fixes were kept **92.5%**; useless edits were rejected **95.0%**. The useless-edit week happens once per year, so its 5.0% kept rate is noisy (binomial 95% CI about 2.4–9.0%); gating the same edit 2,000 more times gave **2.05%** false "better", matching the gate's design. Unreviewed prompt regressions are flagged **98.5% / 99.0%** by comparing with the previous prompt's snapshot (7.5 step 4); before that step existed they were flagged 0%. Windows include the incident week and two following weeks; later drift flags do not establish delayed detection because snapshots roll forward each week. These are planted effects of known size with function stubs, not real users or validation of a real LLM. Reproduce with `PYTHONPATH=src python benchmarks/run_checkup_simulation.py --repeats 200`, then `python benchmarks/charts.py`.

## 15. Build order

| Stage | Contents | Status | External inputs |
|---|---|---|---|
| Project setup | Repo, Apache-2.0, ruff, pytest, CI | Done | — |
| Statistical gate | `gate.py` + planted example (tests 1–2) | Done | — |
| Cases and replay | cases, record, agents, replay, cache (tests 3–5) | Done | — |
| LLM judge | llm client, grader, judge + self-check (test 6) | Done (offline; real runs need a key) | LLM API key and budget for real runs |
| PR gate and CLI | config, evaluation, gate report, `init` and `gate` commands, gate workflow template | Done | — |
| Weekly check-up | starter, patterns, propose, snapshots, `checkup` and `propose` commands, check-up workflow (tests 7–9) | Done (verified with Claude Haiku and Opus as judges) | — |
| Doctor and integration guide | `doctor`, "Connect your agent" guide with tested examples | Done | — |
| Examples | Support-bot quickstart with real judge reports; trading example with recorded replies, a look-ahead test and outcome-blind cases | Done | LLM API key and budget (spent) |
| Simulated year | 52 weekly check-ups with planted incidents, 200 repeats; detection, false alarms, proposal accuracy (section 14, item 5) | Done | — |
| Evolve and trial ledger | `evolve` with held-out gating and per-round confidence; trial ledger; `run.confidence` (7.9–7.10) | Done | — |
| Daily-workflow helpers | `promptcheckup.testing`: a pytest-friendly gate and a look-ahead check for time-series agents (6.5); the weekly check-up compares a new prompt with the previous one (7.5 step 4) | Done | — |
| Validation on real agents | Dogfood on LayeredMemoryTrader over real weeks | Later | 2+ weeks of real check-ups |
| Release | README with real numbers, docs, article, `CITATION.cff` | Planned | Release to PyPI |

## 16. Decisions made

| Decision | Choice |
|---|---|
| Product type | Library + CLI + workflow templates |
| First trace integration | None in v1.0 (`@record` + starter cases) |
| Statistics | Paired 95% t confidence interval (exact quantile) with a skew guard in score mode; minimum 20 cases; conservative three-way verdict |
| Judge | Pairwise, order-swapped, with a self-check |
| LLM access | One OpenAI-compatible endpoint; user brings their own key |
| Autonomy | `evolve` iterates proposals but never merges; held-out gating plus Bonferroni per round |
| Case selection | By a fixed rule or recorded traffic, never by outcome |
| License | Apache-2.0 |
| Language | Python 3.11+, standard library only (HTTP via `urllib`; `httpx` was planned but one JSON POST does not need it) |

## 17. Open items

- **Defaults to tune during validation** from measured cost and inconclusive rate:
  - the judge model;
  - the default size (20 cases × 2 samples);
  - the judge's order-consistency threshold (70%);
  - the proposal edit limit (20% of lines).
- **Patent search.** Do a quick check on Google Patents before the public launch.
