"""``cracktrade indicators`` -- list the registered indicator catalogue."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cracktrade.indicators import registry
from cracktrade.indicators.catalogue import install
from cracktrade.indicators.describe import describe_catalogue

console = Console()


def indicators(
    search: Annotated[
        str | None,
        typer.Argument(help="Only show types containing this text."),
    ] = None,
) -> None:
    """List available indicator types, their parameters, and the names they produce."""
    install()

    table = Table(title=None, show_lines=False)
    table.add_column("type", style="bold")
    table.add_column("parameters")
    table.add_column("produces")
    table.add_column("description")

    needle = search.lower() if search else None
    shown = 0
    for indicator in describe_catalogue():
        if needle and needle not in indicator.type:
            continue
        parameters = ", ".join(
            f"{parameter.name}={parameter.default}" for parameter in indicator.parameters
        )
        produces = ", ".join(indicator.namespace_names("<name>"))
        table.add_row(indicator.type, parameters or "-", produces, indicator.description)
        shown += 1

    if not shown:
        console.print(f"no indicator type matches {search!r}")
        return

    console.print(table)
    console.print(f"{shown} of {registry.count()} indicator types")
