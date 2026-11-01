"""A seeded year through the real weekly check-up, with planted effects, no model or network.

CSV first, then render it with benchmarks/charts.py, as for the gate benchmark:

    PYTHONPATH=src python benchmarks/run_checkup_simulation.py --repeats 200
    python benchmarks/charts.py

Cases are fixed across weeks AND repeats. Gaussian noise has SD 1 by default; every
agent invocation gets an independent draw seeded by (repeat, week, case id, sample).
Sample is the per-case invocation ordinal, including the current run and gate A/B.
No replay cache is used. The tiny agent directly implements the library's in-process
protocol, avoiding per-call timeout threads for this bounded local arithmetic.

The provider drop persists. The real fix adds 1 noise SD to initial quality; the
unreviewed version is 1 SD below initial quality. Harmless provider wording is an
unscored output field. Only the two scripted proposal weeks submit changed text;
otherwise the stub returns unchanged text, rejected by the library before gating.
Kept edits are treated as reviewed and merged before the next weekly run. With the
default 50 cases and holdout share 0.5, proposals learn on 25 cases and are gated on
25 held-out cases. The unreviewed version therefore drops 2 SD after a kept real
fix, or 1 SD otherwise; derive each repeat's stratum from its real_fix outcome.

Detection windows include the incident week and its next two weeks (or the available
tail for short runs). A later drift flag is temporal association, not attribution:
the library replaces its snapshot each week, so it does not accumulate evidence of
a persistent drop. Quiet weeks in those windows still count as false alarms.
Quiet-week rates exclude each repeat's first week, which has no baseline to compare.
Check-ups compare with the latest snapshot, whether it uses the same or a previous
prompt. The separate null check also gates on the 25 held-out cases by default.
"""

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from promptcheckup.checkup.checkup import checkup
from promptcheckup.checkup.evaluate import evaluate, score_mode
from promptcheckup.checkup.split import split_cases
from promptcheckup.data import Case

RESULTS = Path(__file__).parent / "results"
PROMPT_TEXT = (
    "version: {version}\n"
    "Answer each request carefully using the available evidence. State relevant assumptions "
    "and explain the result clearly. Check the details before giving a final answer. "
    "Use the same criteria for every case and prefer accurate reasoning over confident guesses.\n"
)


NULL_SEED = 10_000  # null-check seeds start beyond any year's repeat seeds
HOLDOUT_SHARE = 0.5  # the CLI default for run.holdout


@dataclass(frozen=True)
class Incident:
    kind: str
    week: int
    label: str


INCIDENTS = (
    Incident("harmful_provider", 8, "Harmful provider change"),
    Incident("harmless_provider", 16, "Harmless provider change"),
    Incident("real_fix", 24, "Real fix proposed"),
    Incident("useless_fix", 32, "Useless fix proposed"),
    Incident("unreviewed_regression", 40, "Unreviewed prompt regression"),
)


@dataclass(frozen=True)
class Config:
    weeks: int = 52
    repeats: int = 200
    cases: int = 50
    samples: int = 2
    noise_sd: float = 1.0
    provider_effect_sd: float = 0.5
    fix_effect_sd: float = 1.0
    regression_effect_sd: float = 1.0
    incidents: tuple[Incident, ...] = INCIDENTS


@dataclass(frozen=True)
class Week:
    repeat: int
    week: int
    environment: float
    provider: str


