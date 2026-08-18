"""The registered indicator catalogue.

Two engine builtins plus a pandas_ta catalogue. Output suffixes are *declared* here and
asserted against what the library actually returns (spec section 5.4): a pandas_ta upgrade that
renames a column fails loudly at startup instead of silently renaming a name that user signal
expressions depend on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd
from pydantic import BaseModel

from cracktrade.errors import IndicatorError, IndicatorParameterMismatchError
from cracktrade.indicators import registry as registry_module
from cracktrade.indicators.params import (
    BBandsParams,
    DonchianParams,
    FastSlowParams,
    IchimokuParams,
    MacdParams,
    NoParams,
    ParamsBase,
    PsarParams,
    StochParams,
    StochRsiParams,
    SupertrendParams,
    param,
    window_model,
)
from cracktrade.indicators.registry import register
from cracktrade.indicators.spec import IndicatorSpec, SeriesMap

#: User-facing parameter names that pandas_ta spells differently, for every indicator.
_PARAM_TO_TA: Mapping[str, str] = {"window": "length"}

#: Per-indicator overrides of :data:`_PARAM_TO_TA`, where one user-facing parameter maps to a
#: *set* of library keywords. ``bbands`` is the reason this exists: pandas_ta 0.4.71b0 splits the
#: envelope width into ``lower_std``/``upper_std`` and has no ``std`` argument at all, so the
#: obvious spelling was accepted by ``**kwargs`` and silently ignored -- every Bollinger strategy
#: computed 2.0-sigma bands whatever its YAML said. See spec section 5.5 and defect D17.
_PARAM_TO_TA_BY_TYPE: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "bbands": {"std": ("lower_std", "upper_std")},
}


def _library_keywords(func_name: str, name: str) -> tuple[str, ...]:
    """The pandas_ta keyword(s) one declared parameter is passed as."""
    override = _PARAM_TO_TA_BY_TYPE.get(func_name, {}).get(name)
    if override is not None:
        return override
    return (_PARAM_TO_TA.get(name, name),)


#: Price-series arguments pandas_ta spells differently -- ``open`` is a Python builtin, so the
#: library takes ``open_``.
_INPUT_TO_TA: Mapping[str, str] = {"open": "open_"}

_SOURCE = frozenset({"source"})
_HLC = frozenset({"high", "low", "close"})
_HL = frozenset({"high", "low"})
_HLCV = frozenset({"high", "low", "close", "volume"})
_CV = frozenset({"close", "volume"})
_OHLC = frozenset({"open", "high", "low", "close"})


# --------------------------------------------------------------------- engine builtins


def _rolling_max(series: SeriesMap, params: BaseModel) -> pd.Series:
    window = int(param(params, "window"))
    return series["source"].rolling(window=window).max()


def _rolling_min(series: SeriesMap, params: BaseModel) -> pd.Series:
    window = int(param(params, "window"))
    return series["source"].rolling(window=window).min()


# --------------------------------------------------------------------- pandas_ta adapter


def _ta_compute(
    func_name: str,
    inputs: frozenset[str],
) -> Callable[[SeriesMap, BaseModel], pd.Series | Mapping[str, pd.Series] | None]:
    """Build a compute function that calls ``pandas_ta.<func_name>``.

    Inputs are passed because the spec declares them, not because a signature was inspected.
    ``center=True`` is never among the arguments: a centred window reads bars ahead of the one
    it labels, which is look-ahead by construction.
    """

    def compute(series: SeriesMap, params: BaseModel) -> pd.Series | Mapping[str, pd.Series] | None:
        import pandas_ta

        function = getattr(pandas_ta, func_name, None)
        if function is None:
            msg = f"pandas_ta has no function {func_name!r}"
            raise IndicatorError(msg)

        kwargs: dict[str, Any] = {}
        if "source" in inputs:
            # Single-input pandas_ta functions take their series as `close`.
            kwargs["close"] = series["source"]
        for raw in ("high", "low", "close", "volume", "open"):
            if raw in inputs:
                kwargs[_INPUT_TO_TA.get(raw, raw)] = series[raw]
        for name, value in params.model_dump().items():
            if value is not None:
                for keyword in _library_keywords(func_name, name):
                    kwargs[keyword] = value

        result: pd.Series | pd.DataFrame | None = function(**kwargs)
        if result is None:
            # pandas_ta returns None when the history is shorter than the lookback. That is
            # not an error: the indicator is simply undefined everywhere so far, and the
            # caller turns this into an all-NaN series. Raising instead would make short
            # windows -- and therefore the truncation-equivalence harness -- impossible.
            return None
        if isinstance(result, pd.DataFrame):
            return _fan_out(result)
        return result

    return compute


def _fan_out(frame: pd.DataFrame) -> Mapping[str, pd.Series]:
    """Split a multi-column result into suffix-keyed series.

    The suffix is the column label up to the first underscore, lowercased -- the legacy
    convention, kept so existing strategy files still resolve (spec section 5.4).
    """
    outputs: dict[str, pd.Series] = {}
    for column in frame.columns:
        suffix = str(column).split("_")[0].lower()
        if suffix in outputs:
            msg = (
                f"two output columns of this indicator reduce to the same name {suffix!r}; "
                f"it cannot be exposed unambiguously"
            )
            raise IndicatorError(msg)
        outputs[suffix] = frame[column]
    return outputs


def _ichimoku(series: SeriesMap, params: BaseModel) -> Mapping[str, pd.Series] | None:
    """Ichimoku, restricted to its causal lines.

    pandas_ta returns five columns, three of which the engine must not expose:

    * ``ISA``/``ISB`` (senkou spans) are plotted ``kijun`` bars into the future, so the value
      labelled *t* describes a bar that has not happened.
    * ``ICS`` (chikou span) is the close shifted *backwards*, so the value labelled *t* is a
      price from ``kijun`` bars later -- direct look-ahead.

    Only tenkan (``ITS``) and kijun (``IKS``) survive. See spec section 2.2.
    """
    import pandas_ta

    computed: Any = pandas_ta.ichimoku(
        high=series["high"],
        low=series["low"],
        close=series["close"],
        tenkan=param(params, "tenkan"),
        kijun=param(params, "kijun"),
        senkou=param(params, "senkou"),
    )
    if computed is None:
        return None
    visible: Any = computed[0]
    if visible is None:
        # Not enough history yet; the caller turns this into all-NaN outputs.
        return None
    causal: dict[str, pd.Series] = {}
    for column in visible.columns:
        prefix = str(column).split("_")[0].lower()
        if prefix in {"its", "iks"}:
            causal[prefix] = visible[column]
    if set(causal) != {"its", "iks"}:
        msg = f"ichimoku did not return the expected tenkan/kijun columns: {list(visible.columns)}"
        raise IndicatorError(msg)
    return causal


# --------------------------------------------------------------------- registration


def _register_builtins() -> None:
    for name, function, verb in (
        ("rolling_max", _rolling_max, "maximum"),
        ("rolling_min", _rolling_min, "minimum"),
    ):
        register(
            IndicatorSpec(
                type=name,
                params=window_model(name, 20),
                compute=function,
                warmup=lambda params: int(param(params, "window")) - 1,
                description=f"Rolling {verb} of the source series (engine builtin)",
                inputs=_SOURCE,
            )
        )


#: ``(type, default window, inputs, description)`` for indicators whose only parameter is a
#: lookback. Warm-up is the window: conservative, and never an under-estimate.
_WINDOW_ONLY: tuple[tuple[str, int, frozenset[str], str], ...] = (
    ("sma", 20, _SOURCE, "Simple moving average"),
    ("ema", 20, _SOURCE, "Exponential moving average"),
    ("wma", 20, _SOURCE, "Weighted moving average"),
    ("hma", 20, _SOURCE, "Hull moving average"),
    ("dema", 20, _SOURCE, "Double exponential moving average"),
    ("tema", 20, _SOURCE, "Triple exponential moving average"),
    ("alma", 20, _SOURCE, "Arnaud Legoux moving average"),
    ("fwma", 20, _SOURCE, "Fibonacci weighted moving average"),
    ("kama", 20, _SOURCE, "Kaufman adaptive moving average"),
    ("linreg", 20, _SOURCE, "Rolling linear regression value"),
    ("midpoint", 20, _SOURCE, "Midpoint of the rolling high/low of the source"),
    ("sinwma", 20, _SOURCE, "Sine weighted moving average"),
    ("vidya", 20, _SOURCE, "Variable index dynamic average"),
    ("zlma", 20, _SOURCE, "Zero-lag moving average"),
    ("rsi", 14, _SOURCE, "Relative strength index (0-100)"),
    ("roc", 10, _SOURCE, "Rate of change, percent"),
    ("mom", 10, _SOURCE, "Momentum: change over the window"),
    ("cmo", 14, _SOURCE, "Chande momentum oscillator"),
    ("zscore", 30, _SOURCE, "Rolling z-score"),
    ("stdev", 30, _SOURCE, "Rolling standard deviation"),
    ("variance", 30, _SOURCE, "Rolling variance"),
    ("skew", 30, _SOURCE, "Rolling skew"),
    ("kurtosis", 30, _SOURCE, "Rolling kurtosis"),
    ("mad", 30, _SOURCE, "Rolling mean absolute deviation"),
    ("entropy", 10, _SOURCE, "Rolling entropy"),
    ("ui", 14, _SOURCE, "Ulcer index"),
    ("midprice", 20, _HL, "Midpoint of the rolling high and low"),
    ("cci", 20, _HLC, "Commodity channel index"),
    ("willr", 14, _HLC, "Williams %R (-100 to 0)"),
    ("atr", 14, _HLC, "Average true range"),
    ("natr", 14, _HLC, "Normalised average true range, percent"),
    ("vwma", 20, _CV, "Volume weighted moving average"),
    ("cmf", 20, _HLCV, "Chaikin money flow"),
    ("mfi", 14, _HLCV, "Money flow index (0-100)"),
    ("efi", 13, _CV, "Elder's force index"),
)

#: Indicators with no parameters. ``(type, inputs, warm-up, description)``.
_NO_PARAMS: tuple[tuple[str, frozenset[str], int, str], ...] = (
    ("obv", _CV, 1, "On-balance volume"),
    ("pvt", _CV, 1, "Price-volume trend"),
    ("ao", _HL, 34, "Awesome oscillator"),
    ("bop", _OHLC, 1, "Balance of power"),
    ("swma", _SOURCE, 10, "Symmetric weighted moving average"),
    ("vwap", _HLCV, 1, "Volume weighted average price"),
)


def _register_window_only() -> None:
    for type_name, default, inputs, description in _WINDOW_ONLY:
        register(
            IndicatorSpec(
                type=type_name,
                params=window_model(type_name, default),
                compute=_ta_compute(type_name, inputs),
                warmup=lambda params: int(param(params, "window")),
                description=description,
                inputs=inputs,
            )
        )


def _fixed_warmup(bars: int) -> Callable[[BaseModel], int]:
    """A warm-up that does not depend on any parameter."""

    def warmup(_params: BaseModel) -> int:
        return bars

    return warmup


def _register_no_params() -> None:
    for type_name, inputs, warmup, description in _NO_PARAMS:
        register(
            IndicatorSpec(
                type=type_name,
                params=NoParams,
                compute=_ta_compute(type_name, inputs),
                warmup=_fixed_warmup(warmup),
                description=description,
                inputs=inputs,
            )
        )


def _stoch_warmup(params: BaseModel) -> int:
    """%K needs its own window, then two smoothing passes on top of it."""
    return int(param(params, "k")) + int(param(params, "smooth_k")) + int(param(params, "d"))


def _register_multi_output() -> None:
    register(
        IndicatorSpec(
            type="macd",
            params=MacdParams,
            compute=_ta_compute("macd", _SOURCE),
            outputs=("macd", "macdh", "macds"),
            warmup=lambda p: int(param(p, "slow")) + int(param(p, "signal")),
            description="MACD line, histogram and signal line",
            inputs=_SOURCE,
        )
    )
    register(
        IndicatorSpec(
            type="bbands",
            params=BBandsParams,
            compute=_ta_compute("bbands", _SOURCE),
            outputs=("bbl", "bbm", "bbu", "bbb", "bbp"),
            warmup=lambda p: int(param(p, "window")),
            description="Bollinger bands: lower, mid, upper, bandwidth, percent-B",
            inputs=_SOURCE,
        )
    )
    register(
        IndicatorSpec(
            type="stoch",
            params=StochParams,
            compute=_ta_compute("stoch", _HLC),
            outputs=("stochk", "stochd", "stochh"),
            warmup=_stoch_warmup,
            description="Stochastic oscillator: %K, %D and their difference",
            inputs=_HLC,
        )
    )
    register(
        IndicatorSpec(
            type="stochrsi",
            params=StochRsiParams,
            compute=_ta_compute("stochrsi", _SOURCE),
            outputs=("stochrsik", "stochrsid"),
            warmup=lambda p: 2 * int(param(p, "window")) + int(param(p, "k")),
            description="Stochastic RSI: %K and %D",
            inputs=_SOURCE,
        )
    )
    register(
        IndicatorSpec(
            type="adx",
            params=window_model("adx", 14),
            compute=_ta_compute("adx", _HLC),
            outputs=("adx", "adxr", "dmp", "dmn"),
            warmup=lambda p: 2 * int(param(p, "window")),
            description="Average directional index with +DI/-DI",
            inputs=_HLC,
        )
    )
    register(
        IndicatorSpec(
            type="aroon",
            params=window_model("aroon", 14),
            compute=_ta_compute("aroon", _HL),
            outputs=("aroond", "aroonu", "aroonosc"),
            warmup=lambda p: int(param(p, "window")),
            description="Aroon up, down and oscillator",
            inputs=_HL,
        )
    )
    register(
        IndicatorSpec(
            type="donchian",
            params=DonchianParams,
            compute=_ta_compute("donchian", _HL),
            outputs=("dcl", "dcm", "dcu"),
            warmup=lambda p: max(int(param(p, "lower_length")), int(param(p, "upper_length"))),
            description="Donchian channel: lower, mid, upper",
            inputs=_HL,
        )
    )
    register(
        IndicatorSpec(
            type="kc",
            params=window_model("kc", 20),
            compute=_ta_compute("kc", _HLC),
            outputs=("kcle", "kcbe", "kcue"),
            warmup=lambda p: int(param(p, "window")),
            description="Keltner channel: lower, basis, upper",
            inputs=_HLC,
        )
    )
    register(
        IndicatorSpec(
            type="accbands",
            params=window_model("accbands", 20),
            compute=_ta_compute("accbands", _HLC),
            outputs=("accbl", "accbm", "accbu"),
            warmup=lambda p: int(param(p, "window")),
            description="Acceleration bands: lower, mid, upper",
            inputs=_HLC,
        )
    )
    register(
        IndicatorSpec(
            type="supertrend",
            params=SupertrendParams,
            compute=_ta_compute("supertrend", _HLC),
            outputs=("supert", "supertd", "supertl", "superts"),
            warmup=lambda p: int(param(p, "window")),
            description="Supertrend value, direction, and long/short bands",
            inputs=_HLC,
        )
    )
    register(
        IndicatorSpec(
            type="psar",
            params=PsarParams,
            compute=_ta_compute("psar", _HLC),
            outputs=("psarl", "psars", "psaraf", "psarr"),
            warmup=lambda _p: 2,
            description="Parabolic SAR: long stop, short stop, acceleration, reversal flag",
            inputs=_HLC,
        )
    )
    register(
        IndicatorSpec(
            type="fisher",
            params=window_model("fisher", 9),
            compute=_ta_compute("fisher", _HL),
            outputs=("fishert", "fisherts"),
            warmup=lambda p: int(param(p, "window")),
            description="Fisher transform and its signal line",
            inputs=_HL,
        )
    )
    register(
        IndicatorSpec(
            type="tsi",
            params=FastSlowParams,
            compute=_ta_compute("tsi", _SOURCE),
            outputs=("tsi", "tsis"),
            warmup=lambda p: int(param(p, "fast")) + int(param(p, "slow")),
            description="True strength index and its signal line",
            inputs=_SOURCE,
        )
    )
    register(
        IndicatorSpec(
            type="trix",
            params=window_model("trix", 30),
            compute=_ta_compute("trix", _SOURCE),
            outputs=("trix", "trixs"),
            warmup=lambda p: 3 * int(param(p, "window")),
            description="Triple-EMA oscillator and its signal line",
            inputs=_SOURCE,
        )
    )
    register(
        IndicatorSpec(
            type="kst",
            params=NoParams,
            compute=_ta_compute("kst", _SOURCE),
            outputs=("kst", "ksts"),
            warmup=lambda _p: 60,
            description="Know sure thing and its signal line",
            inputs=_SOURCE,
        )
    )
    register(
        IndicatorSpec(
            type="massi",
            params=FastSlowParams,
            compute=_ta_compute("massi", _HL),
            warmup=lambda p: int(param(p, "fast")) + int(param(p, "slow")),
            description="Mass index",
            inputs=_HL,
        )
    )
    register(
        IndicatorSpec(
            type="adosc",
            params=FastSlowParams,
            compute=_ta_compute("adosc", _HLCV),
            warmup=lambda p: int(param(p, "slow")),
            description="Accumulation/distribution oscillator",
            inputs=_HLCV,
        )
    )
    register(
        IndicatorSpec(
            type="ichimoku",
            params=IchimokuParams,
            compute=_ichimoku,
            outputs=("its", "iks"),
            warmup=lambda p: max(int(param(p, "tenkan")), int(param(p, "kijun"))),
            description="Ichimoku tenkan and kijun lines (the projected spans are not exposed)",
            inputs=_HLC,
        )
    )


def _assert_params_reach_the_library() -> None:
    """Check every declared parameter is a keyword its pandas_ta function actually accepts.

    The mirror of the output assertion in :mod:`cracktrade.indicators.compute` (spec section
    5.4), and it exists for the same reason: what the registry declares must be checked against
    what the library does, not assumed to match.

    Outputs fail loudly on their own -- a renamed column breaks the namespace key a signal
    references. Parameters do not. Every pandas_ta function ends in ``**kwargs``, so a keyword
    it does not know is accepted and dropped, and the indicator is computed at the library's
    default with no error anywhere. The result looks exactly like a correct one. That is the
    failure mode this project exists to not have, so it is checked at registration.
    """
    import inspect

    import pandas_ta

    for spec in registry_module.all_specs():
        function = getattr(pandas_ta, spec.type, None)
        if function is None:
            continue  # an engine builtin; it has no library signature to check against.
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError):  # pragma: no cover - a C function without a signature
            continue
        accepted = frozenset(signature.parameters)
        for name in spec.params.model_fields:
            unreachable = [
                keyword for keyword in _library_keywords(spec.type, name) if keyword not in accepted
            ]
            if unreachable:
                offered = ", ".join(sorted(accepted - {"kwargs"}))
                msg = (
                    f"indicator {spec.type!r} declares parameter {name!r}, which would be "
                    f"passed to pandas_ta.{spec.type} as {', '.join(unreachable)} -- "
                    f"argument(s) it does not accept, so the value would be silently ignored "
                    f"and the indicator computed at the library default. "
                    f"pandas_ta.{spec.type} accepts: {offered}"
                )
                raise IndicatorParameterMismatchError(msg)


def install() -> None:
    """Register the whole catalogue. Idempotent."""
    if registry_module.count():
        return
    _register_builtins()
    _register_window_only()
    _register_no_params()
    _register_multi_output()
    _assert_params_reach_the_library()


__all__ = ["ParamsBase", "install"]
