"""Choosing how a result leaves the process.

Three formats, one rule: **stdout carries the requested output and nothing else**. Progress,
warnings and verdicts go to stderr, so ``cracktrade backtest s.yaml --format json | jq`` works
without the user having to filter anything out.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from cracktrade.serialize import to_json, to_yaml

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console


class OutputFormat(StrEnum):
    """How to present a result."""

    #: Rich tables for a human reading a terminal.
    TABLE = "table"
    #: Machine-readable, for piping into another tool.
    JSON = "json"
    #: Machine-readable and diff-friendly.
    YAML = "yaml"

    @property
    def is_machine_readable(self) -> bool:
        """Whether this format is meant to be parsed rather than read."""
        return self is not OutputFormat.TABLE


def emit(
    result: Any,
    fmt: OutputFormat,
    *,
    console: Console,
    render: Callable[[Any, Console], None],
    output: Path | None = None,
) -> None:
    """Write ``result`` in the requested format.

    Args:
        result: the domain object to present.
        fmt: table, json, or yaml.
        console: where table output goes.
        render: the table renderer for this result type.
        output: write to this file instead of stdout. Table format is never written to a file --
            it carries terminal escape codes and is meant to be read, not stored.
    """
    if fmt is OutputFormat.TABLE:
        render(result, console)
        return

    text = to_json(result) if fmt is OutputFormat.JSON else to_yaml(result)

    if output is not None:
        output.write_text(text, encoding="utf-8")
        return

    # echo rather than console.print: rich would wrap and colourise, which corrupts JSON.
    typer.echo(text, nl=False)
