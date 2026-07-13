from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from .adapters import BaseAdapter, ParsedSessionBundle, RecoverableSourceError
from .adapters.base import CapturedAdapterMember
from .adapters.codex import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
)
from .adapters.common import read_source_bytes
from .schemas.sessions import SessionSource
from .store import BundleMemberCapture, DuckDBStore


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
        captured_parse_source = adapter.source_for_captured_parse(session_source)
        try:
            primary_path = Path(session_source.source_path).expanduser()
            primary_started_at = datetime.now(UTC)
            primary_signature_before = file_capture_signature(primary_path)
            primary_before = file_modified_at(primary_path)
            source_bytes = read_source_bytes(
                primary_path,
                agent_display_name=adapter.display_name,
            )
            primary_signature_after = file_capture_signature(primary_path)
            captured_source = store.capture_source(
                captured_parse_source,
                source_bytes,
                native_modified_at=primary_before,
                captured_at=primary_started_at,
            )
            adapter_members, bundle_members = capture_bundle_members(
                adapter,
                captured_parse_source,
                source_bytes,
                store,
            )
            prepared_source = adapter.prepare_captured_source(
                captured_parse_source,
                adapter_members,
            )
            primary_changed = primary_signature_before != primary_signature_after
            primary_capture_status = "changed_during_capture" if primary_changed else "captured"
            aggregate_capture_status = (
                "skewed"
                if primary_changed
                or any(
                    member.member_capture_status == "changed_during_capture"
                    for member in bundle_members
                )
                else "incomplete"
                if any(
                    member.member_capture_status in {"missing", "unreadable"}
                    for member in bundle_members
                )
                else "complete"
            )
            try:
                bundle = adapter.parse_source(prepared_source, source_bytes)
                if aggregate_capture_status == "complete" and parse_snapshot_incomplete(
                    bundle, source_bytes
                ):
                    aggregate_capture_status = "incomplete"
            except Exception:
                failed_bundle = store.create_single_source_bundle(
                    captured_parse_source,
                    captured_source,
                    f"parse-failed:{captured_parse_source.source_id}",
                    native_identity_status="fallback_parse_failed",
                    capture_status="parse_failed",
                    primary_capture_status=primary_capture_status,
                    capture_evidence={
                        "primary_signature_before": primary_signature_before,
                        "primary_signature_after": primary_signature_after,
                    },
                )
                failed_bundle = store.add_bundle_members(failed_bundle, bundle_members)
                store.record_lifecycle(
                    failed_bundle.snapshot_bundle_id,
                    terminal_observed=False,
                )
                raise
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
        native_session_identity = (
            bundle.session.native_session_id
            if bundle.session and bundle.session.native_session_id
            else captured_parse_source.native_session_id or captured_parse_source.source_id
        )
        captured_bundle = store.create_single_source_bundle(
            captured_parse_source,
            captured_source,
            native_session_identity,
            capture_status=aggregate_capture_status,
            primary_capture_status=primary_capture_status,
            capture_evidence={
                "primary_signature_before": primary_signature_before,
                "primary_signature_after": primary_signature_after,
            },
        )
        captured_bundle = store.add_bundle_members(captured_bundle, bundle_members)
        store.record_lifecycle(
            captured_bundle.snapshot_bundle_id,
            terminal_observed=adapter.terminal_observed(
                captured_parse_source,
                source_bytes,
            ),
        )
        store.insert_parsed_bundle(
            captured_parse_source,
            bundle,
            captured_source,
            captured_bundle,
        )

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


def capture_bundle_members(
    adapter: BaseAdapter,
    primary_source: SessionSource,
    primary_bytes: bytes,
    store: DuckDBStore,
) -> tuple[tuple[CapturedAdapterMember, ...], tuple[BundleMemberCapture, ...]]:
    adapter_members: list[CapturedAdapterMember] = [
        CapturedAdapterMember(primary_source, "primary", primary_bytes)
    ]
    bundle_members: list[BundleMemberCapture] = []
    for capture_order, (member_source, member_role) in enumerate(
        adapter.bundle_member_sources(primary_source, primary_bytes), start=1
    ):
        path = Path(member_source.source_path).expanduser()
        started_at = datetime.now(UTC)
        signature_before = file_capture_signature(path)
        modified_before = file_modified_at(path)
        try:
            member_bytes = path.read_bytes()
        except FileNotFoundError:
            status = "missing"
            member_bytes = None
        except OSError:
            status = "unreadable"
            member_bytes = None
        completed_at = datetime.now(UTC)
        modified_after = file_modified_at(path)
        signature_after = file_capture_signature(path)
        captured = None
        if member_bytes is not None:
            status = "changed_during_capture" if signature_before != signature_after else "captured"
            captured = store.capture_source(
                member_source,
                member_bytes,
                native_modified_at=modified_before,
                captured_at=started_at,
            )
            adapter_members.append(CapturedAdapterMember(member_source, member_role, member_bytes))
        bundle_members.append(
            BundleMemberCapture(
                source_id=member_source.source_id,
                source_path=member_source.source_path,
                member_role=member_role,
                member_capture_status=status,
                capture_order=capture_order,
                capture_started_at=started_at,
                capture_completed_at=completed_at,
                captured_source=captured,
                native_modified_before=modified_before,
                native_modified_after=modified_after,
                evidence={
                    "signature_before": signature_before,
                    "signature_after": signature_after,
                },
            )
        )
    return tuple(adapter_members), tuple(bundle_members)


def file_modified_at(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def file_capture_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def parse_snapshot_incomplete(bundle: ParsedSessionBundle, source_bytes: bytes) -> bool:
    source_lines = source_bytes.decode("utf-8").splitlines()
    last_record_index = max(len(source_lines) - 1, 0)
    last_line = source_lines[-1].rstrip() if source_lines else ""
    if last_line.endswith(("}", "]")):
        return False
    return any(
        warning.metadata.get("code") == "malformed_json"
        and warning.record_index == last_record_index
        for warning in bundle.parse_warnings
    )
