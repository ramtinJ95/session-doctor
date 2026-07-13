from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .adapters import BaseAdapter
from .ingest_workflow import IngestSummary
from .schemas import SourceKind
from .store import TABLE_NAMES
from .store.models import SessionSummary, StoreInfo


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
