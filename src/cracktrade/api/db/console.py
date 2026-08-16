"""Rendering for the ``cracktrade-api db`` subcommands.

Separated from :mod:`cracktrade.api.db.migrate` so the engine stays a library: it returns
objects and raises typed errors, and this module decides what a human sees. The CLI's own
module then only opens a connection and calls one of these.

Stream discipline (spec section 13.2) applies here too: the requested output -- the status
table -- goes to stdout; everything else goes to stderr.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import TupleRow
from rich.console import Console

from cracktrade.api.db.migrate import migrate, plan

out_console = Console()
err_console = Console(stderr=True)


def render_status(connection: psycopg.Connection[TupleRow]) -> None:
    """Print what is applied and what is pending."""
    current = plan(connection)

    for record in current.applied:
        applied_on = record.applied_at.strftime("%Y-%m-%d %H:%M")
        out_console.print(
            f"[green]applied[/green]  {record.version:04d}_{record.name}"
            f"  {applied_on}  {record.duration_ms} ms"
        )
    for migration in current.pending:
        out_console.print(f"[yellow]pending[/yellow]  {migration.path.name}")

    if not current.applied and not current.pending:
        out_console.print("no migrations")
    elif current.is_up_to_date:
        err_console.print(f"[green]up to date[/green] ({len(current.applied)} applied)")
    else:
        err_console.print(f"[yellow]{len(current.pending)} pending[/yellow]")


def render_verify(connection: psycopg.Connection[TupleRow]) -> bool:
    """Report whether the chain is applied and undisturbed. Applies nothing.

    Returns ``True`` when the database is up to date. Pending migrations are a failure here,
    not a state to report and continue from: this is the pre-flight check a deployment runs,
    and a server that starts against a half-migrated database fails later and less clearly.
    """
    current = plan(connection)
    if current.is_up_to_date:
        err_console.print(f"[green]verified[/green] {len(current.applied)} migration(s) applied")
        return True
    for migration in current.pending:
        err_console.print(f"[yellow]pending[/yellow]  {migration.path.name}")
    return False


def render_migrate(connection: psycopg.Connection[TupleRow]) -> None:
    """Apply pending migrations and report what happened."""
    applied = migrate(connection)
    if not applied:
        err_console.print("[green]nothing to do[/green] -- already up to date")
        return
    for migration in applied:
        err_console.print(f"[green]applied[/green]  {migration.path.name}")
