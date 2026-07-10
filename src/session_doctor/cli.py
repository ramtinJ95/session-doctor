from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .adapters import RecoverableSourceError, built_in_adapters
from .analysis_workflow import (
    AnalysisArtifactError,
    AnalysisPersistenceError,
    AnalysisWorkflowError,
    SessionNotLoadableError,
    analyze_session,
)
from .artifacts import analysis_payload, artifact_path_for_analysis, write_analysis_artifact
from .batch_analysis import analyze_all_sessions, batch_analysis_payload
from .cli_options import (
    adapter_for_ingest,
    database_info_for_path,
    database_path_from_option,
    database_path_is_valid,
    os_access_writable,
    path_can_be_created,
    project_filters_from_options,
    require_analysis_output_format,
    require_current_database_schema,
    require_existing_database_path,
    require_graph_output_format,
    require_positive_limit,
    require_report_output_format,
    require_summary_output_format,
    require_valid_database_path,
    scope_filters_from_options,
    source_selection_for_ingest,
    sources_for_ingest,
    summary_filters_from_options,
    trend_filters_from_options,
)
from .cli_renderers import (
    ANALYSIS_SUMMARY_FEATURES,
    render_adapters_table,
    render_batch_analysis,
    render_database_info,
    render_doctor_table,
    render_project_report,
    render_sessions_table,
    render_summary,
    render_trends,
    scan_adapter_summary,
)
from .cli_renderers import (
    render_analysis_summary as _render_analysis_summary,
)
from .cli_renderers import (
    render_ingest_summary as _render_ingest_summary,
)
from .config import supports_current_python
from .graph_payload import graph_payload
from .graph_projection import project_graph
from .ingest_workflow import IngestSummary, ingest_sources
from .report_payload import build_session_report
from .report_renderers import render_session_report, render_session_report_markdown
from .schemas import AnalysisRun, SessionClassification, SessionFeature
from .store import TABLE_NAMES, DuckDBStore
from .summary_payload import summary_payload
from .trend_payload import project_payload, trend_payload

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")
db_app = typer.Typer(help="Manage the local DuckDB store.")
sessions_app = typer.Typer(help="Inspect ingested sessions.")
projects_app = typer.Typer(help="Inspect observed project path hints.")

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
    "require_current_database_schema",
    "require_valid_database_path",
    "render_summary",
    "render_analysis_summary",
    "render_ingest_summary",
    "scan_adapter_summary",
    "sources_for_ingest",
    "summary_filters_from_options",
    "summary_payload",
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
    require_current_database_schema(database_path, allow_empty=True)
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
    render_database_info(database_info_for_path(database_path), console)


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
    require_current_database_schema(database_path)
    store = DuckDBStore(database_path)
    render_sessions_table(store.list_session_summaries(), console)


@app.command()
def ingest(
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Agent adapter to ingest. Supported values: codex, claude, pi.",
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
    require_current_database_schema(database_path, allow_empty=True)
    selection = source_selection_for_ingest(adapter, source)
    sources = list(selection.sources)
    store = DuckDBStore(database_path)
    continue_on_source_error = source is None or source.expanduser().is_dir()
    try:
        summary = ingest_sources(
            adapter,
            sources,
            store,
            console,
            continue_on_source_error=continue_on_source_error,
            discovered_source_counts=selection.discovered_counts,
        )
    except RecoverableSourceError as exc:
        console.print(
            f"[red]Source failed:[/red] {exc.source_path} (category={exc.category}) {exc.detail}"
        )
        raise typer.Exit(1) from exc
    render_ingest_summary(summary, database_path)


@app.command()
def analyze(
    session_id: Annotated[
        str | None,
        typer.Argument(help="Ingested session ID to analyze."),
    ] = None,
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
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Analyze all matching stale or missing sessions."),
    ] = False,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            help="Only include sessions whose project_path or cwd is under this path.",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Only include sessions from this agent, for example codex, claude, or pi.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Reanalyze already-current matching sessions."),
    ] = False,
    write_artifacts: Annotated[
        bool,
        typer.Option(
            "--write-artifacts",
            help="Write normal per-session artifacts during batch analysis.",
        ),
    ] = False,
) -> None:
    """Analyze one session or restore analysis coverage in bulk."""
    require_analysis_output_format(output_format)
    require_analysis_mode(
        session_id=session_id,
        all_sessions=all_sessions,
        artifact=artifact,
        no_artifact=no_artifact,
        project=project,
        agent=agent,
        force=force,
        write_artifacts=write_artifacts,
    )
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)

    store = DuckDBStore(database_path)
    if all_sessions:
        filters = scope_filters_from_options(agent, project)
        batch_result = analyze_all_sessions(
            store,
            database_path,
            filters,
            force=force,
            write_artifacts=write_artifacts,
        )
        if output_format == "json":
            typer.echo(json.dumps(batch_analysis_payload(batch_result), indent=2, sort_keys=True))
        else:
            render_batch_analysis(batch_result, console)
        if batch_result.failures:
            raise typer.Exit(1)
        return

    assert session_id is not None
    try:
        result = analyze_session(store, session_id, database_path, artifact, no_artifact)
    except SessionNotLoadableError as exc:
        if exc.not_found:
            console.print(f"[red]Session not found:[/red] {session_id}")
        else:
            console.print("[red]Session could not be loaded.[/red]")
        raise typer.Exit(1) from exc
    except AnalysisArtifactError as exc:
        console.print("[red]Could not write analysis artifact.[/red]")
        raise typer.Exit(1) from exc
    except AnalysisPersistenceError as exc:
        console.print("[red]Could not persist analysis results.[/red]")
        raise typer.Exit(1) from exc
    except AnalysisWorkflowError as exc:
        console.print(f"[red]{exc.safe_message}.[/red]")
        raise typer.Exit(1) from exc

    if output_format == "json":
        typer.echo(json.dumps(result.payload, indent=2, sort_keys=True, default=str))
        return

    render_analysis_summary(
        session_id,
        result.analysis_run,
        result.session_features,
        result.classifications,
    )


