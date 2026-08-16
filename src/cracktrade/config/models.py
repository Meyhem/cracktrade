"""The strategy schema.

Normative reference: ``docs/ENGINE_SPEC.md`` section 3. Every model is strict (no type
coercion), forbids unknown keys, and is frozen -- a parsed strategy is a value, and the
optimizer produces new ones rather than mutating one in place.

Two deliberate departures from the legacy schema, both recorded in the spec:

* ``universe.ticker`` is a single symbol, not a list (spec section 3.3).
* Indicator parameters are *not* a fixed 21-field allowlist shared by every indicator type.
  Base fields are validated here; type-specific parameters are collected into
  :attr:`IndicatorConfig.params` and validated against the registry's per-indicator schema in
  :mod:`cracktrade.indicators` (spec section 3.5).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Names bound to the raw OHLCV series in the signal namespace. An indicator may not take one
#: of these as its name, or it would shadow the price series it is derived from.
PRICE_SERIES_NAMES: frozenset[str] = frozenset({"open", "high", "low", "close", "volume"})

#: Base fields every indicator entry has, regardless of type. Anything else is a type-specific
#: parameter and is routed to :attr:`IndicatorConfig.params`.
_INDICATOR_BASE_FIELDS: frozenset[str] = frozenset({"name", "type", "source", "optimize"})


class PriceSeries(StrEnum):
    """A raw price series usable as an indicator's primary input."""

    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"


class PositionSizingType(StrEnum):
    """How much capital a single trade consumes.

    See spec section 3.8 for the mapping onto vectorbt sizing, including the correction of the
    legacy documentation's claim that ``fixed_pct`` is a percentage of total equity. It is a
    percentage of *available cash*.
    """

    FIXED_PCT = "fixed_pct"
    FIXED_CASH = "fixed_cash"
    FIXED_SHARES = "fixed_shares"


def _coerce_enum[E: StrEnum](value: object, enum_type: type[E]) -> object:
    """Accept the YAML string form of an enum under strict validation.

    Strict mode will not turn ``"fixed_pct"`` into ``PositionSizingType.FIXED_PCT`` on its own,
    and every strategy file in existence writes the string. The trade-off is deliberate: we
    keep strict mode's refusal to coerce numbers, and hand-roll the one coercion we want, with
    an error message that lists the accepted values instead of naming a Python class.
    """
    if isinstance(value, str):
        try:
            return enum_type(value.strip().lower())
        except ValueError:
            accepted = ", ".join(member.value for member in enum_type)
            msg = f"{value!r} is not one of: {accepted}"
            raise ValueError(msg) from None
    return value


class _Base(BaseModel):
    """Strict, closed, immutable."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class OptimizeBounds(_Base):
    """An explicit search range for one parameter, overriding the default +/-50% (spec 9.1)."""

    min: float
    max: float

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.min >= self.max:
            msg = f"min ({self.min}) must be less than max ({self.max})"
            raise ValueError(msg)
        return self


#: Per-parameter optimizer control. ``False`` pins every parameter of the section; a mapping
#: names individual parameters, each either pinned (``False``) or given explicit bounds.
OptimizeSpec = bool | dict[str, OptimizeBounds | Literal[False]]


class StrategyMeta(_Base):
    """The ``strategy`` section."""

    name: Annotated[str, Field(min_length=1)]

    @field_validator("name")
    @classmethod
    def _no_surrounding_space(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "must not be blank"
            raise ValueError(msg)
        return stripped


class Universe(_Base):
    """The ``universe`` section: what to trade and over what period.

    Exactly one ticker. Dates are real ``date`` objects, not the lexicographically compared
    strings the legacy engine used.
    """

    ticker: Annotated[str, Field(min_length=1)]
    start_date: date
    end_date: Annotated[date, Field(default_factory=lambda: datetime.now().astimezone().date())]

    @field_validator("ticker")
    @classmethod
    def _normalise_ticker(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "must not be blank"
            raise ValueError(msg)
        return stripped

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _accept_iso_strings(cls, value: object) -> object:
        """Accept ``"2020-01-01"`` as well as an unquoted YAML date.

        Strict mode would otherwise reject the quoted form, which is what every strategy file
        in circulation uses.
        """
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                msg = f"{value!r} is not a date in YYYY-MM-DD format"
                raise ValueError(msg) from None
        if isinstance(value, datetime):
            return value.date()
        return value

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.start_date >= self.end_date:
            msg = f"start_date ({self.start_date}) must be before end_date ({self.end_date})"
            raise ValueError(msg)
        return self


class ExecutionConfig(_Base):
    """The ``execution`` section: capital and trading frictions.

    Percentages are expressed as percent, not fractions: ``slippage_pct: 0.1`` is 0.1%.
    """

    initial_capital: Annotated[float, Field(gt=0)]
    slippage_pct: Annotated[float, Field(ge=0)]
    commission_pct: Annotated[float, Field(ge=0)]
    #: Annual rate as a fraction: 0.04 is 4% per year. Converted to a per-period rate before
    #: reaching vectorbt -- see spec section 8 and defect D4.
    risk_free_rate: float = 0.04

    @property
    def commission_fraction(self) -> float:
        """Commission as a fraction, for the portfolio's ``fees`` argument."""
        return self.commission_pct / 100.0

    @property
    def slippage_fraction(self) -> float:
        """Slippage as a fraction, for the portfolio's ``slippage`` argument."""
        return self.slippage_pct / 100.0


