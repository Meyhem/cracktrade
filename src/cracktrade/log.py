"""Logging configuration.

The library never configures logging on import -- only interfaces do, via :func:`configure`.
Library code obtains loggers with :func:`get_logger` and never writes to stdout: stdout belongs
to the interface, which is what makes the same core usable from a CLI and from a server.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

_ROOT = "cracktrade"


def configure(*, verbose: bool = False, quiet: bool = False) -> None:
    """Install a handler for the library's logger.

    Args:
        verbose: emit DEBUG records.
        quiet: emit only WARNING and above.
    """
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logger = logging.getLogger(_ROOT)
    logger.setLevel(level)
    logger.handlers.clear()
    handler = RichHandler(
        # Diagnostics go to stderr. RichHandler defaults to stdout, which would put log lines
        # into the middle of `--format json` output and make it unparseable -- stdout carries
        # the requested result and nothing else.
        console=Console(stderr=True),
        rich_tracebacks=False,
        show_path=False,
        show_time=False,
        markup=False,
    )
    handler.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return the library logger for ``name``, which should be a module ``__name__``."""
    return logging.getLogger(name)
