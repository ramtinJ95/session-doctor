from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .adapters import BaseAdapter, built_in_adapters
from .adapters.codex import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
)
from .analysis import analyze_features, classify_session
from .config import default_database_path, supports_current_python
from .ids import source_id_for_path, stable_id
from .schemas import (
    AnalysisRun,
    MessageFeature,
    Session,
    SessionClassification,
    SessionFeature,
    SourceKind,
)
from .schemas.common import AgentName
from .schemas.sessions import SessionSource
from .store import TABLE_NAMES, DuckDBStore

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")
db_app = typer.Typer(help="Manage the local DuckDB store.")
sessions_app = typer.Typer(help="Inspect ingested sessions.")


@dataclass
class IngestSummary:
    agent_display_name: str = ""
    source_count: int = 0
    skipped_source_count: int = 0
    session_count: int = 0
    message_count: int = 0
    response_item_message_count: int = 0
    event_msg_fallback_count: int = 0
    tool_call_count: int = 0
    tool_result_count: int = 0
    command_count: int = 0
    file_activity_count: int = 0
    model_usage_count: int = 0
    warning_count: int = 0


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
    summaries = store.list_session_summaries()

    table = Table(title="Sessions")
    table.add_column("Session ID")
    table.add_column("Agent")
    table.add_column("Started")
    table.add_column("Messages")
    table.add_column("Commands")
    table.add_column("Warnings")
    table.add_column("Source Path")

    for summary in summaries:
        table.add_row(
            summary.session_id,
            summary.agent_name,
            summary.started_at or "",
            str(summary.message_count),
            str(summary.command_count),
            str(summary.warning_count),
            summary.source_path or "",
        )

    console.print(table)
    for summary in summaries:
        if summary.source_path:
            typer.echo(f"Source path: {summary.source_path}")


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
    summary = IngestSummary(agent_display_name=adapter.display_name, source_count=len(sources))

    for session_source in sources:
        try:
            bundle = adapter.parse_source(session_source)
            store.insert_parsed_bundle(session_source, bundle)
        except Exception as exc:
            summary.skipped_source_count += 1
            console.print(f"[yellow]Skipped source:[/yellow] {session_source.source_path} ({exc})")
            continue

        summary.session_count += 1 if bundle.session else 0
        summary.message_count += len(bundle.messages)
        summary.tool_call_count += len(bundle.tool_calls)
        summary.tool_result_count += len(bundle.tool_results)
        summary.command_count += len(bundle.command_runs)
        summary.file_activity_count += len(bundle.file_activities)
        summary.model_usage_count += len(bundle.model_usage)
        summary.warning_count += len(bundle.parse_warnings)
        source_counts = (
            bundle.session.metadata.get("codex_message_source_counts", {}) if bundle.session else {}
        )
        if isinstance(source_counts, dict):
            summary.response_item_message_count += int(
                source_counts.get(CODEX_MESSAGE_SOURCE_RESPONSE_ITEM, 0)
            )
            summary.event_msg_fallback_count += int(
                source_counts.get(CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK, 0)
            )

    render_ingest_summary(summary, database_path)


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
    if output_format not in {"terminal", "json"}:
        console.print("[red]Invalid --format:[/red] expected terminal or json")
        raise typer.Exit(2)

    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    if not database_path.exists():
        console.print(f"[red]Database does not exist:[/red] {database_path}")
        raise typer.Exit(1)

    store = DuckDBStore(database_path)
    bundle = store.load_session_bundle(session_id)
    if bundle is None or bundle.session is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)

    started_at = datetime.now(UTC)
    analysis_run_id = stable_id("analysis_run", session_id, started_at.isoformat())
    extracted_features = analyze_features(bundle, analysis_run_id)
    classifications = classify_session(
        bundle,
        analysis_run_id,
        extracted_features.message_features,
        extracted_features.session_features,
    )
    artifact_path = artifact_path_for_analysis(database_path, session_id, artifact, no_artifact)
    analysis_run = AnalysisRun(
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        analyzer_version="phase5",
        artifact_path=str(artifact_path) if artifact_path else None,
    )
    payload = analysis_payload(
        bundle.session,
        analysis_run,
        extracted_features.message_features,
        extracted_features.session_features,
        classifications,
    )

    if artifact_path:
        write_analysis_artifact(artifact_path, payload)

    store.replace_analysis_rows(
        analysis_run,
        extracted_features.message_features,
        extracted_features.session_features,
        classifications,
    )

    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    render_analysis_summary(
        session_id,
        analysis_run,
        extracted_features.session_features,
        classifications,
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


def adapter_for_ingest(agent: str) -> BaseAdapter:
    adapters_by_name = {adapter.name.value: adapter for adapter in built_in_adapters()}
    adapter = adapters_by_name.get(agent)
    if adapter is None:
        console.print(f"[red]Unsupported --agent:[/red] {agent}")
        raise typer.Exit(2)
    if adapter.name not in {AgentName.CODEX, AgentName.PI}:
        console.print(f"[red]--agent {agent} is discovered but parsing is not implemented.[/red]")
        raise typer.Exit(2)
    return adapter


def sources_for_ingest(adapter: BaseAdapter, source: Path | None) -> list[SessionSource]:
    if source is None:
        return adapter.discover()

    expanded_source = source.expanduser()
    if not expanded_source.exists():
        console.print(f"[red]Source does not exist:[/red] {expanded_source}")
        raise typer.Exit(1)

    resolved_source = expanded_source.resolve()
    if resolved_source.is_dir():
        return adapter.discover(resolved_source)
    if resolved_source.is_file():
        return [
            SessionSource(
                source_id=source_id_for_path(adapter.name, resolved_source),
                agent_name=adapter.name,
                source_path=str(resolved_source),
                source_kind=SourceKind.ROOT_SESSION,
            )
        ]

    console.print(f"[red]Source is not a file or directory:[/red] {resolved_source}")
    raise typer.Exit(1)


def render_ingest_summary(summary: IngestSummary, database_path: Path) -> None:
    table = Table(title=f"{summary.agent_display_name} ingest")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Database", str(database_path))
    table.add_row("Sources", str(summary.source_count))
    table.add_row("Skipped sources", str(summary.skipped_source_count))
    table.add_row("Sessions", str(summary.session_count))
    table.add_row("Messages", str(summary.message_count))
    if summary.response_item_message_count or summary.event_msg_fallback_count:
        table.add_row("Response item messages", str(summary.response_item_message_count))
        table.add_row("Event message fallbacks", str(summary.event_msg_fallback_count))
    table.add_row("Tool calls", str(summary.tool_call_count))
    table.add_row("Tool results", str(summary.tool_result_count))
    table.add_row("Commands", str(summary.command_count))
    table.add_row("File activities", str(summary.file_activity_count))
    table.add_row("Model usage rows", str(summary.model_usage_count))
    table.add_row("Warnings", str(summary.warning_count))
    console.print(table)

    if summary.skipped_source_count:
        raise typer.Exit(1)


def artifact_path_for_analysis(
    database_path: Path,
    session_id: str,
    artifact: Path | None,
    no_artifact: bool,
) -> Path | None:
    if no_artifact:
        return None
    if artifact is not None:
        return artifact.expanduser()
    return database_path.parent / "artifacts" / f"{session_id}-analysis.json"


def write_analysis_artifact(path: Path, payload: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        console.print(f"[red]Could not write artifact:[/red] {path} ({exc})")
        raise typer.Exit(1) from exc


def analysis_payload(
    session: Session,
    analysis_run: AnalysisRun,
    message_features: list[MessageFeature],
    session_features: list[SessionFeature],
    classifications: list[SessionClassification],
) -> dict[str, object]:
    return {
        "session": session.model_dump(mode="json"),
        "analysis_run": analysis_run.model_dump(mode="json"),
        "summary_metrics": {
            feature.feature_name: feature.feature_value for feature in session_features
        },
        "message_features": [feature.model_dump(mode="json") for feature in message_features],
        "session_features": [feature.model_dump(mode="json") for feature in session_features],
        "classifications": [
            classification.model_dump(mode="json") for classification in classifications
        ],
    }


def render_analysis_summary(
    session_id: str,
    analysis_run: AnalysisRun,
    session_features: list[SessionFeature],
    classifications: list[SessionClassification],
) -> None:
    summary_table = Table(title="Session analysis")
    summary_table.add_column("Metric")
    summary_table.add_column("Value")
    summary_table.add_row("Session ID", session_id)
    summary_table.add_row("Analysis run", analysis_run.analysis_run_id)
    summary_table.add_row("Artifact", analysis_run.artifact_path or "")
    for feature_name in (
        "repeat_request_count",
        "correction_count",
        "frustration_count",
        "scope_boundary_count",
        "failed_command_ratio",
        "repeated_failure_count",
        "repeated_command_failure_count",
        "same_file_edited_repeatedly_count",
        "unresolved_ending_signal",
    ):
        feature = next(
            (candidate for candidate in session_features if candidate.feature_name == feature_name),
            None,
        )
        if feature:
            summary_table.add_row(feature_name, feature.feature_value)
    console.print(summary_table)

    classification_table = Table(title="Classifications")
    classification_table.add_column("Label")
    classification_table.add_column("Score")
    classification_table.add_column("Confidence")
    classification_table.add_column("Evidence")
    for classification in classifications:
        classification_table.add_row(
            classification.label,
            f"{classification.score:.2f}",
            f"{classification.confidence:.2f}",
            classification.evidence_summary,
        )
    console.print(classification_table)
