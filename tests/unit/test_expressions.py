import pytest

from ai_sdr.treeflow.expressions import (
    ExpressionError,
    eval_bool,
)


def test_simple_true_literal() -> None:
    assert eval_bool("true", {}) is True


def test_comparison_against_number() -> None:
    assert eval_bool("faturamento >= 30000", {"faturamento": 50000}) is True
    assert eval_bool("faturamento >= 30000", {"faturamento": 1000}) is False


def test_boolean_operators() -> None:
    ctx = {"a": True, "b": False, "n": 5}
    assert eval_bool("a and n > 0", ctx) is True
    assert eval_bool("a and b", ctx) is False
    assert eval_bool("not b", ctx) is True


def test_in_operator() -> None:
    assert eval_bool("'sim' in resposta", {"resposta": "sim, claro"}) is True


def test_is_set_helper() -> None:
    assert eval_bool("is_set('email')", {"email": "x@y.com"}) is True
    assert eval_bool("is_set('email')", {"email": None}) is False
    assert eval_bool("is_set('email')", {}) is False


def test_missing_name_treated_as_none() -> None:
    # ergonomics: a transition referencing a not-yet-collected field should be False, not crash
    assert eval_bool("faturamento >= 30000", {}) is False


def test_attribute_access_blocked() -> None:
    with pytest.raises(ExpressionError):
        eval_bool("(1).__class__", {})


def test_function_call_blocked() -> None:
    with pytest.raises(ExpressionError):
        eval_bool("len([1,2,3]) > 0", {})  # len is not whitelisted


def test_dunder_name_blocked() -> None:
    with pytest.raises(ExpressionError):
        eval_bool("__import__", {})


def test_non_boolean_result_coerced() -> None:
    # truthy values coerce to True
    assert eval_bool("1", {}) is True
    assert eval_bool("0", {}) is False
    assert eval_bool("''", {}) is False
