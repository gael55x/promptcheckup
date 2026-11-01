import pytest

from promptcheckup.errors import errors_as


class DomainError(RuntimeError):
    pass


def test_maps_each_exception_type_to_its_message():
    translating = errors_as(DomainError, {OSError: "could not read: {error}"})

    with pytest.raises(DomainError, match="^could not read: boom$") as caught, translating:
        raise OSError("boom")

    assert isinstance(caught.value.__cause__, OSError)


def test_first_matching_entry_wins_for_subclasses():
    translating = errors_as(DomainError, {TimeoutError: "timed out", OSError: "os failure"})

    with pytest.raises(DomainError, match="^timed out$"), translating:
        raise TimeoutError


def test_unlisted_exceptions_pass_through_unchanged():
    with pytest.raises(KeyError), errors_as(DomainError, {ValueError: "bad value"}):
        raise KeyError("x")


def test_braces_in_messages_are_kept_literally():
    translating = errors_as(DomainError, {ValueError: "bad entry 'a{b}'"})

    with pytest.raises(DomainError, match=r"^bad entry 'a\{b\}'$"), translating:
        raise ValueError


def test_no_exception_means_no_effect():
    with errors_as(DomainError, {ValueError: "unused"}):
        result = 1 + 1

    assert result == 2
