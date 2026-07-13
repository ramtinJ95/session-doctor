from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from . import __version__
from .adapters import RecoverableSourceError, built_in_adapters
from .cli_options import (
    adapter_for_ingest,
    agent_name_from_option,
    database_info_for_path,
    database_path_from_option,
    database_path_is_valid,
    os_access_writable,
    path_can_be_created,
    require_analysis_output_format,
    require_current_database_schema,
    require_existing_database_path,
    require_valid_database_path,
    source_selection_for_ingest,
    sources_for_ingest,
)
from .cli_renderers import (
    render_adapters_table,
    render_database_info,
    render_doctor_table,
    render_sessions_table,
    scan_adapter_summary,
)
from .cli_renderers import (
    render_ingest_summary as _render_ingest_summary,
)
from .config import supports_current_python
from .episode_workflow import EpisodeAnalysisUnavailable, analyze_session_episodes
from .evaluation_models import JudgeAnnotation
from .evaluation_packets import (
    boundary_pilot_corpus_bytes,
    discard_staged_packet_exports,
    export_boundary_packets,
    export_boundary_pilot,
    publish_staged_packet_exports,
    stage_packet_exports,
)
from .ids import stable_id
from .ingest_workflow import IngestSummary, ingest_sources
from .integration_assets import IntegrationAssetError, session_doctor_skill_directory
from .normalization_workflow import normalize_snapshot
from .schemas import AgentName, SessionSource
from .store import (
    TABLE_NAMES,
    DuckDBStore,
    EvaluationImportError,
    LoadedBundleMember,
    NormalizationConflictError,
    SnapshotPruneBlocked,
    SnapshotSummary,
    import_judge_annotation,
    register_boundary_pilot,
    register_evaluation_corpus,
    registered_corpus_bundle_id,
)

console = Console()

app = typer.Typer(help="Inspect and diagnose local AI agent sessions.")
adapters_app = typer.Typer(help="Inspect built-in session adapters.")
db_app = typer.Typer(help="Manage the local DuckDB store.")
sessions_app = typer.Typer(help="Inspect ingested sessions.")
projects_app = typer.Typer(help="Inspect observed project path hints.")
integrations_app = typer.Typer(help="Locate optional agent integration assets.")
snapshots_app = typer.Typer(help="Inspect and prune exact captured source history.")
normalizations_app = typer.Typer(help="Replay and inspect versioned normalization.")
evaluation_app = typer.Typer(help="Export blinded packets and import offline judgments.")

