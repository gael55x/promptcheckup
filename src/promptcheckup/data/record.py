"""Optionally record successful production calls without changing their result."""

import warnings
from collections.abc import Callable
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any

from promptcheckup.data.cases import Case, append_cases, case_id


def record(path: str | Path, *, redact: Callable[[Any], Any] | None = None):
    """Decorate a function, applying optional redaction to copies of its input and output."""

    def decorate(function):
        @wraps(function)
        def wrapped(case: dict[str, Any], *args, **kwargs):
            output = function(case, *args, **kwargs)
            try:
                recorded_input = redact(deepcopy(case)) if redact is not None else case
                recorded_output = redact(deepcopy(output)) if redact is not None else output
                if not isinstance(recorded_input, dict):
                    raise TypeError("redacted input must be a JSON object")
                recorded = Case(
                    case_id(recorded_input), recorded_input, "recorded", recorded_output
                )
                append_cases(Path(path), [recorded])
            except Exception as e:
                # Recording failures must not change a successful production call.
                try:
                    warnings.warn(
                        f"Could not record case to {path}: {e}", RuntimeWarning, stacklevel=2
                    )
                except RuntimeWarning:
                    # Preserve the result even when warnings are configured as errors.
                    pass
            return output

        return wrapped

    return decorate
