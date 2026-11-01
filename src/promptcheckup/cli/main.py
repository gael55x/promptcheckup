"""Argument parsing and one-line command failures."""

import argparse
import sys
from pathlib import Path

from promptcheckup.cli.config import ConfigError
from promptcheckup.cli.gate import gate
from promptcheckup.cli.init import initialize
from promptcheckup.errors import errors_as, fallback
from promptcheckup.llm import LLMError
from promptcheckup.runner import AgentError

COMMANDS = {"init": lambda args: initialize(args.dir), "gate": gate}


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
    gate_parser = commands.add_parser("gate", help="compare old and new prompt sets")
    _gate_arguments(gate_parser)
    return parser


def _gate_arguments(parser):
    parser.add_argument("--config", type=Path, default=Path("promptcheckup.toml"))
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--old-ref")
    sources.add_argument("--old", nargs="+", type=Path)
    parser.add_argument("--new", nargs="+", type=Path)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", type=Path)


def _failure(error) -> int:
    print(f"promptcheckup: {' '.join(str(error).split())}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
