"""The look-ahead conformance suite (spec section 2).

This module is the reason the user can trust a result. Everything else in the engine is a
design intended to prevent look-ahead; these tests are the evidence that the design holds.

The central idea is **truncation equivalence**: if a value at bar *t* changes when bars after
*t* are appended, that value was computed from those later bars. Equality is exact, not
approximate -- a causal computation on a prefix produces bitwise identical results, and
``isclose`` would hide genuine leakage in the low-order bits.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cracktrade.config import Strategy, parse_strategy
from cracktrade.data import MarketData, prepare_frame
from cracktrade.errors import CausalityViolationError, SignalSyntaxError
from cracktrade.indicators import compute_indicators, registry
from cracktrade.signals import causal_shift, parse_expression, prepare_signal
from tests.factories import make_ohlcv

pytestmark = pytest.mark.causality

BARS = 320

#: Bars to probe. Includes indices inside the warm-up region, where the legacy engine's
#: zero-filling did its damage.
PROBE_INDICES = (5, 30, 61, 120, 199, 260, 319)

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "cracktrade"


def market_data(bars: int = BARS) -> MarketData:
    frame = prepare_frame(make_ohlcv(bars), ticker="TEST", today=date(2100, 1, 1))
    return MarketData(
        ticker="TEST",
        frame=frame,
        requested_start=date(2020, 1, 1),
        requested_end=date(2030, 1, 1),
    )


def strategy_with(indicators: list[dict[str, Any]], signal: str = "close > open") -> Strategy:
    return parse_strategy(
        {
            "strategy": {"name": "s"},
            "universe": {
                "ticker": "TEST",
                "start_date": "2020-01-01",
                "end_date": "2030-01-01",
            },
            "execution": {
                "initial_capital": 10000.0,
                "slippage_pct": 0.0,
                "commission_pct": 0.0,
            },
            "indicators": indicators,
            "entry": {"signal": signal},
            "exit": {"max_holding_days": 5},
        }
    )


#: Indicators whose value at bar *t* is reproducible only up to floating-point rounding.
#:
#: These accumulate over the window with an online algorithm whose rounding error depends on how
#: many elements have been folded in, so a prefix run and a full run can differ in the last bits.
#: That is associativity, not information: a difference of a few parts in 10^12 cannot encode a
#: future price. Every other indicator must match bit for bit, and
#: :func:`test_the_rounding_allowlist_is_minimal` fails if this list grows silently.
ULP_STABLE: dict[str, float] = {
    "alma": 1e-12,
    "skew": 1e-9,
    "kurtosis": 1e-9,
}


def compare_at(
    full: pd.Series,
    truncated: pd.Series,
    at: int,
    label: str,
    *,
    tolerance: float = 0.0,
) -> bool:
    """Assert the value at ``at`` does not depend on bars after ``at``.

    Returns whether the two values were actually comparable, so callers can insist that some
    comparison happened rather than silently verifying nothing.

    One-sided NaN is allowed in exactly one direction: the truncated run may decline to produce
    a value that the full run produces, because some library implementations need a longer array
    before they emit anything. The reverse -- a value on the short history that vanishes once
    more bars exist -- would mean the full-history value was derived differently, and fails.
    """
    on_full = full.iloc[at]
    on_prefix = truncated.iloc[at]
    full_undefined = bool(pd.isna(on_full))
    prefix_undefined = bool(pd.isna(on_prefix))

    if full_undefined and prefix_undefined:
        return False
    if prefix_undefined:
        # The short history was more conservative. Safe: no value was invented.
        return False
    assert not full_undefined, (
        f"{label} at bar {at} is defined on history truncated at {at} but undefined on the "
        f"full history; the two runs disagree about whether the value exists"
    )

    if on_full == on_prefix:
        return True

    deviation = abs(on_full - on_prefix) / max(abs(on_full), 1e-300)
    assert deviation <= tolerance, (
        f"{label} at bar {at} changed when future bars were appended: "
        f"{on_prefix!r} on history truncated at {at}, {on_full!r} on the full history "
        f"(relative deviation {deviation:.3e}). That value was computed from bars after {at}."
    )
    return True


# --------------------------------------------------------------------- indicators


def test_every_indicator_is_truncation_equivalent() -> None:
    """The registry-wide sweep: no indicator's value at t depends on bars after t."""
    full_data = market_data()

    for spec in registry.all_specs():
        strategy = strategy_with([{"name": "probe", "type": spec.type}])
        full = compute_indicators(strategy, full_data)
        tolerance = ULP_STABLE.get(spec.type, 0.0)
        compared = 0

        for at in PROBE_INDICES:
            truncated = compute_indicators(strategy, full_data.head(at + 1))
            for name in spec.output_names("probe"):
                compared += compare_at(
                    full[name],
                    truncated[name],
                    at,
                    f"{spec.type}/{name}",
                    tolerance=tolerance,
                )

        assert compared, f"{spec.type}: no probe bar was comparable, so nothing was verified"