CONFIG_RULES = (
    (
        lambda c: min(c.weeks, c.repeats, c.samples) >= 1,
        "weeks, repeats and samples must be positive",
    ),
    (lambda c: c.cases >= 20, "at least 20 cases are needed by the default gate"),
    (
        lambda c: math.isfinite(c.noise_sd) and c.noise_sd > 0,
        "noise_sd must be finite and positive",
    ),
    (
        lambda c: all(
            math.isfinite(value) and value >= 0
            for value in (c.provider_effect_sd, c.fix_effect_sd, c.regression_effect_sd)
        ),
        "effects must be finite and nonnegative",
    ),
    (
        lambda c: tuple(i.kind for i in c.incidents) == tuple(i.kind for i in INCIDENTS),
        "provide the five incidents in their original order",
    ),
    (
        lambda c: (
            [i.week for i in c.incidents] == sorted({i.week for i in c.incidents})
            and min(i.week for i in c.incidents) >= 2
        ),
        "incident weeks must be distinct, increasing and after the baseline week",
    ),
)
QUALITY = MappingProxyType(
    {0: lambda c: -c.regression_effect_sd, 1: lambda c: 0, 2: lambda c: c.fix_effect_sd}
)
EDITS = MappingProxyType(
    {
        "real_fix": lambda text: text.replace("version: 1", "version: 2", 1),
        "useless_fix": lambda text: text.replace("carefully", "precisely", 1),
    }
)
SIGNALS = MappingProxyType(
    {
        "harmful_provider": ("drift flagged", lambda r: r["drift_decision"] == "worse"),
        "harmless_provider": ("drift (false alarm)", lambda r: r["drift_decision"] == "worse"),
        "real_fix": (
            "fix kept",
            lambda r: r["incident"] == "real_fix" and r["proposal_outcome"] == "kept",
        ),
        "useless_fix": (
            "fix rejected",
            lambda r: r["incident"] == "useless_fix" and r["proposal_outcome"] == "rejected",
        ),
        "unreviewed_regression": ("drift flagged", lambda r: r["drift_decision"] == "worse"),
    }
)
LLM_REPLIES = MappingProxyType(
    {
        "failures": lambda prompt, incident: pattern_reply(prompt),
        "examples": lambda prompt, incident: proposal_reply(prompt, incident),
    }
)


def validate(config: Config) -> None:
    for predicate, message in CONFIG_RULES:
        if not predicate(config):
            raise ValueError(message)


def make_cases(config: Config) -> list[Case]:
    return [
        Case(
            f"case-{index:02d}",
            {"id": f"case-{index:02d}", "difficulty": random.Random(f"case-{index}").uniform(0, 1)},
            source="synthetic",
        )
        for index in range(config.cases)
    ]


def prompts(version: int) -> dict[str, str]:
    return {"system": PROMPT_TEXT.format(version=version)}


def prompt_version(current: dict[str, str]) -> int:
    return int(current["system"].splitlines()[0].split(":")[1])


def make_agent(config: Config, week: Week, calls: list):
    counts = Counter()

    def run(case, current):
        sample = counts[case["id"]]
        counts[case["id"]] += 1
        calls.append(case["id"])
        rng = random.Random(f"{week.repeat}:{week.week}:{case['id']}:{sample}")
        base = QUALITY[prompt_version(current)](config) * config.noise_sd
        quality = base + week.environment + rng.gauss(0, config.noise_sd)
        return {"quality": quality, "wording": week.provider}

    return run


def score(case, output) -> float:
    return output["quality"] - case["difficulty"]


def block(prompt: str, name: str):
    return json.loads(prompt.split(f"<{name}>\n")[1].split(f"\n</{name}>")[0])


def pattern_reply(prompt: str) -> dict:
    return {
        "patterns": [
            {
                "name": "Low quality",
                "description": "These cases have the lowest measured scores.",
                "case_ids": [row[0] for row in block(prompt, "failures")],
            }
        ]
    }


def proposal_reply(prompt: str, incident: str) -> dict:
    original = block(prompt, "prompts")["system"]
    edit = EDITS.get(incident, lambda text: text)
    return {
        "prompts": {"system": edit(original)},
        "rationale": "Address the cited low-scoring cases.",
        "cited_case_ids": [row[0] for row in block(prompt, "examples")],
    }


