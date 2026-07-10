from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .adapters import BaseAdapter
from .ingest_workflow import IngestSummary
from .privacy import redact_home
from .schemas import AnalysisRun, SessionClassification, SessionFeature, SourceKind
from .store import TABLE_NAMES
from .store.models import AggregateSummary, SessionSummary, StoreInfo

ANALYSIS_SUMMARY_FEATURES = (
    "friction_score",
    "stuckness_score",
    "prompt_clarity_risk",
    "agent_fit_risk",
    "project_complexity_signal",
    "repeat_request_count",
    "correction_count",
    "frustration_count",
    "scope_boundary_count",
    "ambiguity_count",
    "stop_or_pause_count",
    "failed_command_ratio",
    "repeated_failure_count",
    "repeated_command_failure_count",
    "same_file_edited_repeatedly_count",
    "unresolved_ending_signal",
)


def render_doctor_table(
    checks: list[tuple[str, bool, str]],
    adapters: Sequence[BaseAdapter],
    console: Console,
) -> tuple[bool, bool]:
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

    for adapter in adapters:
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
    return has_errors, has_warnings


def render_adapters_table(adapters: Sequence[BaseAdapter], scan: bool, console: Console) -> None:
    table = Table(title="Built-in adapters")
    table.add_column("Adapter")
    table.add_column("Root")
    table.add_column("Status")
    if scan:
        table.add_column("Candidates")

    for adapter in adapters:
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


def scan_adapter_summary(adapter: BaseAdapter) -> str:
    sources = adapter.discover()
    counts = {source_kind: 0 for source_kind in SourceKind}
    for source in sources:
        counts[source.source_kind] += 1
    populated_counts = [
        f"{source_kind.value}={count}" for source_kind, count in counts.items() if count
    ]
    return ", ".join(populated_counts) if populated_counts else "0"


def render_database_info(info: StoreInfo, console: Console) -> None:
    table = Table(title="DuckDB store")
    table.add_column("Property")
    table.add_column("Value")
    table.add_row("Path", str(info.database_path))
    table.add_row("Exists", "yes" if info.exists else "no")
    table.add_row("Schema version", str(info.schema_version or "unknown"))
    table.add_row("Tables", f"{len(info.tables)}/{len(TABLE_NAMES)}")
    console.print(table)


def render_sessions_table(summaries: Sequence[SessionSummary], console: Console) -> None:
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


def render_ingest_summary(summary: IngestSummary, database_path: Path, console: Console) -> None:
    table = Table(title=f"{summary.agent_display_name} ingest")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Database", str(database_path))
    table.add_row("Sources", str(summary.source_count))
    if summary.discovered_source_counts:
        selected_counts = summary.selected_source_counts or {}
        parsed_counts = summary.parsed_source_counts or {}
        ignored_counts = {
            key: count - selected_counts.get(key, 0)
            for key, count in summary.discovered_source_counts.items()
            if count > selected_counts.get(key, 0)
        }
        table.add_row("Discovered parsed kinds", format_source_counts(parsed_counts))
        table.add_row("Deliberately ignored kinds", format_source_counts(ignored_counts))
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


def format_source_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "0"


def render_analysis_summary(
    session_id: str,
    analysis_run: AnalysisRun,
    session_features: list[SessionFeature],
    classifications: list[SessionClassification],
    console: Console,
) -> None:
    summary_table = Table(title="Session analysis")
    summary_table.add_column("Metric")
    summary_table.add_column("Value")
    summary_table.add_row("Session ID", session_id)
    summary_table.add_row("Analysis run", analysis_run.analysis_run_id)
    summary_table.add_row("Artifact", analysis_run.artifact_path or "")
    for feature_name in ANALYSIS_SUMMARY_FEATURES:
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


