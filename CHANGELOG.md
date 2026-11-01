# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-01

This release fixes three ways 1.0.x could report "better" for a prompt that wasn't: when runs failed, when the agent changed its own input, and when a cached old-prompt answer was paired with a fresh new-prompt answer. If a 1.0.x gate report listed errors, or your agent modifies the case it receives, run that gate again.

### Changed

- Any failed run, under either prompt, turns a "better" verdict into "not sure yet" and says why. Failed pairs used to be dropped.
- Cached answers are reused only when the cache holds both prompts' answers for a case and sample; otherwise both run again back to back. The cache key now includes the case input.
- `checkup` and `propose` write proposals from one part of your cases and gate them on the rest. The new `run.holdout` setting defaults to 0.5, so a proposal needs about 40 cases before it can be tested; with fewer, the report shows it untested and `doctor` warns.
- The split used by `checkup`, `propose` and `evolve` holds out an exact number of cases, chosen by a SHA-256 hash of their IDs. Any ID now works. Splits differ from 1.0.x.
- The check-up compares with the latest snapshot, whatever its prompt, so a rollback is checked against last week.
- The check-up says "Stable" only after a clean drift comparison. The first run, partly failed runs and runs where every call failed (exit 3) have their own headlines.

### Fixed

- Each agent call gets its own copy of the case input and prompts, and stored answers are copied.
- `evolve` no longer crashes on case IDs that aren't hexadecimal.
- `@record` copies the input before the call and skips duplicate IDs, so its files always load.
- Redaction now covers agent error text and answers loaded from older snapshots.
- `assert_no_lookahead` also changes each future number at random and removes future rows, so it catches a view that compares two future values.
- A "not sure yet" report suggests adding cases instead of running again.
- Gate reports show how often the judge gave the same answer in both orders.
- Documentation: stated the assumptions behind each simulated result, split the unreviewed-regression result by size, corrected the false "better" worst case, the privacy FAQ, the cost cap, the `evolve` confidence description, and several places where the design described the code wrongly. Added GEPA to `CITATION.cff`.

## [1.0.1] - 2026-11-01

### Fixed

- **Workflow templates install the published package.** The templates written by `init` now install `promptcheckup>=1,<2` from PyPI instead of the repository's default branch, so CI installs the release published to PyPI through trusted publishing.
- **README corrections:**
  - the false "better" rate is 2.2–2.7% on symmetric noise, and 2.0% falling to almost 0% on crash-skewed P&L;
  - ten independent tests at a nominal 2.5% rate carry about a 22% chance of a false "better" (not 40%);
  - `evolve` uses a fixed confidence based on the planned rounds; its nominal bound requires calibrated gates and a non-binding confidence cap.
- **Integration guide:** installs from PyPI.

### Changed

- **The README is rewritten in a plainer voice.** It adds a table of how many cases are needed to detect small, medium and large improvements, and a section on what the results mean in practice. Tests check the README's numbers against the committed CSVs.

## [1.0.0] - 2026-11-01

The first public release.

### Added

- **Statistical gate.** A paired 95% t confidence interval with an exact Student t quantile, at least 20 cases, and three verdicts: better, worse, or not sure yet. A skew guard refuses "better" when a few large losses hide among small gains.
- **Paired replay.** Every case runs through both prompts back to back, so shared noise cancels. Outputs are cached, calls are capped, and errors are reported, never dropped.
- **Agents in any language.** A Python `module:function`, or any command speaking JSON over stdin and stdout. `@record` captures real cases from production, with redaction.
- **LLM judge.** Pairwise, order-swapped comparisons with a self-check that compares the old prompt with itself; an unreliable judge downgrades the verdict. Untrusted data cannot close its prompt block. A judge may reason before its JSON verdict; no temperature is sent unless configured.
- **`promptcheckup gate`** compares the prompts in a pull request with its base branch, with a workflow template that comments on the pull request.
- **`promptcheckup checkup`,** the weekly check-up:
  - drift against last week's snapshot, including after a prompt change;
  - starter cases when none exist;
  - failure patterns;
  - one small proposed edit that must pass the gate;
  - a workflow template that posts to one issue and opens a pull request. It never merges.
- **`promptcheckup evolve`:** repeated proposals, learned on fixed-rule learning cases and gated on held-out cases at a stricter confidence for every extra round. It never merges.
- **Trial ledger:** reports how many prompt variants were tried on the same cases and how many false "better" verdicts that many trials would produce by chance. `run.confidence` sets the base confidence.
- **`promptcheckup doctor`:** checks a project's wiring with two agent calls and at most one judge call, including whether the agent really uses the prompts it is given.
- **`promptcheckup.testing`:** `gate`, `assert_better`, `assert_not_worse` and `assert_no_lookahead`, for use in an existing test suite.
- **Examples:**
  - a support-bot quickstart with real reports from three hosted judges;
  - a trading agent on a seeded synthetic market, with recorded replies, a look-ahead test and outcome-blind cases;
  - a planted example comparing naive and paired evaluation.
- **Benchmarks:** false-positive and power simulations of the gate, and a simulated year of weekly check-ups with planted incidents.
- **Documentation:** a guide for connecting an agent, and the full design in `DESIGN.md`.

[1.1.0]: https://github.com/gael55x/promptcheckup/releases/tag/v1.1.0
[1.0.1]: https://github.com/gael55x/promptcheckup/releases/tag/v1.0.1
[1.0.0]: https://github.com/gael55x/promptcheckup/releases/tag/v1.0.0