def make_llm(incident: str, calls: list):
    def llm(prompt):
        kind = next(key for key in LLM_REPLIES if f"<{key}>\n" in prompt)
        calls.append(kind)
        return json.dumps(LLM_REPLIES[kind](prompt, incident))

    return llm


def week_context(config: Config, repeat: int, week: int) -> Week:
    schedule = {incident.kind: incident.week for incident in config.incidents}
    environment = (
        -config.provider_effect_sd * config.noise_sd * (week >= schedule["harmful_provider"])
    )
    provider = "reworded" if week >= schedule["harmless_provider"] else "original"
    return Week(repeat, week, environment, provider)


def run_week(config, context, cases, current, incident, directory):
    agent_calls, llm_calls = [], []
    run = make_agent(config, context, agent_calls)
    result = checkup(
        lambda case, given, timeout_s: run(case, given),
        cases,
        current,
        make_llm(incident, llm_calls),
        directory=directory,
        score_fn=score,
        samples=config.samples,
        max_calls=3 * config.cases * config.samples + 2,
        agent_version=context.provider,
        holdout_share=HOLDOUT_SHARE,
    )
    if result.budget_exhausted or result.errors or not result.snapshot_saved:
        raise RuntimeError("simulation check-up failed or did not save a complete snapshot")
    row = result_row(context, incident, current, result, agent_calls, llm_calls)
    following = result.proposal.prompts if result.proposal is not None else current
    return row, following


def result_row(context, incident, current, result, agent_calls, llm_calls) -> dict:
    outcome = "kept" if result.proposal is not None else "rejected"
    return {
        "repeat": context.repeat,
        "week": context.week,
        "incident": incident,
        "drift_decision": result.drift.verdict.decision if result.drift else "no_baseline",
        "proposal_outcome": outcome if result.proposal_verdict else "not_proposed",
        "proposal_decision": result.proposal_verdict.decision if result.proposal_verdict else "",
        "calls": result.calls,
        "agent_calls": len(agent_calls),
        "llm_calls": len(llm_calls),
        "prompt_version": prompt_version(current),
        "prompt_hash": result.prompts_hash,
        "environment_effect": context.environment,
    }


def year_rows(config: Config, repeat: int, cases: list[Case], directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=False)  # never contaminate a rerun with old snapshots
    schedule = {incident.week: incident.kind for incident in config.incidents}
    current, rows = prompts(1), []
    for week in range(1, config.weeks + 1):
        incident = schedule.get(week, "quiet")
        if incident == "unreviewed_regression":
            current = prompts(0)
        context = week_context(config, repeat, week)
        row, current = run_week(config, context, cases, current, incident, directory)
        rows.append(row)
    return rows


def simulation_rows(config: Config, directory: Path) -> list[dict]:
    """Run seeds 0..R-1 into fresh per-repeat directories beneath a temporary root."""
    validate(config)
    cases = make_cases(config)
    return [
        row
        for repeat in range(config.repeats)
        for row in year_rows(config, repeat, cases, Path(directory) / f"repeat-{repeat:03d}")
    ]


def rate(values):
    return sum(values) / len(values) if values else None


def window_signal(event, by_repeat, predicate, weeks) -> bool:
    return any(
        predicate(row)
        for row in by_repeat[event["repeat"]]
        if event["week"] <= row["week"] <= event["week"] + weeks
    )


def incident_rates(incident, rows, by_repeat) -> dict:
    events = [row for row in rows if row["incident"] == incident.kind]
    signal, predicate = SIGNALS[incident.kind]
    return {
        "incident": incident.kind,
        "label": incident.label,
        "signal": signal,
        "same_week": rate([predicate(row) for row in events]),
        "within_two_weeks": rate([window_signal(row, by_repeat, predicate, 2) for row in events]),
    }


