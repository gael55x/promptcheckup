"""Render an evaluation as a headline followed by evidence and provenance."""

import html
import json
import math
from difflib import unified_diff

from promptcheckup.checkup.evaluate import Evaluation
from promptcheckup.checkup.ledger import false_better_expected
from promptcheckup.runner import prompt_hash

HEADLINES = {
    "better": lambda v, d, confidence: (
        f"B is better: {v.wins} of {v.n} cases improved "
        f"({_percent(confidence)}% CI {v.ci_low:+.{d}f} to {v.ci_high:+.{d}f})."
    ),
    "worse": lambda v, d, confidence: (
        f"B is worse: {v.losses} of {v.n} cases regressed "
        f"({_percent(confidence)}% CI {v.ci_low:+.{d}f} to {v.ci_high:+.{d}f})."
    ),
    "inconclusive": lambda v, d, confidence: (
        f"Not sure yet: {v.reason.rstrip('.')}." + NEXT_STEPS.get(v.reason, "")
    ),
}
NEXT_STEPS = {
    "the confidence interval includes zero": " Add more cases or let it run again.",
}

CHECKUP_HEADLINES = (
    (
        lambda r: r.budget_exhausted,
        lambda r, confidence: "Check-up incomplete: call budget reached.",
    ),
    (
        lambda r: (
            r.drift is not None
            and r.drift.verdict.decision == "worse"
            and r.drift_baseline == "previous prompt"
        ),
        lambda r, confidence: (
            "Your agent got worse since the last check-up, which used a different prompt"
            + (" (agent version changed)" if r.agent_version_changed else "")
            + f": {r.drift.verdict.losses} of {r.drift.verdict.n} cases regressed."
        ),
    ),
    (
        lambda r: r.drift is not None and r.drift.verdict.decision == "worse",
        lambda r, confidence: (
            "Your agent got worse this week without a prompt change"
            + (" (agent version changed)" if r.agent_version_changed else "")
            + f": {r.drift.verdict.losses} of {r.drift.verdict.n} cases regressed."
        ),
    ),
    (
        lambda r: r.proposal is not None,
        lambda r, confidence: (
            f"A prompt change passed the gate: {_improved(r.proposal_verdict, confidence)} "
            "Review the proposed edit before merging."
        ),
    ),
    (
        lambda r: bool(r.patterns),
        lambda r, confidence: f"Stable. No drift and {len(r.patterns)} failure pattern(s) found.",
    ),
    (lambda r: True, lambda r, confidence: "Stable. No drift and no new failure patterns."),
)


EVOLVE_HEADLINES = (
    (
        lambda result: result.accepted > 0,
        lambda result: (
            f"Evolved: {result.accepted} of {len(result.rounds)} rounds passed on "
            f"held-out cases ({result.confidence_per_round:.1%} per round)."
        ),
    ),
    (
        lambda result: True,
        lambda result: (
            f"No improvement passed on held-out cases ({len(result.rounds)} "
            f"round{'' if len(result.rounds) == 1 else 's'} tried)."
        ),
    ),
)


