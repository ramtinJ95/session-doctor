from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from rich.console import Console

from .adapters import BaseAdapter, RecoverableSourceError
from .adapters.codex import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
)
from .schemas.sessions import SessionSource
from .store import DuckDBStore


@dataclass
class SkippedSource:
    source_path: str
    category: str
    detail: str


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
    discovered_source_counts: dict[str, int] | None = None
    selected_source_counts: dict[str, int] | None = None
    parsed_source_counts: dict[str, int] = field(default_factory=dict)
    skipped_sources: tuple[SkippedSource, ...] = ()


def ingest_sources(
    adapter: BaseAdapter,
    sources: list[SessionSource],
    store: DuckDBStore,
    console: Console,
    *,
    continue_on_source_error: bool,
    discovered_source_counts: dict[str, int] | None = None,
) -> IngestSummary:
    summary = IngestSummary(
        agent_display_name=adapter.display_name,
        source_count=len(sources),
        discovered_source_counts=discovered_source_counts,
        selected_source_counts=dict(
            sorted(Counter(source.source_kind.value for source in sources).items())
        ),
    )

    for session_source in sources:
        try:
            bundle = adapter.parse_source(session_source)
        except RecoverableSourceError as exc:
            if not continue_on_source_error:
                raise
            summary.skipped_source_count += 1
            skipped_source = SkippedSource(
                source_path=session_source.source_path,
                category=exc.category,
                detail=exc.detail,
            )
            summary.skipped_sources = (*summary.skipped_sources, skipped_source)
            console.print(
                f"[yellow]Skipped source:[/yellow] {skipped_source.source_path} "
                f"(category={skipped_source.category}) {skipped_source.detail}"
            )
            continue
        store.insert_parsed_bundle(session_source, bundle)

        source_kind = session_source.source_kind.value
        summary.parsed_source_counts[source_kind] = (
            summary.parsed_source_counts.get(source_kind, 0) + 1
        )
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

    return summary