def render_summary(summary: AggregateSummary, database_path: Path, console: Console) -> None:
    overview_table = Table(title="Aggregate summary")
    overview_table.add_column("Metric")
    overview_table.add_column("Value")
    overview_table.add_row("Database", redact_home(database_path))
    overview_table.add_row("Agent filter", summary.filters.agent_name or "all")
    overview_table.add_row(
        "Project filter",
        redact_home(summary.filters.project_path) if summary.filters.project_path else "all",
    )
    overview_table.add_row("Limit", str(summary.filters.limit))
    overview_table.add_row("Sessions", str(summary.total_sessions))
    overview_table.add_row("Analyzed", str(summary.analyzed_sessions))
    overview_table.add_row("Unanalyzed", str(summary.unanalyzed_sessions))
    console.print(overview_table)

    agents_table = Table(title="Agents")
    agents_table.add_column("Agent")
    agents_table.add_column("Sessions")
    agents_table.add_column("Analyzed")
    if summary.agent_counts:
        for row in summary.agent_counts:
            agents_table.add_row(
                row.agent_name,
                str(row.session_count),
                str(row.analyzed_session_count),
            )
    else:
        agents_table.add_row("none", "0", "0")
    console.print(agents_table)

    projects_table = Table(title="Projects")
    projects_table.add_column("Project/CWD")
    projects_table.add_column("Sessions")
    projects_table.add_column("Analyzed")
    if summary.project_counts:
        for row in summary.project_counts:
            projects_table.add_row(
                row.project_path,
                str(row.session_count),
                str(row.analyzed_session_count),
            )
    else:
        projects_table.add_row("none", "0", "0")
    console.print(projects_table)

    classifications_table = Table(title="Classifications")
    classifications_table.add_column("Label")
    classifications_table.add_column("Sessions")
    if summary.classification_counts:
        for row in summary.classification_counts:
            classifications_table.add_row(row.label, str(row.session_count))
    else:
        classifications_table.add_row("no analyzed classifications", "0")
    console.print(classifications_table)

    risk_table = Table(title="Recent risky sessions")
    risk_table.add_column("Session ID")
    risk_table.add_column("Agent")
    risk_table.add_column("Started")
    risk_table.add_column("Labels")
    risk_table.add_column("Stuck")
    risk_table.add_column("Friction")
    risk_table.add_column("Prompt")
    risk_table.add_column("Fit")
    risk_table.add_column("Complexity")
    risk_table.add_column("Project/CWD")
    if summary.recent_risk_sessions:
        for row in summary.recent_risk_sessions:
            risk_table.add_row(
                row.session_id,
                row.agent_name,
                row.started_at or "",
                ", ".join(row.labels) or "none",
                format_optional_score(row.stuckness_score),
                format_optional_score(row.friction_score),
                format_optional_score(row.prompt_clarity_risk),
                format_optional_score(row.agent_fit_risk),
                format_optional_score(row.project_complexity_signal),
                row.project_path or "",
            )
    else:
        risk_table.add_row("none", "", "", "", "", "", "", "", "", "")
    console.print(risk_table)

    commands_table = Table(title="Failed commands")
    commands_table.add_column("Command")
    commands_table.add_column("Failures")
    commands_table.add_column("Sessions")
    commands_table.add_column("Agents")
    commands_table.add_column("Recent")
    commands_table.add_column("Example session")
    if summary.failed_commands:
        for row in summary.failed_commands:
            commands_table.add_row(
                row.command,
                str(row.failure_count),
                str(row.session_count),
                ", ".join(row.agents),
                row.most_recent_at or "",
                row.example_session_id,
            )
    else:
        commands_table.add_row("none", "0", "0", "", "", "")
    console.print(commands_table)

    files_table = Table(title="Repeated files in problematic sessions")
    files_table.add_column("Path")
    files_table.add_column("Activities")
    files_table.add_column("Sessions")
    files_table.add_column("Agents")
    files_table.add_column("Recent")
    files_table.add_column("Example session")
    if summary.repeated_files:
        for row in summary.repeated_files:
            files_table.add_row(
                row.path,
                str(row.activity_count),
                str(row.session_count),
                ", ".join(row.agents),
                row.most_recent_at or "",
                row.example_session_id,
            )
    else:
        files_table.add_row("none", "0", "0", "", "", "")
    console.print(files_table)

    console.print("[bold]Where to look next[/bold]")
    for recommendation in summary.recommendations:
        console.print(f"- {recommendation}")


def format_optional_score(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"
