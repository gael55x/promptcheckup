import pytest

from promptcheckup.errors import errors_as, fallback


class DomainError(RuntimeError):
    pass


def test_fixed_message_replaces_the_error_and_keeps_the_cause():
    translating = errors_as(DomainError, {OSError: "could not read"})

    with pytest.raises(DomainError, match="^could not read$") as caught, translating:
        raise OSError("boom")

    assert isinstance(caught.value.__cause__, OSError)


def test_message_function_receives_the_original_error():
    translating = errors_as(DomainError, {OSError: lambda error: f"could not read: {error}"})

    with pytest.raises(DomainError, match="^could not read: boom$"), translating:
        raise OSError("boom")


def test_text_is_never_reinterpreted_as_a_template():
    # A model's reply or a file path may contain braces; nothing is substituted.
    reply = "the {error} token and {braces}"
    translating = errors_as(DomainError, {ValueError: lambda error: f"bad reply: {reply}"})

    expected = r"^bad reply: the \{error\} token and \{braces\}$"
    with pytest.raises(DomainError, match=expected), translating:
        raise ValueError("boom")


def test_first_matching_entry_wins_for_subclasses():
    translating = errors_as(DomainError, {TimeoutError: "timed out", OSError: "os failure"})

    with pytest.raises(DomainError, match="^timed out$"), translating:
        raise TimeoutError


def test_unlisted_exceptions_pass_through_unchanged():
    with pytest.raises(KeyError), errors_as(DomainError, {ValueError: "bad value"}):
        raise KeyError("x")


def test_no_exception_means_no_effect():
    with errors_as(DomainError, {ValueError: "unused"}):
        result = 1 + 1

    assert result == 2


def test_fallback_returns_success_without_handling_an_error():
    result = object()

    def unexpected(error):
        pytest.fail("successful operations need no fallback")

    assert fallback(lambda: result, unexpected, ValueError) is result


def test_fallback_passes_the_original_exception_to_the_handler():
    error = FileNotFoundError("missing entry")

    def read():
        raise error

    assert fallback(read, lambda cause: cause, OSError, ValueError) is error


def test_fallback_leaves_unlisted_exceptions_unchanged():
    error = KeyError("missing")

    def read():
        raise error

    with pytest.raises(KeyError) as caught:
        fallback(read, lambda cause: None, ValueError)

    assert caught.value is error


def test_fallback_does_not_swallow_handler_failures():
    def fail():
        raise ValueError("operation failed")

    def handle(error):
        raise ValueError("handler failed")

    with pytest.raises(ValueError, match="handler failed"):
        fallback(fail, handle, ValueError)


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit()])
def test_fallback_does_not_swallow_process_control_exceptions(error):
    def stop():
        raise error

    with pytest.raises(type(error)) as caught:
        fallback(stop, lambda cause: None, Exception)

    assert caught.value is error
