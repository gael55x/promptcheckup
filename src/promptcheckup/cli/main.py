"""Argument parsing and one-line command failures."""

import argparse
import sys
from pathlib import Path

from promptcheckup.cli.checkup import weekly
from promptcheckup.cli.config import ConfigError
from promptcheckup.cli.doctor import doctor
from promptcheckup.cli.evolve import evolution
from promptcheckup.cli.gate import gate
from promptcheckup.cli.init import initialize
from promptcheckup.errors import errors_as, fallback
from promptcheckup.llm import LLMError
from promptcheckup.runner import AgentError

COMMANDS = {
    "init": lambda args: initialize(args.dir),
    "gate": gate,
    "checkup": weekly,
    "propose": weekly,
    "doctor": doctor,
    "evolve": evolution,
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigError(message)


def main(argv=None) -> int:
    """Run a command, returning its documented exit status without a traceback."""
    return fallback(lambda: _dispatch(argv), _failure, ConfigError, AgentError, LLMError)


def _dispatch(argv) -> int:
    args = _parser().parse_args(argv)
    with errors_as(
        ConfigError,
        {
            OSError: lambda error: f"project I/O failed: {error}",
            UnicodeError: lambda error: f"invalid project text: {error}",
        },
    ):
        return COMMANDS[args.command](args)


def _parser():
    parser = Parser(prog="promptcheckup")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write starter configuration, cases and workflow")
    init.add_argument("--dir", type=Path, default=Path("."))
    doctor_parser = commands.add_parser("doctor", help="check agent, prompts, scoring and budget")
    doctor_parser.add_argument("--config", type=Path, default=Path("promptcheckup.toml"))
    doctor_parser.add_argument("--no-llm", action="store_true")
    gate_parser = commands.add_parser("gate", help="compare old and new prompt sets")
    _gate_arguments(gate_parser)
    for name in ("checkup", "propose"):
        command = commands.add_parser(name, help=f"run a {name} and gate any proposed edit")
        _common_arguments(command)
        command.add_argument("--proposal-out", type=Path)
    evolution_parser = commands.add_parser("evolve", help="evolve prompts against held-out cases")
    _common_arguments(evolution_parser)
    evolution_parser.add_argument("--rounds", type=int, default=5)
    evolution_parser.add_argument("--holdout", type=float, default=0.3)
    return parser


def _gate_arguments(parser):
    _common_arguments(parser)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--old-ref")
    sources.add_argument("--old", nargs="+", type=Path)
    parser.add_argument("--new", nargs="+", type=Path)


def _common_arguments(parser):
    parser.add_argument("--config", type=Path, default=Path("promptcheckup.toml"))
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", type=Path)


def _failure(error) -> int:
    print(f"promptcheckup: {' '.join(str(error).split())}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
