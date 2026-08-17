"""``cracktrade-api`` command-line entry point.

Three subcommands over one codebase (spec section 15.4, D-12):

* ``serve``   -- the HTTP server;
* ``worker``  -- the run executor, a separate process by design;
* ``db``      -- migration management (``migrate``, ``status``, ``verify``).

The engine's own ``cracktrade`` CLI is untouched: two interfaces, one library.

Each subcommand is registered here and implemented in its own module as the phases land. Until
then they exit :data:`NOT_IMPLEMENTED` rather than printing a success they did not achieve.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import psycopg
import typer
import uvicorn
from psycopg.rows import TupleRow
from rich.console import Console

from cracktrade import __version__
from cracktrade.api.app import API_PREFIX
from cracktrade.api.db.console import render_migrate, render_status, render_verify
from cracktrade.api.db.migrate import plan
from cracktrade.api.settings import load_api_settings
from cracktrade.api.worker.runner import run_forever, shutdown_on_signal
from cracktrade.errors import CracktradeError
from cracktrade.log import configure

#: Exit status for a subcommand that exists but is not built yet. Distinct from the engine's
#: exit codes, which describe why a *run* failed; this says the command itself is unavailable.
NOT_IMPLEMENTED = 70

#: Conventional status for a process ended with Ctrl-C, as in the engine CLI.
INTERRUPTED = 130

#: ``db verify`` found the database is not fully migrated. Distinct from a refusal, which is an
#: error: pending migrations are an ordinary state, they simply are not a passing verification.
NOT_MIGRATED = 3

#: The database could not be reached, or the migration chain refused to run.
DATABASE_ERROR = 4

app = typer.Typer(
    name="cracktrade-api",
    help="HTTP interface and run worker for the cracktrade engine.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

db_app = typer.Typer(
    name="db",
    help="Database migrations.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(db_app)

#: Diagnostics go to stderr; stdout carries requested output only (spec section 13.2, which
#: applies to these processes too).
err_console = Console(stderr=True)


def _unimplemented(what: str, phase: str) -> None:
    """Report a subcommand that is registered but not yet built."""
    err_console.print(f"[yellow]{what} is not implemented yet[/yellow] (arrives in {phase}).")
    raise typer.Exit(NOT_IMPLEMENTED)


@contextmanager
def _database() -> Iterator[psycopg.Connection[TupleRow]]:
    """Open the configured database, reporting an unreachable server as an error, not a crash.

    A wrong password or a database that is not running is an ordinary operational condition
    and deserves a sentence saying so; a traceback here would only bury it.
    """
    settings = load_api_settings()
    try:
        # Autocommit: the migration engine opens each migration's transaction itself, and an
        # implicit transaction underneath would silently reduce those to savepoints.
        connection = psycopg.connect(settings.database_url, autocommit=True)
    except psycopg.OperationalError as error:
        err_console.print(f"[bold red]cannot reach the database:[/bold red] {error}")
        err_console.print(
            "Start it with `docker compose up -d`, or set CRACKTRADE_API_DATABASE_URL."
        )
        raise typer.Exit(DATABASE_ERROR) from error
    try:
        yield connection
    finally:
        connection.close()


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Debug logging."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Warnings and errors only."),
) -> None:
    """Global options."""
    configure(verbose=verbose, quiet=quiet)


@app.command()
def serve(
    reload: bool = typer.Option(False, "--reload", help="Restart on code changes."),
) -> None:
    """Run the HTTP server.

    Refuses to start against a database with pending migrations. A server serving requests
    against half a schema fails later, in a request, and less clearly than it would here.
    """
    settings = load_api_settings()
    with _database() as connection:
        if not plan(connection).is_up_to_date:
            err_console.print(
                "[bold red]the database is not fully migrated[/bold red] "
                "-- run `cracktrade-api db migrate` first."
            )
            raise typer.Exit(NOT_MIGRATED)

    err_console.print(f"serving on http://{settings.host}:{settings.port}{API_PREFIX}")
    uvicorn.run(
        "cracktrade.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Run the queue worker that executes backtests, searches and validations.

    A separate process from the server by design: a walk-forward is minutes of CPU-bound work
    and must not sit inside a request. Refuses to start against an unmigrated database, for the
    same reason `serve` does.
    """
    settings = load_api_settings()
    with _database() as connection:
        if not plan(connection).is_up_to_date:
            err_console.print(
                "[bold red]the database is not fully migrated[/bold red] "
                "-- run `cracktrade-api db migrate` first."
            )
            raise typer.Exit(NOT_MIGRATED)

    err_console.print("worker ready; waiting for runs")
    halt = threading.Event()
    with shutdown_on_signal(halt):
        run_forever(settings, stop=halt)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply every pending migration."""
    with _database() as connection:
        render_migrate(connection)


@db_app.command("status")
def db_status() -> None:
    """Show applied and pending migrations."""
    with _database() as connection:
        render_status(connection)


@db_app.command("verify")
def db_verify() -> None:
    """Check the migration ledger against the migration files, applying nothing.

    Exits non-zero when the database is not fully migrated, so a deployment can gate on it.
    """
    with _database() as connection:
        if not render_verify(connection):
            raise typer.Exit(NOT_MIGRATED)


@app.command()
def version() -> None:
    """Show the version."""
    typer.echo(f"cracktrade-api {__version__}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the application and return a process exit status.

    The error boundary is part of the invocable surface, exactly as in the engine CLI
    (:func:`cracktrade.cli.app.main`): an embedding caller gets the same behaviour as the
    console script rather than a raised exception where a code was expected.

    Standalone mode is deliberate. Typer vendors click privately, so click's exception types
    are not importable; in standalone mode click converts everything it handles -- ``--help``,
    ``typer.Exit``, and usage errors such as an unknown subcommand -- into ``SystemExit``,
    which is public API and enough to cover the surface.
    """
    try:
        app(args=argv)
    except CracktradeError as error:
        # A migration refusal, chiefly. These are user-facing by construction and carry the
        # fix in their message, so a traceback would add nothing but noise.
        err_console.print(f"[bold red]error:[/bold red] {error}")
        return DATABASE_ERROR
    except KeyboardInterrupt:
        err_console.print("[yellow]interrupted[/yellow]")
        return INTERRUPTED
    except SystemExit as exit_signal:
        return _status_of(exit_signal)
    return 0


def _status_of(exit_signal: SystemExit) -> int:
    """The status a ``SystemExit`` carries. ``None`` means success; a string means failure."""
    code = exit_signal.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    err_console.print(f"[bold red]error:[/bold red] {code}")
    return 1


if __name__ == "__main__":  # pragma: no cover - console-script parity
    sys.exit(main())
