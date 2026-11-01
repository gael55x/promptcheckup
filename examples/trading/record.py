"""Record both policies once, with an explicit call plan and confirmation."""

import argparse
import sys
from pathlib import Path

import trader

from promptcheckup.data import load_cases
from promptcheckup.errors import fallback
from promptcheckup.runner import AgentError

ROOT = Path(__file__).resolve().parent
POLICIES = ("prompts/old-policy.md", "prompts/policy.md")


def planned_runs() -> list[tuple[dict, dict]]:
    cases = load_cases(["cases/cases.jsonl"], ROOT)
    prompts = [
        {"prompts/policy.md": (ROOT / path).read_text(encoding="utf-8")} for path in POLICIES
    ]
    return [(case.input, policy) for policy in prompts for case in cases]


def confirm(yes: bool) -> bool:
    if yes:
        return True
    answer = fallback(lambda: input("Proceed? [y/N] "), lambda error: "", EOFError)
    return answer.strip().lower() in ("y", "yes")


def record_pending(yes: bool) -> int:
    runs = planned_runs()
    pending = [
        (case, policy) for case, policy in runs if not trader.recording_path(case, policy).is_file()
    ]
    settings = trader.model_settings()
    print(
        f"Planned: {len(pending)} model calls; model {settings.model}; base URL {settings.base_url}"
    )
    if pending and not confirm(yes):
        print("Cancelled; no model calls.")
        return 0
    for case, policy in pending:
        trader.record_case(case, policy)
    print(f"Recorded {len(pending)}; skipped {len(runs) - len(pending)}.")
    return 0


def failed(error: Exception) -> int:
    print(f"Recording stopped: {error}", file=sys.stderr)
    return 3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="confirm the printed call plan")
    args = parser.parse_args(argv)
    return fallback(lambda: record_pending(args.yes), failed, AgentError, OSError, ValueError)


if __name__ == "__main__":
    raise SystemExit(main())
