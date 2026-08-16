"""Computing the indicator namespace a signal expression is evaluated against.

Normative reference: ``docs/ENGINE_SPEC.md`` section 5. The single most important difference
from the legacy engine is what is *not* here: there is no ``.fillna(0)``. Zero-filling the
warm-up region made ``close >= rolling_max`` true from bar 0 and produced a spurious day-one
entry in every breakout strategy (defect D1). The warm-up region stays NaN, and its length is
carried alongside the data so the signal layer can suppress it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pydantic import BaseModel, ValidationError

from cracktrade.data.contract import DTYPE
from cracktrade.errors import IndicatorError, IndicatorOutputMismatchError
from cracktrade.indicators import registry
from cracktrade.indicators.spec import RAW_SERIES, IndicatorSpec
from cracktrade.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cracktrade.config import IndicatorConfig, Strategy
    from cracktrade.data import MarketData

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IndicatorNamespace:
    """The names a signal expression may reference, and when they become meaningful.

    Attributes:
        series: every name bound to a float series on the data's index -- the five raw price
            series plus one entry per indicator output.
        warmup: bars before which no name is fully defined. Signals are suppressed over this
            region (spec section 6.3).
        index: the shared index of every series.
    """

    series: Mapping[str, pd.Series]
    warmup: int
    index: pd.DatetimeIndex

    def __contains__(self, name: str) -> bool:
        return name in self.series

    def __getitem__(self, name: str) -> pd.Series:
        return self.series[name]

    @property
    def names(self) -> tuple[str, ...]:
        """Every referenceable name, sorted."""
        return tuple(sorted(self.series))

    @property
    def indicator_names(self) -> tuple[str, ...]:
        """Names contributed by indicators, excluding the raw price series."""
        return tuple(name for name in self.names if name not in RAW_SERIES)


def compute_indicators(strategy: Strategy, data: MarketData) -> IndicatorNamespace:
    """Build the namespace for ``strategy`` over ``data``.

    Raises:
        IndicatorError: an indicator could not be computed, or two indicators produced the
            same name.
        IndicatorOutputMismatchError: an implementation returned different outputs than its
            registry entry declares.
    """
    registry_installed()

    frame = data.frame
    series: dict[str, pd.Series] = {
        "open": frame["Open"],
        "high": frame["High"],
        "low": frame["Low"],
        "close": frame["Close"],
        "volume": frame["Volume"],
    }
    warmup = 0

    for config in strategy.indicators:
        spec = registry.get(config.type)
        params = _validate_params(config, spec)
        outputs = _compute_one(config, spec, series, params)

        declared = spec.warmup(params)
        for name, values in outputs.items():
            if name in series:
                msg = (
                    f"indicator {config.name!r} produces the name {name!r}, which is already "
                    f"taken; rename one of them"
                )
                raise IndicatorError(msg)
            aligned = _align(values, data.index, config.name)
            series[name] = aligned
            warmup = max(warmup, declared, _leading_nan_count(aligned))

    logger.debug("namespace has %d names, warm-up %d bars", len(series), warmup)
    return IndicatorNamespace(series=series, warmup=warmup, index=data.index)


def registry_installed() -> None:
    """Ensure the catalogue is registered before use."""
    from cracktrade.indicators.catalogue import install

    install()


def _validate_params(config: IndicatorConfig, spec: IndicatorSpec) -> BaseModel:
    """Validate an indicator's type-specific parameters against its own schema."""
    try:
        return spec.params.model_validate(config.params)
    except ValidationError as error:
        accepted = ", ".join(sorted(spec.params.model_fields)) or "(none)"
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        msg = (
            f"indicator {config.name!r} of type {config.type!r}: {problems}. "
            f"Accepted parameters: {accepted}"
        )
        raise IndicatorError(msg) from error


def _compute_one(
    config: IndicatorConfig,
    spec: IndicatorSpec,
    series: Mapping[str, pd.Series],
    params: BaseModel,
) -> dict[str, pd.Series]:
    """Run one indicator and map its result onto namespace names."""
    inputs = dict(series)
    inputs["source"] = series[config.source.value]

    try:
        result = spec.compute(inputs, params)
    except IndicatorError:
        raise
    except Exception as error:
        msg = f"indicator {config.name!r} of type {config.type!r} failed to compute: {error}"
        raise IndicatorError(msg) from error

    if result is None:
        # Not enough history for this indicator yet: undefined on every bar so far. NaN is the
        # honest answer, and the warm-up machinery then suppresses signals over the region.
        undefined = pd.Series(np.nan, index=inputs["close"].index, dtype=DTYPE)
        return dict.fromkeys(spec.output_names(config.name), undefined)

    if isinstance(result, pd.Series):
        if spec.is_multi_output:
            msg = (
                f"{spec.type!r} declares outputs {spec.outputs} but returned a single series; "
                f"the registry entry and the implementation disagree"
            )
            raise IndicatorOutputMismatchError(msg)
        return {config.name: result}

    produced = tuple(sorted(result))
    expected = tuple(sorted(spec.outputs))
    if produced != expected:
        msg = (
            f"{spec.type!r} declares outputs {expected} but returned {produced}; "
            f"the registry entry is out of date with the installed pandas_ta"
        )
        raise IndicatorOutputMismatchError(msg)
    return {f"{config.name}_{suffix}": values for suffix, values in result.items()}


def _align(values: pd.Series, index: pd.DatetimeIndex, name: str) -> pd.Series:
    """Put an indicator's output on the data's index as float64, keeping NaN as NaN."""
    if len(values) != len(index):
        msg = (
            f"indicator {name!r} returned {len(values)} values for {len(index)} bars; "
            f"an indicator must produce one value per bar"
        )
        raise IndicatorError(msg)
    return pd.Series(np.asarray(values, dtype=DTYPE), index=index, name=name)


def _leading_nan_count(values: pd.Series) -> int:
    """How many bars at the start of ``values`` are undefined.

    Measured rather than trusted. A declared warm-up can drift from the library's actual
    behaviour across versions; taking the larger of the two is always safe, because
    over-suppressing costs a few bars while under-suppressing trades on undefined data.
    """
    defined = values.notna().to_numpy()
    if not defined.any():
        return len(values)
    return int(defined.argmax())
