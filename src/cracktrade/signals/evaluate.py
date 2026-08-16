"""Evaluating a validated signal expression against the indicator namespace.

The AST is walked directly. No ``eval``, no ``pd.eval``: the whitelist in
:mod:`cracktrade.signals.grammar` would be worth much less if the thing that ran the expression
could reach constructs the whitelist rejected.

Evaluation carries a **defined mask** alongside every value (spec section 6.2, defect D15).
Relying on "a comparison against NaN is False" is not enough to keep undefined data out of
trading decisions, because ``~`` inverts it: ``~(rsi > 30)`` is *True* on every bar where ``rsi``
is NaN. And NaN is not confined to warm-up -- ``psar_psarl`` holds a value only while the trend
is up, ``supertrend_supertl`` only while the supertrend is long, so both are undefined on the
majority of their bars by design. The mask makes "undefined" collapse to "no signal" under every
operator rather than under comparison alone.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd

from cracktrade.errors import SignalError, UnknownSymbolError
from cracktrade.signals.alignment import causal_shift
from cracktrade.signals.grammar import SignalExpression, parse_expression

if TYPE_CHECKING:
    from cracktrade.indicators import IndicatorNamespace

_BINARY: Final[Mapping[type[ast.operator], Callable[[Any, Any], Any]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}

_COMPARE: Final[Mapping[type[ast.cmpop], Callable[[Any, Any], Any]]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

_UNARY: Final[Mapping[type[ast.unaryop], Callable[[Any], Any]]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Invert: operator.invert,
}

#: An operand's definedness. ``None`` means "defined on every bar", which is what a literal
#: constant contributes; carrying it as ``None`` rather than an all-True series avoids
#: allocating one per constant in an expression.
type DefinedMask = pd.Series | None


@dataclass(frozen=True, slots=True)
class EvaluatedSignal:
    """A signal and the record of where its inputs were actually defined.

    Attributes:
        series: the boolean series the engine trades on. Already masked by ``defined`` and
            already shifted for next-open execution.
        defined: True on bars where every name the expression references held a value. A signal
            can never be True where this is False.
        expression: the source text, for reporting.
        warmup: bars suppressed at the head of the series.
    """

    series: pd.Series
    defined: pd.Series
    expression: str
    warmup: int

    @property
    def defined_pct(self) -> float:
        """Percentage of post-warm-up bars on which every referenced name held a value.

        A strategy whose entry condition sits far below 100 here is not the strategy its author
        wrote: the condition was silently unevaluable on those bars. ``psar`` and ``supertrend``
        outputs routinely produce values in the 10-60 range, which is by design and not an
        error -- but it must be visible rather than silent.

        The first bar is excluded along with the warm-up. After the next-open shift it has no
        predecessor, so no signal can be defined there for any expression; counting it would
        put a permanent floor under the statistic and make a clean strategy look imperfect.
        """
        usable = self.defined.iloc[max(self.warmup, 1) :]
        if usable.empty:
            return 0.0
        return 100.0 * float(usable.mean())

    @property
    def signal_count(self) -> int:
        """How many bars the signal is True on."""
        return int(self.series.sum())


def evaluate(
    expression: SignalExpression,
    series: Mapping[str, pd.Series],
    index: pd.Index,
) -> tuple[pd.Series, pd.Series]:
    """Evaluate ``expression``, returning ``(condition, defined)``.

    The condition is what was true at the close of each bar, already forced False wherever an
    input was undefined. Turning it into something tradeable is :func:`prepare_signal`'s job.

    Raises:
        UnknownSymbolError: the expression references an undefined name.
        SignalError: the expression does not reduce to a boolean series.
    """
    missing = sorted(name for name in expression.names if name not in series)
    if missing:
        available = ", ".join(sorted(series))
        msg = (
            f"signal {expression.source!r} references undefined name(s) "
            f"{', '.join(missing)}; available names are: {available}"
        )
        raise UnknownSymbolError(msg)

    value, mask = _eval(expression.tree.body, series)
    condition = _as_boolean(value, index, expression)
    defined = (
        pd.Series(True, index=index, dtype=bool)
        if mask is None
        else mask.reindex(index).fillna(value=False).astype(bool)
    )
    return condition & defined, defined


def prepare_signal(
    expression: str | SignalExpression,
    namespace: IndicatorNamespace,
) -> EvaluatedSignal:
    """Turn a signal expression into the boolean series the engine trades on.

    Four steps, in order:

    1. evaluate the condition on each bar's close;
    2. force False wherever any referenced name was undefined on that bar (spec section 6.2,
       defect D15);
    3. suppress the warm-up region, so no decision rests on an indicator that has not yet
       converged (spec section 6.3, defect D1);
    4. shift forward one bar, so the decision taken at the close of *D* is acted on at the open
       of *D+1* (spec section 6.4).
    """
    parsed = (
        expression if isinstance(expression, SignalExpression) else parse_expression(expression)
    )
    raw, defined = evaluate(parsed, namespace.series, namespace.index)
    return align_signal(raw, defined, warmup=namespace.warmup, expression=parsed.source)


def align_signal(
    raw: pd.Series,
    defined: pd.Series,
    *,
    warmup: int,
    expression: str = "",
) -> EvaluatedSignal:
    """Suppress the warm-up region, then shift both series for next-open execution."""
    suppressed = raw.copy()
    if warmup:
        suppressed.iloc[:warmup] = False
    return EvaluatedSignal(
        series=_shift_boolean(suppressed),
        defined=_shift_boolean(defined),
        expression=expression,
        warmup=warmup,
    )


def _shift_boolean(values: pd.Series) -> pd.Series:
    """Move a boolean series one bar forward, keeping it boolean.

    Shifting a boolean series directly introduces NaN and silently widens it to object dtype.
    Shifting as float and comparing to zero restores a clean boolean, and bars with no
    predecessor become False, which is what "nothing known yet" means.
    """
    shifted = causal_shift(values.astype(np.float64), 1)
    return (shifted > 0).astype(bool)


def _eval(node: ast.expr, series: Mapping[str, pd.Series]) -> tuple[Any, DefinedMask]:
    """Recursively evaluate a whitelisted node, tracking where its inputs were defined."""
    if isinstance(node, ast.Name):
        values = series[node.id]
        return values, values.notna()

    if isinstance(node, ast.Constant):
        return node.value, None

    if isinstance(node, ast.BinOp):
        left, left_mask = _eval(node.left, series)
        right, right_mask = _eval(node.right, series)
        return _BINARY[type(node.op)](left, right), _intersect(left_mask, right_mask)

    if isinstance(node, ast.UnaryOp):
        operand, mask = _eval(node.operand, series)
        return _UNARY[type(node.op)](operand), mask

    if isinstance(node, ast.Compare):
        left, left_mask = _eval(node.left, series)
        right, right_mask = _eval(node.comparators[0], series)
        return _COMPARE[type(node.ops[0])](left, right), _intersect(left_mask, right_mask)

    # Unreachable: the grammar rejected everything else before we got here.
    msg = f"unexpected node {type(node).__name__} survived validation"
    raise SignalError(msg)


def _intersect(left: DefinedMask, right: DefinedMask) -> DefinedMask:
    """A result is defined only where both of its operands were."""
    if left is None:
        return right
    if right is None:
        return left
    return left & right


def _as_boolean(value: Any, index: pd.Index, expression: SignalExpression) -> pd.Series:
    """Coerce an evaluation result into a boolean series, or explain why it is not one."""
    if isinstance(value, bool | np.bool_):
        return pd.Series(bool(value), index=index, dtype=bool)

    if not isinstance(value, pd.Series):
        msg = (
            f"signal {expression.source!r} produced {type(value).__name__}, not a condition; "
            f"a signal must compare series, e.g. '(rsi_14 < 30) & (close > sma_200)'"
        )
        raise SignalError(msg)

    if value.dtype != bool:
        msg = (
            f"signal {expression.source!r} produced numbers rather than true/false values; "
            f"add a comparison, e.g. '{expression.source} > 0'"
        )
        raise SignalError(msg)

    return value.reindex(index).fillna(value=False).astype(bool)
