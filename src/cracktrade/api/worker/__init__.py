"""The worker.

A separate process from the server, because a walk-forward is minutes of CPU-bound work and
must not sit inside the event loop. It claims queued runs from Postgres, executes the engine,
reports progress, and lands terminal state -- see spec section 14.5.

The same process also drives continuous prospecting (section 19.7), and the ordering inside
:func:`~cracktrade.api.worker.runner.run_forever` is the priority: runs first, then one tick of
one sweep, then back to the top. A second process for sweeps was rejected -- both halves are
CPU-bound on the same cores, and two processes would compete for them with nothing arbitrating.
"""

from __future__ import annotations

import os
import socket


def worker_name() -> str:
    """Identifies the process holding a lease, for a human reading the row later.

    Lives on the package rather than in :mod:`~cracktrade.api.worker.runner` because both the
    run loop and the prospecting loop stamp it, and a process must appear under one name in
    both tables -- otherwise a lease held by this worker looks, from the other table, like a
    lease held by somebody else.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


__all__ = ["worker_name"]