def metrics(rows: list[dict]) -> dict:
    by_repeat = defaultdict(list)
    for row in rows:
        by_repeat[row["repeat"]].append(row)
    incidents = [incident_rates(incident, rows, by_repeat) for incident in INCIDENTS]
    by_kind = {item["incident"]: item for item in incidents}
    quiet = [
        row["drift_decision"] == "worse"
        for row in rows
        if row["incident"] == "quiet" and row["drift_decision"] != "no_baseline"
    ]
    return {
        "incidents": incidents,
        "quiet_false_alarm_rate": rate(quiet),
        "false_alarms_per_year": sum(quiet) / len(by_repeat) if by_repeat else None,
        "real_fixes_kept": by_kind["real_fix"]["same_week"],
        "useless_fixes_rejected": by_kind["useless_fix"]["same_week"],
        "unreviewed_regressions_noticed": by_kind["unreviewed_regression"]["same_week"],
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")


def percentage(value) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def print_summary(config: Config, rows: list[dict]) -> None:
    result = metrics(rows)
    print(
        f"\n{config.repeats} repeats x {config.weeks} weeks; {config.cases} fixed cases; "
        f"{config.samples} samples; provider drop {config.provider_effect_sd:g} noise SD"
    )
    print(f"{'Incident':30} {'Measured signal':20} {'That week':>10} {'Through +2w':>12}")
    for item in result["incidents"]:
        print(
            f"{item['label']:30} {item['signal']:20} "
            f"{percentage(item['same_week']):>10} {percentage(item['within_two_weeks']):>12}"
        )
    print(f"Quiet-week false alarms: {percentage(result['quiet_false_alarm_rate'])}")
    print(f"Expected false alarms per simulated year: {result['false_alarms_per_year']:.3f}")
    print(f"Calls: {sum(row['calls'] for row in rows):,} (agent + plain-function LLM stubs)")
    print("Windows include weeks w..w+2; later drift flags are not proof of delayed detection.")
    print("Planted effects of known size, not real users; no real model or network.")


def useless_fix_null_check(config: Config, trials: int) -> dict[str, int]:
    """Gate the week-32 useless edit many times, outside the year, to measure false "better".

    The year has only one useless-fix week per repeat, so its rate is noisy; this checks the
    same comparison with many independent seeds through the library's real evaluation.
    """
    current = prompts(2)
    useless = {"system": EDITS["useless_fix"](current["system"])}
    counts = dict.fromkeys(("better", "worse", "inconclusive"), 0)
    for trial in range(trials):
        counts[null_trial(config, trial, current, useless)] += 1
    return counts


def null_trial(config: Config, trial: int, current: dict, useless: dict) -> str:
    run = make_agent(config, Week(NULL_SEED + trial, 32, 0.0, "original"), [])
    return evaluate(
        lambda case, given, timeout_s: run(case, given),
        split_cases(make_cases(config), HOLDOUT_SHARE)[1],  # the week's gate cases
        current,
        useless,
        score_mode(score),
        samples=config.samples,
        timeout_s=5,
        max_calls=4 * config.cases * config.samples,
        min_pairs=20,
        cache=None,
    ).verdict.decision


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--provider-effect-sd", type=float, default=0.5)
    parser.add_argument("--null-trials", type=int, default=2000)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = arguments(argv)
    config = Config(repeats=args.repeats, provider_effect_sd=args.provider_effect_sd)
    with TemporaryDirectory(prefix="promptcheckup-simulation-") as directory:
        rows = simulation_rows(config, Path(directory))
    write_csv(RESULTS / "checkup_simulation.csv", rows)
    print_summary(config, rows)
    if args.null_trials:
        counts = useless_fix_null_check(config, args.null_trials)
        write_csv(
            RESULTS / "useless_fix_null_check.csv",
            [
                {"decision": name, "count": count, "trials": args.null_trials}
                for name, count in counts.items()
            ],
        )
        print(
            f"Useless edit gated {args.null_trials} times: "
            + ", ".join(f"{name} {count / args.null_trials:.2%}" for name, count in counts.items())
        )


if __name__ == "__main__":
    main()
