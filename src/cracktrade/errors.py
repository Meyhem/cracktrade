"""Exception taxonomy.

Every error the engine raises derives from :class:`CracktradeError`. Errors carry enough
context to be rendered directly to a user without a traceback, because the CLI and the future
API both present them that way.

Defect D10 in ``docs/ENGINE_SPEC.md``: the legacy engine caught bare ``Exception`` and returned
plausible-looking zeros. Nothing here is ever converted into a result. Ruff's ``BLE`` rule
enforces the absence of blind excepts.
"""

from __future__ import annotations


class CracktradeError(Exception):
    """Base class for every error raised by the library."""


# --------------------------------------------------------------------------- configuration


class ConfigError(CracktradeError):
    """The strategy configuration could not be loaded or is invalid."""


class StrategyValidationError(ConfigError):
    """One or more strategy fields failed validation.

    Carries the individual messages so a caller can render them without reparsing a blob.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        body = "\n".join(f"  - {issue}" for issue in issues)
        super().__init__(f"Please fix the following issues in your strategy file:\n{body}")


# --------------------------------------------------------------------------- market data


class DataError(CracktradeError):
    """Market data could not be obtained or does not satisfy the engine's contract."""


class DataUnavailableError(DataError):
    """The provider returned nothing for the requested ticker and range."""


class DataContractError(DataError):
    """A frame violates the OHLCV contract in ``ENGINE_SPEC.md`` section 4.1."""


class InsufficientHistoryError(DataError):
    """Not enough bars survive to warm up the strategy's indicators (defect D11)."""


class DataQualityError(DataError):
    """The history is complete enough to load but too damaged to backtest on.

    Raised when forward-filling had to fabricate more of the series than the engine is willing
    to draw conclusions from (audit finding A3).
    """


# --------------------------------------------------------------------------- indicators


class IndicatorError(CracktradeError):
    """An indicator could not be resolved or computed."""


class UnknownIndicatorError(IndicatorError):
    """The requested indicator ``type`` is not present in the registry."""


class IndicatorOutputMismatchError(IndicatorError):
    """A registry entry's declared outputs disagree with what the implementation returned.

    This is an engine bug or a library upgrade, never a user error (spec section 5.4).
    """


class IndicatorParameterMismatchError(IndicatorError):
    """A declared parameter would not reach the library function that implements it.

    Every pandas_ta function takes ``**kwargs``, so an unrecognised keyword is *accepted and
    ignored* rather than rejected. A parameter the user set in YAML then has no effect and the
    result is computed at the library's default -- silently. This is an engine bug or a library
    upgrade, never a user error (spec section 5.5).
    """


# --------------------------------------------------------------------------- signals


class SignalError(CracktradeError):
    """A signal expression is invalid."""


class SignalSyntaxError(SignalError):
    """The expression does not parse, or uses a construct outside the permitted grammar."""


class UnknownSymbolError(SignalError):
    """The expression references a name that is not in the indicator namespace."""


class CausalityViolationError(CracktradeError):
    """An operation would let information from the future reach an earlier bar.

    Raised by ``causal_shift`` on a negative shift, and by the truncation-equivalence harness.
    This is never a recoverable condition: it means the engine would produce a result that
    could not have been achieved in live trading.
    """


# --------------------------------------------------------------------------- backtest


class BacktestError(CracktradeError):
    """The simulation could not be completed."""


# --------------------------------------------------------------------------- optimization


class OptimizationError(CracktradeError):
    """The parameter search could not be completed."""


class NoOptimizableParametersError(OptimizationError):
    """The strategy exposes no numeric parameters to search over."""


class EvolutionError(OptimizationError):
    """A strategy could not be evolved.

    A subclass of :class:`OptimizationError` because it is the same kind of failure to the
    caller -- a search that could not be completed -- and so maps onto the same exit code
    without the CLI having to learn a new branch.
    """


# --------------------------------------------------------------------------- authoring


class AuthoringError(CracktradeError):
    """A strategy file could not be drafted from a description."""


class AuthoringUnavailableError(AuthoringError):
    """The model that drafts strategy files could not be reached.

    Distinct from a draft that came back wrong, which is not an error at all: an invalid draft
    is returned to the user with the validator's complaints attached. This means nothing was
    produced -- the CLI is missing, its credentials have expired, the request timed out. The
    detail carries whatever the tool itself said, because "generation failed" tells a user
    nothing they can act on and "OAuth session expired" tells them exactly what to do.
    """


# --------------------------------------------------------------------------- cancellation


class RunCancelled(CracktradeError):  # noqa: N818 - a signal, not a fault; see the docstring
    """The caller asked for the run to stop, and it did.

    Not a failure: nothing went wrong and nothing was computed incorrectly. It is an error
    class only because unwinding is how a long call stack stops. Interfaces report it as its
    own outcome -- neither a result nor a fault -- because a partial search is not a cheaper
    search, it is an unfinished one, and reporting its intermediate numbers would present a
    search that never ran to completion as if it had.

    Deliberately without the ``Error`` suffix the naming rule asks for. It is the same kind of
    exception as ``StopIteration``, ``KeyboardInterrupt`` and ``GeneratorExit``, none of which
    carries one either: control flow, not a defect. Naming it ``RunCancelledError`` would make
    every ``except`` site read as though something had gone wrong.
    """
