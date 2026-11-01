"""The only two ways exceptions are handled: translate them, or fall back on purpose."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TypeVar

T = TypeVar("T")
Message = str | Callable[[BaseException], str]


@contextmanager
def errors_as(
    error: type[Exception], messages: Mapping[type[BaseException], Message]
) -> Iterator[None]:
    """Re-raise any listed exception as `error`, keeping the original as its cause.

    A message is a fixed string, or a function of the original exception. Text is used
    as given and never treated as a template. The first matching entry wins, so list
    subclasses before their bases. Anything unlisted passes through unchanged.
    """
    try:
        yield
    except tuple(messages) as cause:
        message = next(text for kind, text in messages.items() if isinstance(cause, kind))
        raise error(message(cause) if callable(message) else message) from cause


def fallback(
    operation: Callable[[], T],
    on_error: Callable[[Exception], T],
    *exceptions: type[Exception],
) -> T:
    """Return an operation's result, or `on_error(exception)` for a listed failure.

    Only recording failures and damaged cache entries may be ignored. Other handlers
    must keep the failure in their result (such as an errored run). Unlisted exceptions
    and failures inside the handler propagate unchanged.
    """
    try:
        return operation()
    except exceptions as error:
        return on_error(error)
