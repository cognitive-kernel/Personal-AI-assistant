import pytest

from app.config import _int_env


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("42", 42), (" 7 ", 7), ("0", 0)],
)
def test_int_env_parses_integer_values(monkeypatch, raw, expected):
    monkeypatch.setenv("TEST_INTEGER_SETTING", raw)
    assert _int_env("TEST_INTEGER_SETTING", 99) == expected


def test_int_env_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("TEST_INTEGER_SETTING", raising=False)
    assert _int_env("TEST_INTEGER_SETTING", 99) == 99


def test_int_env_invalid_value_has_actionable_error_without_echoing_value(monkeypatch):
    monkeypatch.setenv("TEST_INTEGER_SETTING", "not-an-int-secret-looking-value")
    with pytest.raises(ValueError, match="TEST_INTEGER_SETTING must be an integer") as exc:
        _int_env("TEST_INTEGER_SETTING", 99)
    assert "not-an-int-secret-looking-value" not in str(exc.value)
