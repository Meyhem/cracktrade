"""The run worker.

A separate process from the server, because a walk-forward is minutes of CPU-bound work and
must not sit inside the event loop. It claims queued runs from Postgres, executes the engine,
reports progress, and lands terminal state -- see spec section 14.5.
"""

from __future__ import annotations
