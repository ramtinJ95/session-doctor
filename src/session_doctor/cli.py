from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
import typer

from . import __version__
from .config import default_adapter_roots, default_database_path, supports_current_python

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")


@app.callback()
def main() -> None:
    pass


@app.command()
def version() -> None:
    """Print the session-doctor version."""
    console.print(__version__)


@app.command()
def doctor(
    db: Path | None = typer.Option(
        None,
        "--db",
        help="DuckDB path to check. Defaults to SESSION_DOCTOR_DB or app data.",
    ),
) -> None:
    """Check local prerequisites without modifying agent session directories."""
    database_path = db.expanduser() if db else default_database_path()
    checks = [
        ("Python version", supports_current_python(), sys.version.split()[0]),
        (
            "DuckDB import",
            importlib.util.find_spec("duckdb") is not None,
            "available" if importlib.util.find_spec("duckdb") else "missing",
        ),
    ]

    database_parent_writable = path_can_be_created(database_path.parent)
    checks.append(
        (
            "Database path",
            database_parent_writable,
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

    for adapter_root in default_adapter_roots():
        exists = adapter_root.path.exists()
        if not exists:
            has_warnings = True
        table.add_row(
            f"{adapter_root.display_name} sessions",
            "found" if exists else "missing",
            str(adapter_root.path),
        )

    console.print(table)
    if has_errors:
        console.print("[red]Result: failed[/red]")
        raise typer.Exit(1)

    result = "ok with warnings" if has_warnings else "ok"
    console.print(f"[green]Result: {result}[/green]")


@adapters_app.command("list")
def list_adapters(
    scan: bool = typer.Option(
        False,
        "--scan",
        help="Count candidate source files under each default adapter root.",
    ),
) -> None:
    """Show built-in adapters and their default roots."""
    table = Table(title="Built-in adapters")
    table.add_column("Adapter")
    table.add_column("Root")
    table.add_column("Status")
    if scan:
        table.add_column("Candidates")

    for adapter_root in default_adapter_roots():
        exists = adapter_root.path.exists()
        row = [
            adapter_root.display_name,
            str(adapter_root.path),
            "found" if exists else "missing",
        ]
        if scan:
            row.append(str(count_jsonl_candidates(adapter_root.path)) if exists else "0")
        table.add_row(*row)

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


def count_jsonl_candidates(path: Path) -> int:
    return sum(1 for candidate in path.rglob("*.jsonl") if candidate.is_file())


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
