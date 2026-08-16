"""Parameter schemas for registered indicators.

Each indicator owns its schema. This is the replacement for the legacy engine's single
21-field allowlist shared by every type, under which ``{type: sma, window: 20, tenkan: 9}``
validated cleanly and silently discarded ``tenkan`` (spec section 3.5).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, create_model


def param(params: BaseModel, name: str) -> Any:
    """Read one parameter from a validated schema instance.

    Spec callbacks receive a ``BaseModel`` because each indicator has its own schema, so the
    attribute cannot be named statically. This keeps that access in one typed place.
    """
    return getattr(params, name)


class ParamsBase(BaseModel):
    """Strict, closed, immutable parameter set."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class NoParams(ParamsBase):
    """For indicators that take no parameters at all."""


def window_model(type_name: str, default: int) -> type[ParamsBase]:
    """Build a one-parameter schema for a lookback-only indicator.

    ``window`` is the user-facing name across every indicator, for continuity with existing
    strategy files; it is remapped to pandas_ta's ``length`` at the library boundary.
    """
    model: type[ParamsBase] = create_model(
        f"{type_name.capitalize()}Params",
        __base__=ParamsBase,
        window=(Annotated[int, Field(ge=1)], default),
    )
    return model


class MacdParams(ParamsBase):
    """MACD: two EMAs and a signal line."""

    fast: Annotated[int, Field(ge=1)] = 12
    slow: Annotated[int, Field(ge=1)] = 26
    signal: Annotated[int, Field(ge=1)] = 9


class FastSlowParams(ParamsBase):
    """Indicators defined by a fast and a slow period."""

    fast: Annotated[int, Field(ge=1)] = 12
    slow: Annotated[int, Field(ge=1)] = 26


class BBandsParams(ParamsBase):
    """Bollinger Bands: a moving average plus a standard-deviation envelope."""

    window: Annotated[int, Field(ge=2)] = 20
    std: Annotated[float, Field(gt=0)] = 2.0


class StochParams(ParamsBase):
    """Stochastic oscillator."""

    k: Annotated[int, Field(ge=1)] = 14
    d: Annotated[int, Field(ge=1)] = 3
    smooth_k: Annotated[int, Field(ge=1)] = 3


class StochRsiParams(ParamsBase):
    """Stochastic RSI."""

    window: Annotated[int, Field(ge=1)] = 14
    k: Annotated[int, Field(ge=1)] = 3
    d: Annotated[int, Field(ge=1)] = 3


class DonchianParams(ParamsBase):
    """Donchian channels, which take separate lower and upper lookbacks."""

    lower_length: Annotated[int, Field(ge=1)] = 20
    upper_length: Annotated[int, Field(ge=1)] = 20


class SupertrendParams(ParamsBase):
    """Supertrend: an ATR band around price."""

    window: Annotated[int, Field(ge=1)] = 7
    multiplier: Annotated[float, Field(gt=0)] = 3.0


class PsarParams(ParamsBase):
    """Parabolic SAR acceleration factors."""

    af0: Annotated[float, Field(gt=0)] = 0.02
    af: Annotated[float, Field(gt=0)] = 0.2


class IchimokuParams(ParamsBase):
    """Ichimoku periods.

    ``senkou`` is accepted because it shapes the calculation, but the spans it produces are
    projected into the future and are never exposed -- see the catalogue entry.
    """

    tenkan: Annotated[int, Field(ge=1)] = 9
    kijun: Annotated[int, Field(ge=1)] = 26
    senkou: Annotated[int, Field(ge=1)] = 52
