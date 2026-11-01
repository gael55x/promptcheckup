"""Optionally record successful production calls without changing their result."""

import warnings
from collections.abc import Callable
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any

from promptcheckup.data.cases import Case, append_cases, case_id
from promptcheckup.errors import fallback

# warnings.warn -> lambda -> fallback -> _warn_recording -> handler lambda -> fallback
# -> wrapped -> the production call site. Pointing there shows users their own code.
CALL_SITE_STACKLEVEL = 7


def record(path: str | Path, *, redact: Callable[[Any], Any] | None = None):
    """Decorate a function, applying optional redaction to copies of its input and output."""

    def decorate(function):
        @wraps(function)
        def wrapped(case: dict[str, Any], *args, **kwargs):
            output = function(case, *args, **kwargs)
            fallback(
                lambda: _record_case(path, case, output, redact),
                lambda error: _warn_recording(path, error),
                Exception,
            )
            return output

        return wrapped

    return decorate


def _record_case(path, case, output, redact) -> None:
    recorded_input = redact(deepcopy(case)) if redact is not None else case
    recorded_output = redact(deepcopy(output)) if redact is not None else output
    if not isinstance(recorded_input, dict):
        raise TypeError("redacted input must be a JSON object")
    recorded = Case(case_id(recorded_input), recorded_input, "recorded", recorded_output)
    append_cases(Path(path), [recorded])


def _warn_recording(path, error: Exception) -> None:
    # Preserve the production result even when warnings are configured as errors.
    fallback(
        lambda: warnings.warn(
            f"Could not record case to {path}: {error}",
            RuntimeWarning,
            stacklevel=CALL_SITE_STACKLEVEL,
        ),
        lambda warning: None,
        RuntimeWarning,
    )
