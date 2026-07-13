from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
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
    SessionAgentMismatchError,
    SessionNotLoadableError,
    analyze_session,
)
from .artifacts import analysis_payload, artifact_path_for_analysis, write_analysis_artifact
from .batch_analysis import analyze_all_sessions, batch_analysis_payload
from .cli_options import (
    adapter_for_ingest,
    agent_name_from_option,
    database_info_for_path,
    database_path_from_option,
    database_path_is_valid,
    html_output_path_from_options,
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
    require_trend_output_format,
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
from .html import (
    HtmlRenderError,
    HtmlWriteError,
    render_report_html,
    render_trends_html,
    write_html,
)
from .ingest_workflow import IngestSummary, ingest_sources
from .integration_assets import IntegrationAssetError, session_doctor_skill_directory
from .normalization_workflow import normalize_snapshot
from .report_payload import build_session_report
from .report_renderers import render_session_report, render_session_report_markdown
from .schemas import AnalysisRun, SessionClassification, SessionFeature
from .store import (
    TABLE_NAMES,
    DuckDBStore,
    LoadedBundleMember,
    NormalizationConflictError,
    SnapshotPruneBlocked,
    SnapshotSummary,
)
from .summary_payload import summary_payload
from .trend_payload import project_payload, trend_payload

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")
db_app = typer.Typer(help="Manage the local DuckDB store.")
sessions_app = typer.Typer(help="Inspect ingested sessions.")
projects_app = typer.Typer(help="Inspect observed project path hints.")
integrations_app = typer.Typer(help="Locate optional agent integration assets.")
snapshots_app = typer.Typer(help="Inspect and prune exact captured source history.")
normalizations_app = typer.Typer(help="Replay and inspect versioned normalization.")

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


@snapshots_app.command("list")
def snapshots_list(
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to inspect.")] = None,
    agent: Annotated[str | None, typer.Option("--agent")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    output_format: Annotated[str, typer.Option("--format", help="terminal or json")] = "terminal",
) -> None:
    """List latest and historical exact source snapshots."""
    if output_format not in {"terminal", "json"}:
        raise typer.BadParameter("format must be terminal or json", param_hint="--format")
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    try:
        rows = DuckDBStore(database_path).list_snapshots(
            agent_name=agent_name_from_option(agent),
            lifecycle_state=status,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--status") from exc
    payload = [snapshot_summary_payload(row) for row in rows]
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return
    for row in payload:
        latest = " latest" if row["is_latest"] else ""
        typer.echo(
            f"{row['snapshot_id']} {row['agent_name']} {row['lifecycle_state']}"
            f"{latest} {row['source_path']}"
        )


@normalizations_app.command("replay")
def normalization_replay(
    snapshot_id: str,
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to modify.")] = None,
) -> None:
    """Explicitly add the current parser output for one historical snapshot."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    store = DuckDBStore(database_path)
    summary = store.snapshot_summary(snapshot_id)
    if summary is None:
        console.print(f"[red]Snapshot not found:[/red] {snapshot_id}")
        raise typer.Exit(1)
    adapter = adapter_for_ingest(summary.agent_name)
    try:
        run = normalize_snapshot(adapter, store, snapshot_id)
    except (ValueError, RecoverableSourceError, NormalizationConflictError) as exc:
        console.print(f"[red]Normalization failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    typer.echo(
        json.dumps(
            {
                "normalization_run_id": run.normalization_run_id,
                "snapshot_bundle_id": run.snapshot_bundle_id,
                "adapter_name": run.adapter_name,
                "adapter_version": run.adapter_version,
                "normalization_version": run.normalization_version,
                "configuration_hash": run.configuration_hash,
            },
            indent=2,
            sort_keys=True,
        )
    )


@normalizations_app.command("status")
def normalization_status(
    snapshot_id: str,
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to inspect.")] = None,
) -> None:
    """Report current, stale, or missing parser coverage for one snapshot."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    store = DuckDBStore(database_path)
    summary = store.snapshot_summary(snapshot_id)
    if summary is None:
        console.print(f"[red]Snapshot not found:[/red] {snapshot_id}")
        raise typer.Exit(1)
    if summary.snapshot_bundle_id is None:
        status_payload = {
            "snapshot_id": snapshot_id,
            "snapshot_bundle_id": None,
            "status": "missing",
            "current_normalization_run_id": None,
            "selected_normalization_run_id": None,
            "available_normalization_run_ids": [],
        }
    else:
        adapter = adapter_for_ingest(summary.agent_name)
        coverage = store.normalization_coverage(
            summary.snapshot_bundle_id,
            adapter_name=adapter.name.value,
            adapter_version=adapter.version,
        )
        status_payload = {
            "snapshot_id": snapshot_id,
            "snapshot_bundle_id": coverage.snapshot_bundle_id,
            "status": coverage.status,
            "current_normalization_run_id": coverage.current_normalization_run_id,
            "selected_normalization_run_id": coverage.selected_normalization_run_id,
            "available_normalization_run_ids": coverage.available_normalization_run_ids,
        }
    typer.echo(json.dumps(status_payload, indent=2, sort_keys=True))


@snapshots_app.command("show")
def snapshots_show(
    snapshot_id: str,
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to inspect.")] = None,
) -> None:
    """Show one exact snapshot's provenance and lifecycle."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    row = DuckDBStore(database_path).snapshot_summary(snapshot_id)
    if row is None:
        console.print(f"[red]Snapshot not found:[/red] {snapshot_id}")
        raise typer.Exit(1)
    payload = snapshot_summary_payload(row)
    payload["members"] = (
        [
            bundle_member_payload(member)
            for member in DuckDBStore(database_path).load_bundle_members(row.snapshot_bundle_id)
        ]
        if row.snapshot_bundle_id is not None
        else []
    )
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


@snapshots_app.command("replay")
def snapshots_replay(
    snapshot_id: str,
    output: Annotated[Path, typer.Option("--output", help="Exact raw-byte destination.")],
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to inspect.")] = None,
    bundle_mode: Annotated[
        bool,
        typer.Option("--bundle", help="Export the complete ordered bundle to a new directory."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing single-file output."),
    ] = False,
) -> None:
    """Atomically write one exact captured source to an explicit path."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    store = DuckDBStore(database_path)
    summary = store.snapshot_summary(snapshot_id)
    source_bytes = store.load_snapshot_bytes(snapshot_id)
    if source_bytes is None or summary is None:
        console.print(f"[red]Snapshot not found:[/red] {snapshot_id}")
        raise typer.Exit(1)
    destination = output.expanduser()
    if not destination.parent.is_dir():
        raise typer.BadParameter("output parent directory must exist", param_hint="--output")
    if bundle_mode:
        if summary.snapshot_bundle_id is None:
            raise typer.BadParameter(
                "unbundled snapshots support single-file replay only",
                param_hint="--bundle",
            )
        if destination.exists():
            raise typer.BadParameter(
                "bundle output directory must not already exist",
                param_hint="--output",
            )
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
        )
        try:
            members = store.load_bundle_members(summary.snapshot_bundle_id)
            manifest = [bundle_member_payload(member) for member in members]
            for member in members:
                if member.source_bytes is None:
                    continue
                filename = (
                    f"{member.capture_order:03d}-{member.member_role}-"
                    f"{Path(member.source_path).name}"
                )
                (temporary_directory / filename).write_bytes(member.source_bytes)
            (temporary_directory / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
            )
            os.replace(temporary_directory, destination)
        except Exception:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise
        typer.echo(f"Wrote exact snapshot bundle: {destination}")
        return
    if destination.exists() and not overwrite:
        raise typer.BadParameter(
            "output already exists; pass --overwrite to replace it",
            param_hint="--output",
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(source_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    typer.echo(f"Wrote exact snapshot: {destination}")


@snapshots_app.command("prune")
def snapshots_prune(
    snapshot_id: str,
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to modify.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Delete dependent derived rows.")] = False,
) -> None:
    """Explicitly prune one snapshot and checkpoint DuckDB."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    try:
        if force:
            dependencies = DuckDBStore(database_path).snapshot_dependencies(snapshot_id)
            typer.echo("Force prune dependencies:")
            typer.echo(
                json.dumps(
                    {
                        "bundles": dependencies.bundle_ids,
                        "sources": dependencies.source_ids,
                        "sessions": dependencies.session_ids,
                        "analysis_runs": dependencies.analysis_run_ids,
                        "normalization_runs": dependencies.normalization_run_ids,
                        "inbound_source_references": dependencies.inbound_source_ids,
                        "inbound_session_references": dependencies.inbound_session_ids,
                        "downstream_lifecycle_bundles": (
                            dependencies.downstream_lifecycle_bundle_ids
                        ),
                        "derived_rows": dependencies.derived_row_counts,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        result = DuckDBStore(database_path).prune_snapshot(snapshot_id, force=force)
    except SnapshotPruneBlocked as exc:
        console.print(f"[red]Snapshot prune blocked:[/red] {exc}")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    typer.echo(
        f"Pruned {result.snapshot_id}: bundles={result.deleted_bundle_count} "
        f"blobs={result.deleted_blob_count} forced={str(result.forced).lower()} "
        f"checkpoint={str(result.checkpoint_completed).lower()}"
    )


def snapshot_summary_payload(row: SnapshotSummary) -> dict[str, object]:
    return {
        "snapshot_id": row.snapshot_id,
        "snapshot_bundle_id": row.snapshot_bundle_id,
        "source_id": row.source_id,
        "agent_name": row.agent_name,
        "source_path": row.source_path,
        "capture_sequence": row.capture_sequence,
        "captured_at": row.captured_at,
        "lifecycle_state": row.lifecycle_state,
        "capture_status": row.capture_status,
        "byte_length": row.byte_length,
        "is_latest": row.is_latest,
    }


def bundle_member_payload(member: LoadedBundleMember) -> dict[str, object]:
    return {
        "capture_order": member.capture_order,
        "source_id": member.source_id,
        "source_path": member.source_path,
        "member_role": member.member_role,
        "member_capture_status": member.member_capture_status,
        "capture_started_at": member.capture_started_at,
        "capture_completed_at": member.capture_completed_at,
        "byte_length": len(member.source_bytes) if member.source_bytes is not None else None,
    }


@sessions_app.command("list")
def list_sessions(
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
            help="Only include sessions from this agent: codex, claude, or pi.",
        ),
    ] = None,
) -> None:
    """List sessions stored in DuckDB."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    agent_name = agent_name_from_option(agent)
    store = DuckDBStore(database_path)
    render_sessions_table(store.list_session_summaries(agent_name), console)


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
            help=("Require this agent for one session, or filter --all: codex, claude, or pi."),
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
    expected_agent_name = agent_name_from_option(agent)
    try:
        result = analyze_session(
            store,
            session_id,
            database_path,
            artifact,
            no_artifact,
            expected_agent_name=expected_agent_name,
        )
    except SessionAgentMismatchError as exc:
        render_agent_mismatch(exc.actual_agent, exc.expected_agent)
        raise typer.Exit(1) from exc
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
    force: bool,
    write_artifacts: bool,
) -> None:
    if (session_id is None and not all_sessions) or (session_id is not None and all_sessions):
        console.print("[red]Choose exactly one:[/red] SESSION_ID or --all")
        raise typer.Exit(2)
    if all_sessions and (artifact is not None or no_artifact):
        console.print("[red]Batch mode rejects --artifact and --no-artifact.[/red]")
        raise typer.Exit(2)
    if not all_sessions and (project is not None or force or write_artifacts):
        console.print(
            "[red]Single-session mode rejects --project, --force, and --write-artifacts.[/red]"
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
        typer.Option("--format", help="Output format: terminal, json, or html."),
    ] = "terminal",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="HTML destination to replace atomically. Required only for --format html.",
        ),
    ] = None,
) -> None:
    """Show deterministic project-level session trends."""
    require_trend_output_format(output_format)
    html_output = html_output_path_from_options(output_format, output)
    filters = trend_filters_from_options(agent, project, bucket, periods, limit)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    report = DuckDBStore(database_path).trends(filters)

    if output_format == "html":
        assert html_output is not None
        try:
            html_document = render_trends_html(report)
            write_html(html_output, html_document)
        except (HtmlRenderError, HtmlWriteError):
            console.print("[red]Could not write HTML trends dashboard.[/red]")
            raise typer.Exit(1) from None
        typer.echo(f"Wrote HTML trends dashboard: {html_output}")
        return
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


