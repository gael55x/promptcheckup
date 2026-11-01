# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/gael55x/promptcheckup/releases/tag/v1.0.0
