from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .adapters import built_in_adapters
from .analysis_workflow import analyze_session
from .artifacts import analysis_payload, artifact_path_for_analysis, write_analysis_artifact
from .cli_options import (
    adapter_for_ingest,
    database_path_from_option,
    database_path_is_valid,
    os_access_writable,
    path_can_be_created,
    require_analysis_output_format,
    require_existing_database_path,
    require_valid_database_path,
    sources_for_ingest,
)
from .cli_renderers import (
    ANALYSIS_SUMMARY_FEATURES,
    render_adapters_table,
    render_analysis_summary,
    render_database_info,
    render_doctor_table,
    render_ingest_summary,
    render_sessions_table,
)
from .config import supports_current_python
from .ingest_workflow import IngestSummary, ingest_sources
from .store import TABLE_NAMES, DuckDBStore

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")
db_app = typer.Typer(help="Manage the local DuckDB store.")
sessions_app = typer.Typer(help="Inspect ingested sessions.")

__all__ = [
    "ANALYSIS_SUMMARY_FEATURES",
    "IngestSummary",
    "adapter_for_ingest",
    "analysis_payload",
    "app",
    "artifact_path_for_analysis",
    "database_path_from_option",
    "database_path_is_valid",
    "os_access_writable",
    "path_can_be_created",
    "require_valid_database_path",
    "sources_for_ingest",
    "write_analysis_artifact",
]


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

    has_errors, has_warnings = render_doctor_table(checks, built_in_adapters(), console)
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
    render_adapters_table(built_in_adapters(), scan, console)


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
    render_database_info(store.info(), console)


@sessions_app.command("list")
def list_sessions(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to inspect. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
) -> None:
    """List sessions stored in DuckDB."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    store = DuckDBStore(database_path)
    render_sessions_table(store.list_session_summaries(), console)


@app.command()
def ingest(
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Agent adapter to ingest. Supported values: codex, pi.",
        ),
    ],
    source: Annotated[
        Path | None,
        typer.Option(
            "--source",
            help="Session JSONL file or directory. Defaults to the adapter session root.",
        ),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to write. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
) -> None:
    """Parse and store local session records."""
    adapter = adapter_for_ingest(agent)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    sources = sources_for_ingest(adapter, source)
    store = DuckDBStore(database_path)
    summary = ingest_sources(adapter, sources, store, console)
    render_ingest_summary(summary, database_path, console)


@app.command()
def analyze(
    session_id: str,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to inspect. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: terminal or json.",
        ),
    ] = "terminal",
    artifact: Annotated[
        Path | None,
        typer.Option(
            "--artifact",
            help="Path for the machine-readable JSON artifact.",
        ),
    ] = None,
    no_artifact: Annotated[
        bool,
        typer.Option(
            "--no-artifact",
            help="Skip writing the default JSON artifact.",
        ),
    ] = False,
) -> None:
    """Analyze one ingested session and persist derived rows."""
    require_analysis_output_format(output_format)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)

    store = DuckDBStore(database_path)
    result = analyze_session(store, session_id, database_path, artifact, no_artifact, console)

    if output_format == "json":
        typer.echo(json.dumps(result.payload, indent=2, sort_keys=True, default=str))
        return

    render_analysis_summary(
        session_id,
        result.analysis_run,
        result.session_features,
        result.classifications,
        console,
    )


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
app.add_typer(sessions_app, name="sessions")


def not_implemented(command_name: str) -> None:
    console.print(f"[yellow]{command_name} is not implemented yet.[/yellow]")
    raise typer.Exit(2)
