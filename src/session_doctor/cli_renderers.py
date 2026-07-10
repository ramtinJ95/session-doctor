from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .adapters import BaseAdapter
from .batch_analysis import BatchAnalysisResult
from .ingest_workflow import IngestSummary
from .privacy import redact_home
from .schemas import AnalysisRun, SessionClassification, SessionFeature, SourceKind
from .store import TABLE_NAMES
from .store.aggregate_queries import SCORE_NAMES
from .store.models import AggregateSummary, SessionSummary, StoreInfo
from .store.trend_models import TrendCohort, TrendMetrics, TrendReport

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


def render_batch_analysis(result: BatchAnalysisResult, console: Console) -> None:
    table = Table(title="Batch analysis")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Matching", str(result.matching_count))
    table.add_row("Selected", str(result.selected_count))
    table.add_row("Succeeded", str(len(result.succeeded_session_ids)))
    table.add_row("Skipped", str(len(result.skipped_session_ids)))
    table.add_row("Failed", str(len(result.failures)))
    console.print(table)

    if result.succeeded_session_ids or result.skipped_session_ids:
        session_table = Table(title="Batch sessions")
        session_table.add_column("Session ID")
        session_table.add_column("Status")
        for session_id in result.succeeded_session_ids:
            session_table.add_row(session_id, "succeeded")
        for session_id in result.skipped_session_ids:
            session_table.add_row(session_id, "skipped")
        console.print(session_table)

    if result.failures:
        failure_table = Table(title="Analysis failures")
        failure_table.add_column("Session ID")
        failure_table.add_column("Code")
        failure_table.add_column("Message")
        for failure in result.failures:
            failure_table.add_row(failure.session_id, failure.code.value, failure.message)
        console.print(failure_table)


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


def render_trends(report: TrendReport, database_path: Path, console: Console) -> None:
    scope_table = Table(title="Session trends")
    scope_table.add_column("Metric")
    scope_table.add_column("Value")
    scope_table.add_row("Database", redact_home(database_path))
    scope_table.add_row("Agent filter", report.filters.agent_name or "all")
    scope_table.add_row(
        "Project filter",
        redact_home(report.filters.project_path) if report.filters.project_path else "all",
    )
    scope_table.add_row("Bucket", report.filters.bucket.value)
    scope_table.add_row("Periods", str(report.filters.periods))
    scope_table.add_row("Window start", format_timestamp(report.window.start))
    scope_table.add_row("Window end", format_timestamp(report.window.end))
    scope_table.add_row("Latest session", format_timestamp(report.window.latest_session_at))
    scope_table.add_row("Matching sessions", str(report.scope.matching_sessions))
    scope_table.add_row("Windowed sessions", str(report.scope.windowed_sessions))
    scope_table.add_row("Outside window", str(report.scope.outside_window_sessions))
    scope_table.add_row("Untimed sessions", str(report.scope.untimed_sessions))
    scope_table.add_row("Current analysis", str(report.scope.windowed_analysis.current))
    scope_table.add_row("Stale analysis", str(report.scope.windowed_analysis.stale))
    scope_table.add_row("Never analyzed", str(report.scope.windowed_analysis.never))
    console.print(scope_table)
    if report.scope.windowed_analysis.stale or report.scope.windowed_analysis.never:
        console.print(
            "Run [bold]session-doctor analyze --all[/bold] with the same filters to restore "
            "current analysis coverage."
        )

    render_trend_cohort("Top-level", report.cohorts.top_level, console)
    render_trend_cohort("Sidechain", report.cohorts.sidechain, console)


def render_trend_cohort(title: str, cohort: TrendCohort, console: Console) -> None:
    if cohort.totals.sessions == 0:
        return
    bucket_table = Table(title=f"{title} buckets")
    bucket_table.add_column("Start")
    bucket_table.add_column("Sessions")
    bucket_table.add_column("Current")
    bucket_table.add_column("Coverage")
    bucket_table.add_column("Risk rate")
    for score_name in SCORE_NAMES:
        bucket_table.add_column(score_name.removesuffix("_score").replace("_risk", ""))
    bucket_table.add_column("Classifications")
    for bucket in cohort.buckets:
        metrics = bucket.metrics
        bucket_table.add_row(
            bucket.start.date().isoformat(),
            str(metrics.sessions),
            str(metrics.current_analyzed),
            format_rate(metrics.current_analysis_coverage),
            format_rate(metrics.risky_session_rate),
            *(format_score_metric(metrics, score_name) for score_name in SCORE_NAMES),
            ", ".join(
                f"{row.label}={row.session_count}/{format_rate(row.rate)}"
                for row in metrics.classifications
            ),
        )
    console.print(bucket_table)

    judgment_table = Table(title=f"{title} judgments")
    judgment_table.add_column("Metric")
    judgment_table.add_column("Status")
    judgment_table.add_column("Earlier")
    judgment_table.add_column("Recent")
    judgment_table.add_column("Delta")
    judgment_table.add_column("Reason")
    for judgment in cohort.judgments:
        judgment_table.add_row(
            judgment.metric_name,
            judgment.status.value,
            format_optional_score(judgment.earlier_value),
            format_optional_score(judgment.recent_value),
            format_optional_score(judgment.delta),
            ", ".join(judgment.reasons),
        )
    console.print(judgment_table)


def format_score_metric(metrics: TrendMetrics, metric_name: str) -> str:
    score = next(row for row in metrics.scores if row.metric_name == metric_name)
    return f"{format_optional_score(score.average)} ({score.sample_count})"


def format_rate(value: float | None) -> str:
    return "" if value is None else f"{value:.0%}"


def format_timestamp(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def format_optional_score(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"