def require_analysis_mode(
    *,
    session_id: str | None,
    all_sessions: bool,
    artifact: Path | None,
    no_artifact: bool,
    project: Path | None,
    agent: str | None,
    force: bool,
    write_artifacts: bool,
) -> None:
    if (session_id is None and not all_sessions) or (session_id is not None and all_sessions):
        console.print("[red]Choose exactly one:[/red] SESSION_ID or --all")
        raise typer.Exit(2)
    if all_sessions and (artifact is not None or no_artifact):
        console.print("[red]Batch mode rejects --artifact and --no-artifact.[/red]")
        raise typer.Exit(2)
    if not all_sessions and (project is not None or agent is not None or force or write_artifacts):
        console.print(
            "[red]Single-session mode rejects --project, --agent, --force, "
            "and --write-artifacts.[/red]"
        )
        raise typer.Exit(2)


@app.command()
def summary(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to inspect. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            help="Only include sessions whose project_path or cwd is under this path.",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Only include sessions from this agent, for example codex, claude, or pi.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help="Maximum rows for ranked/detail sections.",
        ),
    ] = 10,
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: terminal or json.",
        ),
    ] = "terminal",
) -> None:
    """Summarize aggregate session and analysis data in DuckDB."""
    require_summary_output_format(output_format)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)

    filters = summary_filters_from_options(agent, project, limit)
    aggregate = DuckDBStore(database_path).aggregate_summary(filters)

    if output_format == "json":
        typer.echo(json.dumps(summary_payload(aggregate), indent=2, sort_keys=True, default=str))
        return

    render_summary(aggregate, database_path, console)


@app.command()
def trends(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to inspect. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
    project: Annotated[
        Path | None,
        typer.Option(
            "--project",
            help="Only include sessions whose project_path or cwd is under this path.",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Only include sessions from this agent, for example codex, claude, or pi.",
        ),
    ] = None,
    bucket: Annotated[
        str,
        typer.Option("--bucket", help="Calendar bucket size: week or month."),
    ] = "week",
    periods: Annotated[
        int,
        typer.Option("--periods", help="Number of aligned buckets, from 1 to 120."),
    ] = 12,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum rows for ranked/detail sections."),
    ] = 10,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: terminal or json."),
    ] = "terminal",
) -> None:
    """Show deterministic project-level session trends."""
    require_summary_output_format(output_format)
    filters = trend_filters_from_options(agent, project, bucket, periods, limit)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    report = DuckDBStore(database_path).trends(filters)

    if output_format == "json":
        typer.echo(json.dumps(trend_payload(report), indent=2, sort_keys=True))
        return
    render_trends(report, database_path, console)


@projects_app.command("list")
def projects_list(
    db: Annotated[
        Path | None,
        typer.Option(
            "--db",
            help="DuckDB path to inspect. Defaults to SESSION_DOCTOR_DB or app data.",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Only include sessions from this agent, for example codex, claude, or pi.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum observed project rows."),
    ] = 10,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: terminal or json."),
    ] = "terminal",
) -> None:
    """List exact observed project_path/CWD hints."""
    require_summary_output_format(output_format)
    filters = project_filters_from_options(agent, limit)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    report = DuckDBStore(database_path).projects(filters)
    if output_format == "json":
        typer.echo(json.dumps(project_payload(report), indent=2, sort_keys=True))
        return
    render_project_report(report, console)


@app.command()
def report(
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
        typer.Option("--format", help="Output format: terminal, markdown, or json."),
    ] = "terminal",
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum rows per bounded evidence section."),
    ] = 10,
    show_text: Annotated[
        bool,
        typer.Option("--show-text", help="Include text for displayed evidence messages only."),
    ] = False,
) -> None:
    """Generate a privacy-safe exact-session diagnostic report."""
    require_report_output_format(output_format)
    require_positive_limit(limit)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    snapshot = DuckDBStore(database_path).load_diagnostic_snapshot(session_id)
    if snapshot is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)
    payload = build_session_report(snapshot, limit=limit, show_text=show_text)
    if output_format == "json":
        typer.echo(payload.model_dump_json(indent=2))
        return
    if output_format == "markdown":
        typer.echo(render_session_report_markdown(payload), nl=False)
        return
    render_session_report(payload, console)


@app.command()
def graph(
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
        typer.Option("--format", help="Output format: json."),
    ] = "json",
) -> None:
    """Project a complete conservative exact-session evidence graph."""
    require_graph_output_format(output_format)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    snapshot = DuckDBStore(database_path).load_diagnostic_snapshot(session_id)
    if snapshot is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)
    typer.echo(json.dumps(graph_payload(project_graph(snapshot)), indent=2, sort_keys=True))


app.add_typer(adapters_app, name="adapters")
app.add_typer(db_app, name="db")
app.add_typer(sessions_app, name="sessions")
app.add_typer(projects_app, name="projects")


def render_ingest_summary(summary: IngestSummary, database_path: Path) -> None:
    _render_ingest_summary(summary, database_path, console)


def render_analysis_summary(
    session_id: str,
    analysis_run: AnalysisRun,
    session_features: list[SessionFeature],
    classifications: list[SessionClassification],
) -> None:
    _render_analysis_summary(
        session_id,
        analysis_run,
        session_features,
        classifications,
        console,
    )
