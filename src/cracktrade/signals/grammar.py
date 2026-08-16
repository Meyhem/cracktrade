"""The signal expression grammar.

Normative reference: ``docs/ENGINE_SPEC.md`` section 2.1. This is the first and strongest of
the four layers that make look-ahead bias impossible.

The legacy engine evaluated signals with ``pd.eval``, which permits attribute access and method
calls. ``close.shift(-1) > close`` was therefore a *valid strategy* -- forbidden by a sentence
in a prompt, and by nothing else.

Here an expression is parsed into a Python AST and every node is checked against a whitelist.
``Call``, ``Attribute``, ``Subscript`` and slicing are not on it. There is consequently no way
to write ``.shift(-1)``, ``.iloc[t + 1]``, ``.rolling(center=True)``, or any other escape
hatch: a look-ahead strategy is not one that fails validation, it is a string that is not a
strategy. Rejecting ``Call`` also removes the arbitrary-code-execution surface ``pd.eval``
carries.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Final

from cracktrade.errors import SignalSyntaxError

#: Comparison operators. Chained comparisons (``a < b < c``) are permitted by Python but do not
#: vectorise over Series, so they are rejected separately below.
_COMPARISONS: Final = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)

#: Element-wise boolean operators.
_BITWISE: Final = (ast.BitAnd, ast.BitOr, ast.BitXor)

#: Arithmetic on price and indicator series.
_ARITHMETIC: Final = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)

_UNARY: Final = (ast.USub, ast.UAdd, ast.Invert)

#: Human-readable explanations for the constructs users most often reach for.
_EXPLANATIONS: Final[dict[type[ast.AST], str]] = {
    ast.Call: (
        "function and method calls are not allowed. This is what makes look-ahead bias "
        "impossible to express: without calls there is no .shift(), .rolling() or abs(). "
        "Define what you need as an indicator instead"
    ),
    ast.Attribute: (
        "attribute access is not allowed, so .shift, .values and .iloc cannot be reached. "
        "Define what you need as an indicator instead"
    ),
    ast.Subscript: (
        "indexing is not allowed: it is the other way to read a different bar than the one "
        "being decided"
    ),
    ast.BoolOp: (
        "'and' / 'or' do not work element-wise on price series. Use '&' and '|', with each "
        "comparison in parentheses"
    ),
    ast.IfExp: "conditional expressions are not allowed; combine conditions with & and |",
    ast.Lambda: "lambdas are not allowed",
    ast.NamedExpr: "assignment expressions are not allowed",
    ast.ListComp: "comprehensions are not allowed",
    ast.Starred: "unpacking is not allowed",
}


@dataclass(frozen=True, slots=True)
class SignalExpression:
    """A parsed, whitelisted signal expression.

    Attributes:
        source: the expression as the user wrote it, for error messages.
        tree: the validated AST, ready to evaluate.
        names: every name the expression references.
    """

    source: str
    tree: ast.Expression
    names: frozenset[str]

    def __str__(self) -> str:
        return self.source


def parse_expression(source: str) -> SignalExpression:
    """Parse and validate a signal expression.

    Raises:
        SignalSyntaxError: the expression does not parse, or uses a construct outside the
            grammar. The message names the construct and says what to write instead.
    """
    text = source.strip()
    if not text:
        msg = "signal expression is empty"
        raise SignalSyntaxError(msg)

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as error:
        msg = f"could not parse signal {source!r}: {error.msg}"
        raise SignalSyntaxError(msg) from error

    _reject_precedence_trap(tree, source)
    names = _validate(tree, source)
    return SignalExpression(source=text, tree=tree, names=frozenset(names))


def _validate(tree: ast.Expression, source: str) -> set[str]:
    """Walk every node against the whitelist, collecting referenced names."""
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Expression):
            continue

        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                _reject(source, "names may only be read, not assigned")
            names.add(node.id)
            continue

        if isinstance(node, ast.Constant):
            if not isinstance(node.value, int | float) or isinstance(node.value, complex):
                _reject(source, f"only numbers and booleans are allowed, found {node.value!r}")
            continue

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1:
                _reject(
                    source,
                    "chained comparisons like 'a < b < c' do not work on price series; "
                    "write '(a < b) & (b < c)'",
                )
            if not isinstance(node.ops[0], _COMPARISONS):
                _reject(source, f"comparison operator {type(node.ops[0]).__name__} is not allowed")
            _reject_float_equality(node, source)
            continue

        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, (*_BITWISE, *_ARITHMETIC)):
                _reject(source, f"operator {type(node.op).__name__} is not allowed")
            continue

        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY):
                _reject(source, f"unary operator {type(node.op).__name__} is not allowed")
            continue

        if isinstance(node, ast.expr_context | ast.operator | ast.unaryop | ast.cmpop):
            # Operator marker nodes, already checked via their parent.
            continue

        _reject(source, _explain(node))

    if not names:
        _reject(source, "references no price series or indicator, so it is constant")
    return names


def _reject_float_equality(node: ast.Compare, source: str) -> None:
    """Reject ``==`` / ``!=`` between two series, which is never true in floating point.

    ``close == sma_20`` reads as "price touches the average" and evaluates to False on every
    bar of every history, because two independently computed float64 values are equal only by
    accident. It fails silently -- no error, no trades, no explanation.

    Comparing a series to a *literal* is left alone: several indicators emit integer-valued
    flags, and ``supertrend_supertd == 1`` is both legitimate and exact.
    """
    if not isinstance(node.ops[0], ast.Eq | ast.NotEq):
        return
    left, right = node.left, node.comparators[0]
    if not (_references_a_series(left) and _references_a_series(right)):
        return
    operator = "==" if isinstance(node.ops[0], ast.Eq) else "!="
    _reject(
        source,
        f"'{operator}' between two series is never true in floating point, so this signal "
        f"would silently never fire. Compare with a tolerance instead, e.g. "
        f"'(a - b < 0.01) & (b - a < 0.01)', or use '>' / '<'",
    )


def _references_a_series(node: ast.expr) -> bool:
    """Whether ``node`` reads at least one price series or indicator."""
    return any(isinstance(child, ast.Name) for child in ast.walk(node))


def _reject_precedence_trap(tree: ast.Expression, source: str) -> None:
    """Catch ``a < 30 & b > 20``, which Python parses as ``a < (30 & b) > 20``.

    The legacy engine left this to fail at runtime inside pandas with an opaque message. It is
    the single most common mistake in hand-written signals, so it gets its own diagnostic.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for operand in operands:
            if isinstance(operand, ast.BinOp) and isinstance(operand.op, _BITWISE):
                _reject(
                    source,
                    "'&' and '|' bind more tightly than comparisons, so this reads as "
                    "'a < (b & c)'. Put each comparison in its own parentheses, e.g. "
                    "'(rsi < 30) & (close > sma)'",
                )


def _explain(node: ast.AST) -> str:
    """Explain why ``node`` is not allowed."""
    for kind, explanation in _EXPLANATIONS.items():
        if isinstance(node, kind):
            return explanation
    return f"{type(node).__name__} is not allowed in a signal expression"


def _reject(source: str, reason: str) -> None:
    msg = f"invalid signal {source!r}: {reason}"
    raise SignalSyntaxError(msg)
