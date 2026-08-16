"""Progress reporting and cooperative cancellation.

A backtest takes seconds; a walk-forward takes tens of minutes. Something has to be able to say
how far along it is and to ask it to stop, and neither belongs in the engine's own vocabulary:
the engine computes results and knows nothing about queues, terminals or HTTP requests.

So both arrive as callables. The CLI passes a progress bar's update method; the API's worker
passes one that writes to a database row; a test passes nothing and the engine behaves exactly
as it did before hooks existed. That last property is asserted rather than assumed
(``tests/test_control.py``) -- a hook that changed a result would be a hook that changed what
the numbers mean.

Cancellation is **cooperative**. Nothing is killed mid-computation: the engine checks between
generations and between folds, at points where stopping leaves nothing half-written. A run
cancelled this way produces no result at all, which is the honest outcome -- a partial search
is not a cheaper search, it is an unfinished one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cracktrade.errors import RunCancelled

#: Called with a short stage description and a percentage in ``[0, 100]``.
ProgressCallback = Callable[[str, float], None]

#: Called with nothing; returns True when the run should stop.
StopCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class RunControl:
    """How a long run reports progress and learns it should stop.

    The default instance does neither, so ``RunControl()`` is the "no hooks" case and callers
    never have to pass ``None`` down through several layers.
    """

    on_progress: ProgressCallback | None = None
    should_stop: StopCallback | None = None

    def progress(self, stage: str, percent: float) -> None:
        """Report progress, if anyone is listening.

        Failures in a caller's callback are deliberately *not* caught. A progress hook that
        raises is a bug in the caller, and swallowing it here would leave a run that reports
        nothing for twenty minutes with no explanation.
        """
        if self.on_progress is not None:
            self.on_progress(stage, max(0.0, min(100.0, percent)))

    @property
    def cancelled(self) -> bool:
        """Whether the caller has asked this run to stop."""
        return self.should_stop is not None and self.should_stop()

    def raise_if_cancelled(self) -> None:
        """Stop the run if cancellation has been requested.

        Raises:
            RunCancelled: the caller asked for a stop.
        """
        if self.cancelled:
            raise RunCancelled("the run was cancelled")


#: Shared instance for the common case of no hooks at all.
NO_CONTROL = RunControl()

__all__ = [
    "NO_CONTROL",
    "ProgressCallback",
    "RunCancelled",
    "RunControl",
    "StopCallback",
]