def test_the_rounding_allowlist_is_minimal() -> None:
    """Every indicator not on the allowlist must reproduce bit for bit.

    Guards the allowlist against quietly absorbing a real regression: if an indicator starts
    deviating, it fails here rather than being covered by a blanket tolerance.
    """
    full_data = market_data()
    deviating: set[str] = set()

    for spec in registry.all_specs():
        strategy = strategy_with([{"name": "probe", "type": spec.type}])
        full = compute_indicators(strategy, full_data)
        for at in PROBE_INDICES:
            truncated = compute_indicators(strategy, full_data.head(at + 1))
            for name in spec.output_names("probe"):
                on_full = full[name].iloc[at]
                on_prefix = truncated[name].iloc[at]
                if pd.isna(on_full) or pd.isna(on_prefix):
                    continue
                if on_full != on_prefix:
                    deviating.add(spec.type)

    assert deviating <= set(ULP_STABLE), (
        f"these indicators are no longer bit-for-bit reproducible on a prefix: "
        f"{sorted(deviating - set(ULP_STABLE))}"
    )


@pytest.mark.parametrize(
    "config",
    [
        {"name": "probe", "type": "sma", "window": 7},
        {"name": "probe", "type": "ema", "window": 13},
        {"name": "probe", "type": "rsi", "window": 9},
        {"name": "probe", "type": "atr", "window": 21},
        {"name": "probe", "type": "rolling_max", "window": 55},
        {"name": "probe", "type": "bbands", "window": 34, "std": 1.5},
        {"name": "probe", "type": "macd", "fast": 5, "slow": 17, "signal": 4},
    ],
)
def test_indicators_are_truncation_equivalent_at_other_parameters(config: dict[str, Any]) -> None:
    """Randomised parameterisations, in case a default happens to be benign."""
    full_data = market_data()
    strategy = strategy_with([config])
    spec = registry.get(str(config["type"]))
    full = compute_indicators(strategy, full_data)

    tolerance = ULP_STABLE.get(spec.type, 0.0)
    for at in PROBE_INDICES:
        truncated = compute_indicators(strategy, full_data.head(at + 1))
        for name in spec.output_names("probe"):
            compare_at(
                full[name],
                truncated[name],
                at,
                f"{config['type']}/{name}",
                tolerance=tolerance,
            )


# --------------------------------------------------------------------- signals


INDICATOR_POOL: list[dict[str, Any]] = [
    {"name": "sma_fast", "type": "sma", "window": 10},
    {"name": "sma_slow", "type": "sma", "window": 50},
    {"name": "rsi_14", "type": "rsi", "window": 14},
    {"name": "hi", "type": "rolling_max", "window": 20},
    {"name": "lo", "type": "rolling_min", "window": 20},
    {"name": "bb", "type": "bbands", "window": 20, "std": 2.0},
]

NAME_POOL = (
    "close",
    "open",
    "high",
    "low",
    "volume",
    "sma_fast",
    "sma_slow",
    "rsi_14",
    "hi",
    "lo",
    "bb_bbl",
    "bb_bbu",
)