__all__ = [
    "IngestSummary",
    "adapter_for_ingest",
    "app",
    "database_path_from_option",
    "database_path_is_valid",
    "os_access_writable",
    "path_can_be_created",
    "require_current_database_schema",
    "require_valid_database_path",
    "render_ingest_summary",
    "scan_adapter_summary",
    "sources_for_ingest",
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
            capability_declarations=adapter.capabilities,
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


@evaluation_app.command("export-boundaries")
def evaluation_export_boundaries(
    normalization_run_id: str,
    output: Annotated[Path, typer.Option("--output", help="New packet directory.")],
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to inspect.")] = None,
) -> None:
    """Export deterministic blinded boundary packets without provider calls."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    output = output.expanduser()
    if output.exists() or not output.parent.is_dir():
        console.print("[red]Evaluation export failed:[/red] output must be a new directory")
        raise typer.Exit(1)
    store = DuckDBStore(database_path)
    stored = store.load_normalization(normalization_run_id)
    foundation = store.load_semantic_foundation(normalization_run_id)
    if stored is None or foundation is None:
        console.print(f"[red]Normalization not found:[/red] {normalization_run_id}")
        raise typer.Exit(1)
    exports = export_boundary_packets(stored, foundation)
    staged_output: Path | None = None
    try:
        staged_output = stage_packet_exports(exports, output)
        register_evaluation_corpus(database_path, normalization_run_id, exports)
        publish_staged_packet_exports(staged_output, output)
        staged_output = None
    except (OSError, ValueError, EvaluationImportError) as exc:
        if staged_output is not None:
            discard_staged_packet_exports(staged_output)
        console.print(f"[red]Evaluation export failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Exported {len(exports)} boundary packets: {output}")


@evaluation_app.command("export-episodes")
def evaluation_export_episodes() -> None:
    """Reject episode export until adjudicated boundaries are frozen after PR 8."""
    console.print(
        "[red]Episode packet generation is unavailable until boundary references "
        "are frozen after PR 8.[/red]"
    )
    raise typer.Exit(1)


@evaluation_app.command("export-pilot")
def evaluation_export_pilot(
    output: Annotated[Path, typer.Option("--output", help="New judge-packet directory.")],
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to modify.")] = None,
) -> None:
    """Register and export the checked blinded boundary pilot without provider calls."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    output = output.expanduser()
    if output.exists() or not output.parent.is_dir():
        console.print("[red]Evaluation export failed:[/red] output must be a new directory")
        raise typer.Exit(1)
    staged_output: Path | None = None
    try:
        corpus_bytes = boundary_pilot_corpus_bytes()
        store = DuckDBStore(database_path)
        bundle_id = registered_corpus_bundle_id(database_path, "boundary-pilot-v1")
        if bundle_id is None:
            source = SessionSource(
                source_id=stable_id(
                    "evaluation-pilot-source", hashlib.sha256(corpus_bytes).hexdigest()
                ),
                agent_name=AgentName.PI,
                source_path="evaluation-corpus://boundary-pilot-v1",
            )
            captured = store.capture_source(source, corpus_bytes)
            bundle = store.create_single_source_bundle(source, captured, "boundary-pilot-v1")
            store.record_lifecycle(bundle.snapshot_bundle_id, terminal_observed=True)
            bundle_id = bundle.snapshot_bundle_id
        exports = export_boundary_pilot(corpus_bytes, bundle_id)
        staged_output = stage_packet_exports(exports, output)
        register_boundary_pilot(database_path, corpus_bytes, bundle_id, exports)
        publish_staged_packet_exports(staged_output, output)
        staged_output = None
    except (OSError, KeyError, ValueError, EvaluationImportError) as exc:
        if staged_output is not None:
            discard_staged_packet_exports(staged_output)
        console.print(f"[red]Evaluation export failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Exported {len(exports)} registered pilot packets: {output}")


@evaluation_app.command("import-judge")
def evaluation_import_judge(
    input_path: Annotated[Path, typer.Option("--input", help="Judge annotation JSON.")],
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to modify.")] = None,
) -> None:
    """Import one schema-validated judgment produced outside Session Doctor."""
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_current_database_schema(database_path)
    try:
        annotation = JudgeAnnotation.model_validate_json(input_path.read_text())
        import_judge_annotation(database_path, annotation)
    except (OSError, ValueError, EvaluationImportError) as exc:
        console.print(f"[red]Judge import failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"Imported judge annotation: {annotation.judge_annotation_id}")


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
                        "semantic_analysis_runs": dependencies.analysis_run_ids,
                        "normalization_runs": dependencies.normalization_run_ids,
                        "evaluation_packets": dependencies.evaluation_packet_ids,
                        "evaluation_corpora": dependencies.evaluation_corpus_ids,
                        "partial_evaluation_corpora": dependencies.partial_evaluation_corpus_ids,
                        "audit_protocols": dependencies.audit_protocol_ids,
                        "partial_audit_protocols": dependencies.partial_audit_protocol_ids,
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
    session_id: Annotated[str, typer.Argument(help="Normalized session ID to segment.")],
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path to inspect.")] = None,
    output_format: Annotated[
        str, typer.Option("--format", help="Output format: terminal or json.")
    ] = "terminal",
) -> None:
    """Produce deterministic task episodes, boundaries, lifecycle, and observations."""
    require_analysis_output_format(output_format)
    database_path = database_path_from_option(db)
    require_valid_database_path(database_path)
    require_existing_database_path(database_path)
    require_current_database_schema(database_path)
    try:
        analysis = analyze_session_episodes(DuckDBStore(database_path), session_id, database_path)
    except EpisodeAnalysisUnavailable as exc:
        console.print(f"[red]Episode analysis unavailable:[/red] {exc}")
        raise typer.Exit(1) from exc
    payload = analysis.model_dump(mode="json")
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    console.print(
        f"[bold]Episode analysis[/bold] {session_id}: "
        f"{len(analysis.episodes)} episodes, {len(analysis.boundaries)} boundaries, "
        f"lifecycle={analysis.lifecycle_state}"
    )
    for episode in analysis.episodes:
        console.print(
            f"- {episode.episode_id} users={len(episode.user_anchor_ids)} "
            f"provisional={str(episode.provisional).lower()}"
        )
    for boundary in analysis.boundaries:
        console.print(
            f"- boundary {boundary.boundary_id} decision={boundary.decision.value} "
            f"reason={boundary.reason.value}"
        )
    for observation in analysis.observations:
        console.print(
            f"- observation {observation.observation_id} kind={observation.observation_kind}"
        )


UNAVAILABLE_REBUILD_MESSAGE = (
    "{command} is unavailable during the deterministic analysis v2 rebuild; "
    "see docs/deterministic-analysis-v2-plan.md."
)


def unavailable_during_v2_rebuild(command: str) -> NoReturn:
    typer.echo(UNAVAILABLE_REBUILD_MESSAGE.format(command=command))
    raise typer.Exit(1)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def summary(_context: typer.Context) -> None:
    """Fail explicitly while the v2 summary projection is rebuilt."""
    unavailable_during_v2_rebuild("summary")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def trends(_context: typer.Context) -> None:
    """Fail explicitly while the v2 trends projection is rebuilt."""
    unavailable_during_v2_rebuild("trends")


@projects_app.command(
    "list", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def projects_list(_context: typer.Context) -> None:
    """Fail explicitly while the v2 project projection is rebuilt."""
    unavailable_during_v2_rebuild("projects list")


@integrations_app.command("path")
def integration_path() -> None:
    """Print the bundled session-doctor Agent Skill directory."""
    try:
        skill_directory = session_doctor_skill_directory()
    except IntegrationAssetError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    typer.echo(skill_directory)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def report(_context: typer.Context) -> None:
    """Fail explicitly while the v2 report projection is rebuilt."""
    unavailable_during_v2_rebuild("report")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def graph(_context: typer.Context) -> None:
    """Fail explicitly while the v2 graph projection is rebuilt."""
    unavailable_during_v2_rebuild("graph")


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
app.add_typer(evaluation_app, name="evaluation")


def render_ingest_summary(summary: IngestSummary, database_path: Path) -> None:
    _render_ingest_summary(summary, database_path, console)
