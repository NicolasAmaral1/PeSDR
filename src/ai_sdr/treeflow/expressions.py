"""Safe expression evaluator for TreeFlow transitions and rule_expression exits.

Backed by `simpleeval`, restricted to a small whitelist of AST nodes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from simpleeval import (
    AttributeDoesNotExist,
    FeatureNotAvailable,
    InvalidExpression,
    NameNotDefined,
    SimpleEval,
)


class ExpressionError(Exception):
    """Raised when an expression is malformed or uses forbidden features."""


def _is_set_factory(ctx: dict[str, Any]) -> Callable[[str], bool]:
    def is_set(name: str) -> bool:
        return name in ctx and ctx[name] is not None

    return is_set


def eval_bool(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate `expression` against `context` and coerce to bool.

    Missing names resolve to `None` (so transitions on not-yet-collected fields
    evaluate to False instead of raising). Forbidden operations raise
    `ExpressionError`. Built-in helpers: `true`, `false`, `is_set(name)`.
    """
    # Belt-and-suspenders: simpleeval blocks dunder access via AttributeDoesNotExist,
    # but bare dunder names should also be forbidden up front.
    if "__" in expression:
        raise ExpressionError(f"dunder names forbidden in expression {expression!r}")

    names: dict[str, Any] = {"true": True, "false": False}
    names.update(context)
    evaluator = SimpleEval(
        names=names,
        functions={"is_set": _is_set_factory(context)},
    )
    try:
        result = evaluator.eval(expression)
    except NameNotDefined:
        return False
    except AttributeDoesNotExist as e:
        raise ExpressionError(f"attribute access not allowed: {e}") from e
    except FeatureNotAvailable as e:
        raise ExpressionError(f"forbidden feature in expression {expression!r}: {e}") from e
    except InvalidExpression as e:
        raise ExpressionError(f"invalid expression {expression!r}: {e}") from e
    except (SyntaxError, ValueError) as e:
        raise ExpressionError(f"could not parse expression {expression!r}: {e}") from e

    return bool(result)