@st.composite
def signal_expressions(draw: st.DrawFn) -> str:
    """Generate arbitrary valid signal expressions.

    Property-based rather than hand-written, so coverage is not limited to the cases the author
    thought of.
    """
    numbers = st.floats(min_value=0.1, max_value=200.0, allow_nan=False, allow_infinity=False)
    operand = st.one_of(st.sampled_from(NAME_POOL), numbers.map(lambda value: f"{value:.2f}"))
    # The left side is always a name: an expression referencing nothing is constant, and the
    # grammar rejects it, so generating one would only test the rejection path.
    comparison = st.builds(
        lambda left, op, right: f"({left} {op} {right})",
        st.sampled_from(NAME_POOL),
        st.sampled_from(["<", ">", "<=", ">="]),
        operand,
    )
    joined = st.builds(
        lambda left, op, right: f"({left} {op} {right})",
        comparison,
        st.sampled_from(["&", "|"]),
        comparison,
    )
    return draw(st.one_of(comparison, joined))


@given(expression=signal_expressions())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_generated_signals_are_truncation_equivalent(expression: str) -> None:
    full_data = market_data()
    strategy = strategy_with(INDICATOR_POOL, signal=expression)
    full_namespace = compute_indicators(strategy, full_data)
    full_signal = prepare_signal(expression, full_namespace).series

    for at in (80, 160, 240):
        truncated_namespace = compute_indicators(strategy, full_data.head(at + 1))
        truncated_signal = prepare_signal(expression, truncated_namespace).series
        compare_at(full_signal, truncated_signal, at, f"signal {expression!r}")


def test_signal_never_fires_during_warmup() -> None:
    """Defect D1: with a zero-filled warm-up this fired on bar one of every backtest."""
    data = market_data()
    strategy = strategy_with([{"name": "hi", "type": "rolling_max", "window": 100}])
    namespace = compute_indicators(strategy, data)

    signal = prepare_signal("close >= hi", namespace).series

    assert not signal.iloc[: namespace.warmup].any()
    assert namespace.warmup >= 99


def test_signal_lands_on_the_bar_after_the_condition() -> None:
    """The next-open chain: condition true at the close of D, tradeable at the open of D+1."""
    data = market_data(60)
    strategy = strategy_with([])
    namespace = compute_indicators(strategy, data)

    close = data.frame["Close"]
    threshold = float(close.iloc[30])
    raw = close > threshold
    aligned = prepare_signal(f"close > {threshold}", namespace).series

    first_raw = int(np.argmax(raw.to_numpy()))
    first_aligned = int(np.argmax(aligned.to_numpy()))
    assert first_aligned == first_raw + 1


# --------------------------------------------------------------------- the grammar


@pytest.mark.parametrize(
    "expression",
    [
        "close.shift(-1) > close",
        "close.shift(1) > close",
        "close > close.rolling(5).mean()",
        "abs(close) > 1",
        "max(close, open) > 1",
        "close[1] > close",
        "close.iloc[1] > close",
        "close.values > 1",
        "(close > open) and (high > low)",
        "(close > open) or (high > low)",
        "close if high > low else open",
        "(lambda x: x)(close) > 1",
        "close < high < open",
        "[c for c in close]",
        "close.__class__",
    ],
)
def test_look_ahead_and_escape_hatches_do_not_parse(expression: str) -> None:
    """Not "rejected by validation" -- these are simply not expressions in this language."""
    with pytest.raises(SignalSyntaxError):
        parse_expression(expression)


def test_precedence_trap_is_explained() -> None:
    with pytest.raises(SignalSyntaxError, match="bind more tightly"):
        parse_expression("rsi_14 < 30 & close > sma_200")


@pytest.mark.parametrize(
    "expression",
    [
        "close > open",
        "(rsi_14 < 30) & (close > sma_slow)",
        "(close >= hi) | (close <= lo)",
        "close > sma_slow * 1.5",
        "(volume > 1000) & (close - open > 0.5)",
        "~(close > open)",
        "close ** 2 > 10",
    ],
)
def test_legitimate_expressions_parse(expression: str) -> None:
    assert parse_expression(expression).names


# --------------------------------------------------------------------- the shift helper


