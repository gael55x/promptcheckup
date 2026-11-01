"""Translate low-level exceptions into domain errors at a module boundary."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager


@contextmanager
def errors_as(
    error: type[Exception], messages: Mapping[type[BaseException], str]
) -> Iterator[None]:
    """Re-raise any listed exception as `error`, keeping the original as its cause.

    Messages may contain `{error}`, replaced by the original exception's text. The first
    matching entry wins, so list subclasses before their bases. Anything unlisted passes
    through unchanged.
    """
    try:
        yield
    except tuple(messages) as cause:
        template = next(text for kind, text in messages.items() if isinstance(cause, kind))
        raise error(template.replace("{error}", str(cause))) from cause
