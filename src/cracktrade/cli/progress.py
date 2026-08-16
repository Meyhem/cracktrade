"""Search progress, reported to stderr.

Progress belongs on stderr, never stdout: ``cracktrade optimize s.yaml --format json | jq``
must receive JSON and nothing else. It is also suppressed unless stderr is a terminal, so a
redirected log does not fill with half-drawn progress bars.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@contextmanager
def search_progress(
    *, total: int, label: str, enabled: bool
) -> Iterator[Callable[[int, float], None]]:
    """Yield a per-generation callback that drives a progress bar.

    Yields a no-op when disabled, so callers never branch on whether progress is wanted.
    """
    if not enabled or not sys.stderr.isatty():
        yield lambda _generation, _convergence: None
        return

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} generations"),
        TimeElapsedColumn(),
        console=Console(stderr=True),
        transient=True,
    )
    with progress:
        task = progress.add_task(label, total=total)

        def advance(generation: int, _convergence: float) -> None:
            progress.update(task, completed=generation)

        yield advance
