from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .adapters import BaseAdapter, built_in_adapters
from .config import default_database_path, supports_current_python
from .schemas import SourceKind
from .store import TABLE_NAMES, DuckDBStore

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")
db_app = typer.Typer(help="Manage the local DuckDB store.")


@app.callback()
def main() -> None:
    pass


@app.command()
def version() -> None:
    """Print the session-doctor version."""
    console.print(__version__)


@app.command()
def doctor(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to check. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
) -> None:
    """Check local prerequisites without modifying agent session directories."""
    database_path = database_path_from_option(db)
    checks = [
        ("Python version", supports_current_python(), sys.version.split()[0]),
        (
            "DuckDB import",
            importlib.util.find_spec("duckdb") is not None,
            "available" if importlib.util.find_spec("duckdb") else "missing",
        ),
    ]

    database_path_valid = database_path_is_valid(database_path)
    checks.append(
        (
            "Database path",
            database_path_valid,
            str(database_path),
        )
    )

    table = Table(title="session-doctor doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    has_errors = False
    has_warnings = False

    for check_name, ok, detail in checks:
        if not ok:
            has_errors = True
        table.add_row(check_name, "ok" if ok else "error", detail)

    for adapter in built_in_adapters():
        root = adapter.default_roots()[0]
        exists = root.exists()
        if not exists:
            has_warnings = True
        table.add_row(
            f"{adapter.display_name} sessions",
            "found" if exists else "missing",
            str(root),
        )

    console.print(table)
    if has_errors:
        console.print("[red]Result: failed[/red]")
        raise typer.Exit(1)

    result = "ok with warnings" if has_warnings else "ok"
    console.print(f"[green]Result: {result}[/green]")


@adapters_app.command("list")
def list_adapters(
    scan: Annotated[
        bool,
        typer.Option(
            "--scan",
            help="Count candidate source files under each default adapter root.",
        ),
    ] = False,
) -> None:
    """Show built-in adapters and their default roots."""
    table = Table(title="Built-in adapters")
    table.add_column("Adapter")
    table.add_column("Root")
    table.add_column("Status")
    if scan:
        table.add_column("Candidates")

    for adapter in built_in_adapters():
        root = adapter.default_roots()[0]
        exists = root.exists()
        row = [
            adapter.display_name,
            str(root),
            "found" if exists else "missing",
        ]
        if scan:
            row.append(scan_adapter_summary(adapter) if exists else "0")
        table.add_row(*row)

    console.print(table)


@db_app.command("init")
def init_database(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to initialize. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
) -> None:
    """Create the local DuckDB database and schema tables."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    store = DuckDBStore(database_path)
    info = store.initialize()
    console.print(f"Initialized DuckDB store: {info.database_path}")
    console.print(f"Schema version: {info.schema_version}")
    console.print(f"Tables: {len(info.tables)}/{len(TABLE_NAMES)}")


@db_app.command("info")
def database_info(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to inspect. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
) -> None:
    """Show local DuckDB database path and schema status."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    store = DuckDBStore(database_path)
    info = store.info()

    table = Table(title="DuckDB store")
    table.add_column("Property")
    table.add_column("Value")
    table.add_row("Path", str(info.database_path))
    table.add_row("Exists", "yes" if info.exists else "no")
    table.add_row("Schema version", str(info.schema_version or "unknown"))
    table.add_row("Tables", f"{len(info.tables)}/{len(TABLE_NAMES)}")
    console.print(table)


def os_access_writable(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def path_can_be_created(path: Path) -> bool:
    current_path = path.expanduser()
    while not current_path.exists() and current_path != current_path.parent:
        current_path = current_path.parent
    return os_access_writable(current_path)


def database_path_is_valid(path: Path) -> bool:
    expanded_path = path.expanduser()
    if expanded_path.exists() and not expanded_path.is_file():
        return False
    return path_can_be_created(expanded_path.parent)


def database_path_from_option(path: Path | None) -> Path:
    return path.expanduser() if path else default_database_path()


def require_valid_database_path(path: Path) -> None:
    if database_path_is_valid(path):
        return
    console.print(f"[red]Invalid database path:[/red] {path}")
    raise typer.Exit(1)


def scan_adapter_summary(adapter: BaseAdapter) -> str:
    sources = adapter.discover()
    counts = {source_kind: 0 for source_kind in SourceKind}
    for source in sources:
        counts[source.source_kind] += 1
    populated_counts = [
        f"{source_kind.value}={count}" for source_kind, count in counts.items() if count
    ]
    return ", ".join(populated_counts) if populated_counts else "0"


def not_implemented(command_name: str) -> None:
    console.print(f"[yellow]{command_name} is not implemented in Phase 1.[/yellow]")
    raise typer.Exit(2)


@app.command()
def ingest() -> None:
    """Reserved for future session ingestion."""
    not_implemented("ingest")


@app.command()
def analyze(session_id: str) -> None:
    """Reserved for future session analysis."""
    _ = session_id
    not_implemented("analyze")


@app.command()
def report(session_id: str) -> None:
    """Reserved for future report generation."""
    _ = session_id
    not_implemented("report")


@app.command()
def graph(session_id: str) -> None:
    """Reserved for future graph projection."""
    _ = session_id
    not_implemented("graph")


app.add_typer(adapters_app, name="adapters")
app.add_typer(db_app, name="db")