class IndicatorConfig(_Base):
    """One entry of the ``indicators`` list.

    Type-specific parameters are collected into :attr:`params` rather than being declared here,
    so adding an indicator never means editing this model.
    """

    name: Annotated[str, Field(min_length=1)]
    type: Annotated[str, Field(min_length=1)]
    source: PriceSeries = PriceSeries.CLOSE
    params: dict[str, Any] = Field(default_factory=dict)
    optimize: OptimizeSpec = True

    @model_validator(mode="before")
    @classmethod
    def _collect_type_specific_params(cls, data: object) -> object:
        """Route unrecognised keys into ``params`` instead of rejecting them.

        The registry -- not this model -- decides which parameters an indicator accepts.
        """
        if not isinstance(data, dict):
            return data
        if "params" in data:
            # Already normalised (round-trip through dump_strategy).
            return data
        known = {key: value for key, value in data.items() if key in _INDICATOR_BASE_FIELDS}
        extra = {key: value for key, value in data.items() if key not in _INDICATOR_BASE_FIELDS}
        return {**known, "params": extra}

    @field_validator("name")
    @classmethod
    def _usable_as_a_signal_name(cls, value: str) -> str:
        name = value.strip()
        if not name.isidentifier():
            msg = f"{value!r} is not a valid identifier, so it cannot be used in a signal"
            raise ValueError(msg)
        if name.lower() in PRICE_SERIES_NAMES:
            msg = f"{value!r} would shadow the built-in price series of the same name"
            raise ValueError(msg)
        if name.startswith("_"):
            msg = f"{value!r} must not start with an underscore"
            raise ValueError(msg)
        return name

    @field_validator("type")
    @classmethod
    def _normalise_type(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("source", mode="before")
    @classmethod
    def _accept_source_string(cls, value: object) -> object:
        return _coerce_enum(value, PriceSeries)


class EntryRule(_Base):
    """The strategy's entry condition. Exactly one per strategy (spec section 7.1)."""

    signal: Annotated[str, Field(min_length=1)]
    optimize: OptimizeSpec = True


class ExitRule(_Base):
    """The strategy's exit condition: a signal, stops, holding-period bounds, or a combination.

    Stop priority is ``atr_stop_multiplier`` > ``trailing_stop_pct`` > ``stop_loss_pct``; only
    the highest-priority one set is active. ``take_profit_pct`` is orthogonal and always
    applies. See spec section 3.7.
    """

    signal: str | None = None
    stop_loss_pct: Annotated[float, Field(gt=0)] | None = None
    trailing_stop_pct: Annotated[float, Field(gt=0)] | None = None
    atr_stop_multiplier: Annotated[float, Field(gt=0)] | None = None
    take_profit_pct: Annotated[float, Field(gt=0)] | None = None
    min_holding_days: Annotated[int, Field(ge=1)] | None = None
    max_holding_days: Annotated[int, Field(ge=1)] | None = None
    optimize: OptimizeSpec = True

    @property
    def active_stop(self) -> Literal["atr", "trailing", "fixed"] | None:
        """Which stop type wins the priority chain, if any."""
        if self.atr_stop_multiplier is not None:
            return "atr"
        if self.trailing_stop_pct is not None:
            return "trailing"
        if self.stop_loss_pct is not None:
            return "fixed"
        return None

    @property
    def shadowed_stops(self) -> tuple[str, ...]:
        """Stop fields that are set but lose the priority chain, so have no effect."""
        declared = [
            name
            for name, value in (
                ("atr_stop_multiplier", self.atr_stop_multiplier),
                ("trailing_stop_pct", self.trailing_stop_pct),
                ("stop_loss_pct", self.stop_loss_pct),
            )
            if value is not None
        ]
        return tuple(declared[1:])

    @model_validator(mode="after")
    def _must_be_able_to_exit(self) -> Self:
        """An exit with no mechanism holds its first position forever."""
        mechanisms = (
            self.signal,
            self.stop_loss_pct,
            self.trailing_stop_pct,
            self.atr_stop_multiplier,
            self.take_profit_pct,
            self.max_holding_days,
        )
        if all(mechanism is None for mechanism in mechanisms):
            msg = (
                "defines no way to exit a position: set at least one of signal, stop_loss_pct, "
                "trailing_stop_pct, atr_stop_multiplier, take_profit_pct, max_holding_days"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _holding_bounds_are_consistent(self) -> Self:
        if (
            self.min_holding_days is not None
            and self.max_holding_days is not None
            and self.min_holding_days >= self.max_holding_days
        ):
            msg = (
                f"min_holding_days ({self.min_holding_days}) must be less than "
                f"max_holding_days ({self.max_holding_days})"
            )
            raise ValueError(msg)
        return self


class PositionSizing(_Base):
    """The ``position_sizing`` section. Omitted means 100% of available cash per trade."""

    type: PositionSizingType
    value: Annotated[float, Field(gt=0)]

    @field_validator("type", mode="before")
    @classmethod
    def _accept_type_string(cls, value: object) -> object:
        return _coerce_enum(value, PositionSizingType)

    @model_validator(mode="after")
    def _percentage_is_within_range(self) -> Self:
        if self.type is PositionSizingType.FIXED_PCT and self.value > 100:
            msg = f"fixed_pct value ({self.value}) cannot exceed 100"
            raise ValueError(msg)
        return self


class Strategy(_Base):
    """A complete, structurally valid strategy."""

    strategy: StrategyMeta
    universe: Universe
    execution: ExecutionConfig
    indicators: tuple[IndicatorConfig, ...] = ()
    entry: EntryRule
    exit: ExitRule
    position_sizing: PositionSizing | None = None

    @field_validator("indicators", mode="before")
    @classmethod
    def _accept_yaml_lists(cls, value: object, /) -> object:
        """Reject the dict shape (defect D14) and accept the list shape YAML produces.

        ``indicators`` is stored as a tuple so a parsed strategy is a value, but strict mode
        will not coerce a list into one, and YAML always hands us a list.
        """
        if isinstance(value, dict):
            msg = (
                "must be a list of entries, not a mapping; write "
                "'- name: my_sma' entries rather than 'my_sma: {...}'"
            )
            # ValueError, not TypeError: pydantic only collects ValueError and AssertionError
            # from validators, and a TypeError here would escape as an internal crash.
            raise ValueError(msg)  # noqa: TRY004
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _indicator_names_are_unique(self) -> Self:
        names = [item.name for item in self.indicators]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            msg = f"indicators contains duplicate names: {', '.join(duplicates)}"
            raise ValueError(msg)
        return self

    def indicator(self, name: str) -> IndicatorConfig | None:
        """Look up an indicator by name."""
        return next((item for item in self.indicators if item.name == name), None)
