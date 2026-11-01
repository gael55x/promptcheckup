"""Optionally record successful production calls without changing their result."""

import warnings
from collections.abc import Callable
from copy import deepcopy
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any

from promptcheckup.data.cases import Case, append_cases, case_id, load_cases
from promptcheckup.errors import fallback

# warnings.warn -> lambda -> fallback -> _warn_recording -> handler lambda -> fallback
# -> wrapped -> the production call site. Pointing there shows users their own code.
CALL_SITE_STACKLEVEL = 7


def record(path: str | Path, *, redact: Callable[[Any], Any] | None = None):
    """Decorate a function, applying optional redaction to copies of its input and output."""

    def decorate(function):
        recorder = _Recorder(Path(path))

        @wraps(function)
        def wrapped(case: dict[str, Any], *args, **kwargs):
            original = fallback(
                lambda: deepcopy(case), lambda error: _warn_recording(path, error), Exception
            )
            output = function(case, *args, **kwargs)
            fallback(
                lambda: _record_case(recorder, original, output, redact),
                lambda error: _warn_recording(path, error),
                Exception,
            )
            return output

        return wrapped

    return decorate


class _Recorder:
    """Load existing IDs once, then serialize this decorator's duplicate checks and writes."""

    def __init__(self, path):
        self.path = path
        self.ids = None
        self.lock = Lock()

    def write(self, case):
        with self.lock:
            if self.ids is None:
                existing = (
                    load_cases([self.path.name], self.path.parent) if self.path.exists() else []
                )
                self.ids = {item.id for item in existing}
            if case.id not in self.ids:
                append_cases(self.path, [case])
                self.ids.add(case.id)


def _record_case(recorder, case, output, redact) -> None:
    if case is None:
        return  # copying failed and was already reported; never record mutated inputs
    recorded_input = redact(deepcopy(case)) if redact is not None else case
    recorded_output = redact(deepcopy(output)) if redact is not None else output
    if not isinstance(recorded_input, dict):
        raise TypeError("redacted input must be a JSON object")
    recorded = Case(case_id(recorded_input), recorded_input, "recorded", recorded_output)
    recorder.write(recorded)


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