def gate_report(
    evaluation: Evaluation, *, prompt_hashes, version, trial_count=0, confidence=0.95
) -> str:
    """Render only the supplied evaluation and provenance; never perform I/O."""
    digits = _decimals(evaluation.verdict)
    sections = [
        HEADLINES[evaluation.verdict.decision](evaluation.verdict, digits, confidence),
        _stats(evaluation.verdict, digits, confidence),
        _self_check(evaluation.scores.self_check),
        _top_cases(evaluation, digits),
        _failures(evaluation.errors),
        _usage(evaluation),
        _provenance(prompt_hashes, version),
        _trial_details(trial_count, confidence),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _percent(confidence) -> str:
    """95% stays "95"; per-round levels such as 0.98333 show as "98.3"."""
    return f"{100 * confidence:.1f}".removesuffix(".0")


def _decimals(verdict) -> int:
    """Two decimals, or more when scores are so small that two would print zero."""
    largest = max((abs(x) for x in (verdict.mean, verdict.ci_low, verdict.ci_high) if x), default=0)
    return max(2, 1 - math.floor(math.log10(largest))) if largest else 2


def _stats(verdict, digits, confidence=0.95) -> str:
    interval = (
        "n/a"
        if verdict.ci_low is None
        else f"{verdict.ci_low:+.{digits}f} to {verdict.ci_high:+.{digits}f}"
    )
    return (
        f"| n | Mean | {_percent(confidence)}% CI | Wins | Losses | Ties |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| {verdict.n} | {verdict.mean:+.{digits}f} | {interval} | "
        f"{verdict.wins} | {verdict.losses} | {verdict.ties} |"
    )


def _self_check(check) -> str:
    if check is None:
        return ""
    line = f"Self-check: {'reliable' if check.reliable else 'unreliable'}; "
    line += f"consistency {check.consistency:.1%}. {_cell(check.reason)}."
    if check.reliable:
        return line
    return line + (
        " The judge is unreliable for these criteria: "
        "try sharper criteria or a stronger judge model."
    )


def _top_cases(evaluation, digits) -> str:
    top = sorted(evaluation.scores.per_case, key=lambda item: abs(item[1]), reverse=True)[:3]
    if not top:
        return ""
    outputs = _outputs(evaluation.replay.runs)
    rows = [
        _case_row(key, difference, outputs.get(key, (None, None)), digits)
        for key, difference in top
    ]
    return (
        "Largest changes (first successful paired sample shown):\n\n"
        "| Case | Difference | A | B |\n| --- | --- | --- | --- |\n" + "\n".join(rows)
    )


def _outputs(runs) -> dict:
    pairs = {}
    for run in runs:
        if run.error is None:
            pairs.setdefault((run.case_id, run.sample), {})[run.prompt_set] = run.output
    outputs = {}
    for (case_id, _), pair in pairs.items():
        if "A" in pair and "B" in pair:
            outputs.setdefault(case_id, (pair["A"], pair["B"]))
    return outputs


def _case_row(key, difference, outputs, digits) -> str:
    a, b = outputs
    return f"| {_cell(key)} | {difference:+.{digits}f} | {_output(a)} | {_output(b)} |"


def _output(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    clipped = text[:300] + ("…" if len(text) > 300 else "")
    return _cell(clipped)


def _cell(text) -> str:
    return (
        html.escape(str(text), quote=False)
        .replace("|", r"\|")
        .replace("\n", "<br>")
        .replace("\r", "")
    )


def _failures(errors) -> str:
    if not errors:
        return ""
    rows = [
        f"| {_cell(run.case_id)} | {run.prompt_set} | {run.sample} | {_cell(run.error)} |"
        for run in errors
    ]
    return (
        "Failed runs:\n\n| Case | Prompt | Sample | Error |\n| --- | --- | --- | --- |\n"
        + "\n".join(rows)
    )


def _usage(evaluation) -> str:
    scores = evaluation.scores
    tokens = "unavailable"
    if scores.prompt_tokens is not None:
        tokens = f"{scores.prompt_tokens} input, {scores.completion_tokens} output"
    return (
        f"Calls: {evaluation.replay.calls} agent, {scores.judge_calls} judge. "
        f"Tokens: {tokens} (judge); unavailable (agent protocol). "
        f"Judge model: {_cell(scores.model or 'not reported')}."
    )


def _provenance(hashes, version) -> str:
    prompts = "; ".join(f"{_cell(name)}: {_cell(value)}" for name, value in hashes.items())
    return f"Prompt hashes: {prompts}.\n\npromptcheckup version: {_cell(version)}."


def checkup_report(result, *, version, confidence=0.95) -> str:
    """Render the weekly findings, including incomplete runs and case provenance."""
    headline = next(
        render(result, confidence) for predicate, render in CHECKUP_HEADLINES if predicate(result)
    )
    sections = [
        headline,
        _drift_details(result),
        _pattern_details(result.patterns),
        _proposal_details(result, confidence),
        _synthetic_share(result.synthetic_share),
        _failures(result.errors),
        (
            f"Calls: {result.calls} (agent and LLM combined). "
            f"Judge model: {_cell(result.model or 'not reported')}."
        ),
        _provenance({"current": result.prompts_hash}, version),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _drift_details(result):
    if not result.drift_checked:
        return "Drift: not checked (proposal only)."
    if result.drift is not None:
        evaluation = result.drift
        return (
            f"Drift comparison: {evaluation.verdict.decision}; "
            f"{_cell(evaluation.verdict.reason)}.\n\n"
            f"Agent version changed: {'yes' if result.agent_version_changed else 'no'}.\n\n"
            + _previous_prompt_details(result)
            + _stats(evaluation.verdict, _decimals(evaluation.verdict))
            + "\n\n"
            + _self_check(evaluation.scores.self_check)
        ).rstrip()
    if result.snapshot_saved:
        return "Drift: first check-up: snapshot saved."
    return "Drift: not completed; no snapshot saved."


def _previous_prompt_details(result):
    if result.drift_baseline != "previous prompt":
        return ""
    return (
        f"Previous prompt hash: {_cell(result.previous_prompts_hash)}. "
        f"Current prompt hash: {_cell(result.prompts_hash)}.\n\n"
        "The prompt and the environment may both have changed.\n\n"
    )


def _pattern_details(patterns):
    rows = [
        f"- {_cell(pattern.name)}: {_cell(pattern.description)} "
        f"(cases: {_cell(', '.join(pattern.case_ids))})"
        for pattern in patterns
    ]
    return "Patterns:\n\n" + ("\n".join(rows) or "No failure patterns found.")


def _improved(verdict, confidence=0.95) -> str:
    digits = _decimals(verdict)
    return (
        f"{verdict.wins} of {verdict.n} cases improved "
        f"({_percent(confidence)}% CI "
        f"{verdict.ci_low:+.{digits}f} to {verdict.ci_high:+.{digits}f})."
    )


def _proposal_details(result, confidence=0.95):
    if result.proposal is not None:
        proposal, verdict = result.proposal, result.proposal_verdict
        return (
            f"Proposal passed the gate: {_cell(proposal.rationale)}\n\n"
            f"Cited cases: {_cell(', '.join(proposal.cited_case_ids))}.\n\n"
            + _stats(verdict, _decimals(verdict), confidence)
        )
    if result.proposal_rejection is not None:
        return f"Proposal rejected: {_cell(result.proposal_rejection)}."
    if result.proposal_verdict is not None:
        return f"A proposed fix did not pass the gate: {_cell(result.proposal_verdict.reason)}."
    if result.budget_exhausted:
        return (
            "Proposal not completed: call budget reached; raise run.max_calls or use fewer cases."
        )
    return "No proposal: no failing cases."


def _synthetic_share(share):
    note = " Replace synthetic cases with recorded ones." if share > 0 else ""
    return f"Synthetic cases: {share:.0%}.{note}"


def proposal_report(result, *, originals, version, confidence=0.95) -> str:
    """Include the gate findings and reviewable file diffs for the workflow's PR body."""
    text = checkup_report(result, version=version, confidence=confidence)
    if result.proposal is None:
        return text
    return text + _prompt_diff(originals, result.proposal.prompts)


def _prompt_diff(originals, prompts):
    diffs = [
        "".join(
            unified_diff(
                originals[name].splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=name,
                tofile=name,
            )
        )
        for name, new in prompts.items()
        if originals[name] != new
    ]
    return "\nProposed prompt edits:\n\n<pre>" + html.escape("\n".join(diffs)) + "</pre>\n"


def evolve_report(result, *, originals, version) -> str:
    """Render the complete lineage, including candidates that failed the holdout gate."""
    headline = next(render(result) for predicate, render in EVOLVE_HEADLINES if predicate(result))
    sections = [
        headline,
        f"Learning cases: {len(result.learning_ids)}; held-out cases: {len(result.holdout_ids)}.",
        *[_round_details(item, result.confidence_per_round) for item in result.rounds],
        "Call budget reached; only completed rounds are shown." if result.budget_exhausted else "",
        _failures(result.errors),
        f"Calls: {result.calls} (agent and LLM combined). "
        f"Judge model: {_cell(result.model or 'not reported')}.",
        _provenance(
            {"original": prompt_hash(originals), "final": prompt_hash(result.final_prompts)},
            version,
        ),
        "Held-out cases were never shown to the proposer; each round had to pass at "
        f"{result.confidence_per_round:.1%} confidence to limit false improvements across the run. "
        "The holdout is reused, so fresh cases remain the strongest check; "
        "the 99.9% cap makes the confidence guarantee approximate for long runs.",
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _round_details(item, confidence):
    sections = [f"Round {item.index}: {'accepted' if item.accepted else 'not accepted'}."]
    if item.proposal is not None:
        sections.extend(
            [
                f"Rationale: {_cell(item.proposal.rationale)}",
                f"Cited cases: {_cell(', '.join(item.proposal.cited_case_ids))}.",
            ]
        )
    if item.verdict is not None:
        sections.extend(
            [
                f"Held-out gate: {item.verdict.decision}; {_cell(item.verdict.reason)}.",
                _stats(item.verdict, _decimals(item.verdict), confidence),
            ]
        )
    if item.proposal is not None:
        sections.append(_prompt_diff(item.prompts_before, item.proposal.prompts))
    if item.rejection is not None:
        sections.append(f"Proposal rejected: {_cell(item.rejection)}.")
    return "\n\n".join(sections)


def _trial_details(count, confidence):
    if count < 2:
        return ""
    expected = false_better_expected(count, confidence)
    return (
        f"Trials on these cases: {count} distinct variants. At {_percent(confidence)}% confidence, "
        f"about {expected:.3g} false 'better' verdicts are expected across them; "
        "consider fresh cases or a stricter `run.confidence`."
    )