def test_causal_shift_refuses_to_look_ahead() -> None:
    series = pd.Series([1.0, 2.0, 3.0])
    with pytest.raises(CausalityViolationError, match="look-ahead bias"):
        causal_shift(series, -1)


def test_causal_shift_moves_values_forward() -> None:
    series = pd.Series([1.0, 2.0, 3.0])
    shifted = causal_shift(series, 1)
    assert pd.isna(shifted.iloc[0])
    assert shifted.iloc[1] == 1.0


# --------------------------------------------------------------------- source scanning


def source_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def parsed_sources() -> list[tuple[Path, ast.Module]]:
    """Every engine module, parsed.

    These checks read the *code*, not the file text: this project's own documentation
    discusses ``.shift(-1)`` and ``center=True`` at length, and a substring scan would flag the
    warnings against them as violations.
    """
    return [(path, ast.parse(path.read_text(encoding="utf-8"))) for path in source_files()]


def test_only_the_alignment_helper_shifts_series() -> None:
    """One shift, in one place, that refuses to go backwards (spec section 2.3)."""
    offenders: list[str] = []
    for path, tree in parsed_sources():
        if path.name == "alignment.py":
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shift"
            ):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
    assert not offenders, (
        f"these call .shift() directly instead of going through causal_shift: {offenders}"
    )


def test_no_negative_shift_anywhere() -> None:
    """Even inside the helper, no literal negative shift may be written."""
    for path, tree in parsed_sources():
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shift"
            ):
                continue
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                is_negative_literal = isinstance(argument, ast.UnaryOp) and isinstance(
                    argument.op, ast.USub
                )
                assert not is_negative_literal, (
                    f"{path.relative_to(SRC_ROOT)}:{node.lineno} shifts by a negative amount"
                )


def test_no_centred_windows() -> None:
    """A centred rolling window reads bars ahead of the one it labels."""
    for path, tree in parsed_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "center":
                    continue
                centred = isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                assert not centred, (
                    f"{path.relative_to(SRC_ROOT)}:{node.lineno} uses a centred window"
                )


def test_no_blind_excepts_in_the_engine() -> None:
    """Defect D10: a bare except turned a broken run into a plausible-looking zero."""
    offenders: list[str] = []
    for path, tree in parsed_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno}")
    assert not offenders, f"bare 'except:' found at {offenders}"


#: Calls named like dynamic evaluation but categorically unrelated to it, as
#: ``module.attribute`` pairs. Deliberately an allowlist of exact receivers rather than a
#: relaxation of the rule: ``re.compile`` builds a regular expression and cannot evaluate a
#: Python expression against a price series, while a bare ``compile(...)`` still can.
DYNAMIC_EVALUATION_ALLOWED = frozenset({("re", "compile")})


def test_no_dynamic_evaluation_in_the_engine() -> None:
    """``pd.eval`` permits attribute access, which is how the legacy engine let .shift(-1) in."""
    forbidden = {"eval", "exec", "compile"}
    offenders: list[str] = []
    for path, tree in parsed_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = (
                called.attr
                if isinstance(called, ast.Attribute)
                else called.id
                if isinstance(called, ast.Name)
                else None
            )
            if name not in forbidden:
                continue
            receiver = (
                called.value.id
                if isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name)
                else None
            )
            if (receiver, name) in DYNAMIC_EVALUATION_ALLOWED:
                continue
            offenders.append(f"{path.relative_to(SRC_ROOT)}:{node.lineno} calls {name}")
    assert not offenders, f"dynamic evaluation found: {offenders}"


def test_the_dynamic_evaluation_allowlist_is_narrow() -> None:
    """The allowlist must not quietly become an escape hatch.

    Entries are ``(receiver, name)`` pairs, so a bare ``compile(...)`` -- which has no receiver
    -- can never match one, and neither can ``compile`` on some other object. That leaves this
    assertion to defend the only thing still at risk: that the list stays one entry long.
    """
    assert set(DYNAMIC_EVALUATION_ALLOWED) == {("re", "compile")}
    assert ("pd", "eval") not in DYNAMIC_EVALUATION_ALLOWED
