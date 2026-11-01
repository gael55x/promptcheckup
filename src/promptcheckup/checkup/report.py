"""Render an evaluation as a headline followed by evidence and provenance."""

import html
import json
import math
from difflib import unified_diff

from promptcheckup.checkup.evaluate import Evaluation

HEADLINES = {
    "better": lambda v, d: (
        f"B is better: {v.wins} of {v.n} cases improved "
        f"(95% CI {v.ci_low:+.{d}f} to {v.ci_high:+.{d}f})."
    ),
    "worse": lambda v, d: (
        f"B is worse: {v.losses} of {v.n} cases regressed "
        f"(95% CI {v.ci_low:+.{d}f} to {v.ci_high:+.{d}f})."
    ),
    "inconclusive": lambda v, d: (
        f"Not sure yet: {v.reason.rstrip('.')}." + NEXT_STEPS.get(v.reason, "")
    ),
}
NEXT_STEPS = {
    "the confidence interval includes zero": " Add more cases or let it run again.",
}

CHECKUP_HEADLINES = (
    (lambda r: r.budget_exhausted, lambda r: "Check-up incomplete: call budget reached."),
    (
        lambda r: r.drift is not None and r.drift.verdict.decision == "worse",
        lambda r: (
            "Your agent got worse this week without a prompt change"
            + (" (agent version changed)" if r.agent_version_changed else "")
            + f": {r.drift.verdict.losses} of {r.drift.verdict.n} cases regressed."
        ),
    ),
    (
        lambda r: r.proposal is not None,
        lambda r: (
            f"A prompt change passed the gate: {_improved(r.proposal_verdict)} "
            "Review the proposed edit before merging."
        ),
    ),
    (
        lambda r: bool(r.patterns),
        lambda r: f"Stable. No drift and {len(r.patterns)} failure pattern(s) found.",
    ),
    (lambda r: True, lambda r: "Stable. No drift and no new failure patterns."),
)


def gate_report(evaluation: Evaluation, *, prompt_hashes, version) -> str:
    """Render only the supplied evaluation and provenance; never perform I/O."""
    digits = _decimals(evaluation.verdict)
    sections = [
        HEADLINES[evaluation.verdict.decision](evaluation.verdict, digits),
        _stats(evaluation.verdict, digits),
        _self_check(evaluation.scores.self_check),
        _top_cases(evaluation, digits),
        _failures(evaluation.errors),
        _usage(evaluation),
        _provenance(prompt_hashes, version),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _decimals(verdict) -> int:
    """Two decimals, or more when scores are so small that two would print zero."""
    largest = max((abs(x) for x in (verdict.mean, verdict.ci_low, verdict.ci_high) if x), default=0)
    return max(2, 1 - math.floor(math.log10(largest))) if largest else 2


def _stats(verdict, digits) -> str:
    interval = (
        "n/a"
        if verdict.ci_low is None
        else f"{verdict.ci_low:+.{digits}f} to {verdict.ci_high:+.{digits}f}"
    )
    return (
        "| n | Mean | 95% CI | Wins | Losses | Ties |\n"
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


def checkup_report(result, *, version) -> str:
    """Render the weekly findings, including incomplete runs and case provenance."""
    headline = next(render(result) for predicate, render in CHECKUP_HEADLINES if predicate(result))
    sections = [
        headline,
        _drift_details(result),
        _pattern_details(result.patterns),
        _proposal_details(result),
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
            + _stats(evaluation.verdict, _decimals(evaluation.verdict))
            + "\n\n"
            + _self_check(evaluation.scores.self_check)
        ).rstrip()
    if result.snapshot_saved:
        return "Drift: first check-up: snapshot saved."
    return "Drift: not completed; no snapshot saved."


def _pattern_details(patterns):
    rows = [
        f"- {_cell(pattern.name)}: {_cell(pattern.description)} "
        f"(cases: {_cell(', '.join(pattern.case_ids))})"
        for pattern in patterns
    ]
    return "Patterns:\n\n" + ("\n".join(rows) or "No failure patterns found.")


def _improved(verdict) -> str:
    digits = _decimals(verdict)
    return (
        f"{verdict.wins} of {verdict.n} cases improved "
        f"(95% CI {verdict.ci_low:+.{digits}f} to {verdict.ci_high:+.{digits}f})."
    )


def _proposal_details(result):
    if result.proposal is not None:
        proposal, verdict = result.proposal, result.proposal_verdict
        return (
            f"Proposal passed the gate: {_cell(proposal.rationale)}\n\n"
            f"Cited cases: {_cell(', '.join(proposal.cited_case_ids))}.\n\n"
            + _stats(verdict, _decimals(verdict))
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


def proposal_report(result, *, originals, version) -> str:
    """Include the gate findings and reviewable file diffs for the workflow's PR body."""
    text = checkup_report(result, version=version)
    if result.proposal is None:
        return text
    diffs = [
        "".join(
            unified_diff(
                originals[name].splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=name,
                tofile=name,
            )
        )
        for name, new in result.proposal.prompts.items()
        if originals[name] != new
    ]
    return text + "\nProposed prompt edits:\n\n<pre>" + html.escape("\n".join(diffs)) + "</pre>\n"