@integrations_app.command("path")
def integration_path() -> None:
    """Print the bundled session-doctor Agent Skill directory."""
    try:
        skill_directory = session_doctor_skill_directory()
    except IntegrationAssetError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    typer.echo(skill_directory)


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
        typer.Option("--format", help="Output format: terminal, markdown, json, or html."),
    ] = "terminal",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="HTML destination to replace atomically. Required only for --format html.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum rows per bounded evidence section."),
    ] = 10,
    show_text: Annotated[
        bool,
        typer.Option("--show-text", help="Include text for displayed evidence messages only."),
    ] = False,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Require the session to belong to this agent: codex, claude, or pi.",
        ),
    ] = None,
) -> None:
    """Generate a privacy-safe exact-session diagnostic report."""
    require_report_output_format(output_format)
    require_positive_limit(limit)
    html_output = html_output_path_from_options(output_format, output)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    expected_agent_name = agent_name_from_option(agent)
    store = DuckDBStore(database_path)
    require_stored_session_agent(store, session_id, expected_agent_name)
    snapshot = store.load_diagnostic_snapshot(session_id)
    if snapshot is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)
    require_matching_session_agent(
        snapshot.normalized.session.agent_name.value, expected_agent_name
    )
    payload = build_session_report(snapshot, limit=limit, show_text=show_text)
    if output_format == "html":
        assert html_output is not None
        try:
            html_document = render_report_html(payload)
            write_html(html_output, html_document)
        except (HtmlRenderError, HtmlWriteError):
            console.print("[red]Could not write HTML report.[/red]")
            raise typer.Exit(1) from None
        typer.echo(f"Wrote HTML report: {html_output}")
        return
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
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Require the session to belong to this agent: codex, claude, or pi.",
        ),
    ] = None,
) -> None:
    """Project a complete conservative exact-session evidence graph."""
    require_graph_output_format(output_format)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    expected_agent_name = agent_name_from_option(agent)
    store = DuckDBStore(database_path)
    require_stored_session_agent(store, session_id, expected_agent_name)
    snapshot = store.load_diagnostic_snapshot(session_id)
    if snapshot is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)
    require_matching_session_agent(
        snapshot.normalized.session.agent_name.value, expected_agent_name
    )
    typer.echo(json.dumps(graph_payload(project_graph(snapshot)), indent=2, sort_keys=True))


def require_matching_session_agent(actual_agent: str, expected_agent: str | None) -> None:
    if expected_agent is None or actual_agent == expected_agent:
        return
    render_agent_mismatch(actual_agent, expected_agent)
    raise typer.Exit(1)


def require_stored_session_agent(
    store: DuckDBStore,
    session_id: str,
    expected_agent: str | None,
) -> None:
    if expected_agent is None:
        return
    actual_agent = store.session_agent_name(session_id)
    if actual_agent is None:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise typer.Exit(1)
    require_matching_session_agent(actual_agent, expected_agent)


def render_agent_mismatch(actual_agent: str, expected_agent: str) -> None:
    console.print(
        f"[red]Agent mismatch:[/red] session belongs to {actual_agent}, not {expected_agent}."
    )


app.add_typer(adapters_app, name="adapters")
app.add_typer(db_app, name="db")
app.add_typer(sessions_app, name="sessions")
app.add_typer(projects_app, name="projects")
app.add_typer(integrations_app, name="integrations")
app.add_typer(snapshots_app, name="snapshots")
app.add_typer(normalizations_app, name="normalizations")


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
