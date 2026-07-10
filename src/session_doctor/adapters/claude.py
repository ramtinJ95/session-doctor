from __future__ import annotations

from pathlib import Path
from typing import Any

from session_doctor.ids import source_id_for_path
from session_doctor.privacy import hash_text
from session_doctor.schemas import AgentName, RawEvent, SessionSource, SourceKind

from .base import BaseAdapter, ParsedSessionBundle
from .claude_commands import ClaudeToolResult, ClaudeToolUse, command_runs_from_tools
from .claude_files import FILE_TOOL_OPERATIONS, file_activity_from_tool_use
from .claude_messages import message_from_record, unsupported_content_shapes
from .claude_metadata import ClaudeSessionMetadata, extract_session_metadata
from .claude_records import raw_event_for_record, read_claude_jsonl
from .claude_sidecars import add_topology_warnings, enrich_tool_result_from_sidecar
from .claude_tools import (
    assistant_tool_use_blocks,
    model_usage_from_record,
    serialized_value,
    tool_call_from_block,
    tool_result_from_block,
    user_tool_result_blocks,
)
from .claude_topology import enrich_claude_sources
from .common import (
    bool_value,
    dict_value,
    hash_json,
    increment_count,
    string_value,
    warning_for_record,
    warning_for_source,
)
from .errors import SourceFormatError

CLAUDE_METADATA_ONLY_TYPES = {
    "agent-name",
    "ai-title",
    "attachment",
    "custom-title",
    "file-history-snapshot",
    "last-prompt",
    "mode",
    "permission-mode",
    "pr-link",
    "progress",
    "queue-operation",
}
CLAUDE_MESSAGE_TYPES = {"assistant", "system", "user"}


class ClaudeCodeAdapter(BaseAdapter):
    name = AgentName.CLAUDE
    display_name = "Claude Code"
    ingestible_source_kinds = (SourceKind.ROOT_SESSION, SourceKind.SUBSESSION)

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".claude" / "projects",)

    def discover(self, root: Path | None = None) -> list[SessionSource]:
        discovery_root = self.root_for_discovery(root)
        if not discovery_root.exists():
            return []

        sources = [
            self._source_for_path(path, discovery_root)
            for path in sorted(discovery_root.rglob("*"))
            if path.is_file()
        ]
        enrich_claude_sources(self._topology_context(sources, discovery_root))
        return sources

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        if source.source_kind not in self.ingestible_source_kinds:
            raise SourceFormatError(
                source_path,
                f"Claude Code source kind {source.source_kind.value} is not a session transcript",
            )

        valid_records, malformed_warnings = read_claude_jsonl(source, source_path)
        session_metadata = extract_session_metadata(source, source_path, valid_records)
        bundle = ParsedSessionBundle(
            session=session_metadata.session,
            parse_warnings=malformed_warnings,
        )
        add_session_identity_warnings(bundle, source, session_metadata)
        add_topology_warnings(bundle, source)

        metadata_only_counts: dict[str, int] = {}
        tool_uses: list[ClaudeToolUse] = []
        tool_results: list[ClaudeToolResult] = []

        for record_index, record in valid_records:
            record_type = string_value(record.get("type"))
            event = raw_event_for_record(
                source,
                session_metadata.session_id,
                record_index,
                record,
            )
            bundle.raw_events.append(event)

            is_sidechain_record = bool_value(record.get("isSidechain"))
            if source.source_kind is SourceKind.ROOT_SESSION and is_sidechain_record is True:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unexpected_sidechain_record",
                        "Root Claude source contains a sidechain record",
                    )
                )
            elif source.source_kind is SourceKind.SUBSESSION and is_sidechain_record is False:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unexpected_root_record",
                        "Claude Code subagent source contains a non-sidechain record",
                    )
                )

            if record_type in CLAUDE_MESSAGE_TYPES:
                parse_message_record(
                    bundle,
                    source,
                    session_metadata,
                    record_index,
                    record,
                    event,
                    metadata_only_counts,
                    tool_uses,
                    tool_results,
                )
            elif record_type in CLAUDE_METADATA_ONLY_TYPES:
                increment_count(metadata_only_counts, record_type)
            else:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unsupported_record_type",
                        f"Unsupported Claude Code record type: {record_type}",
                        {"record_type": record_type},
                    )
                )

        bundle.command_runs.extend(
            command_runs_from_tools(
                session_metadata.session_id,
                tool_uses,
                tool_results,
                session_cwd=session_metadata.session.cwd,
            )
        )
        session_metadata.session.metadata["claude_metadata_only_counts"] = metadata_only_counts
        return bundle

    def source_for_path(self, path: Path) -> SessionSource:
        source_kind = classify_claude_path(path)
        if source_kind is SourceKind.SUBSESSION:
            discovery_root = path.parent.parent
            discovered = self.discover(discovery_root)
            matched = next(
                (source for source in discovered if Path(source.source_path) == path),
                None,
            )
            if matched is not None:
                return matched
        source = SessionSource(
            source_id=source_id_for_path(self.name, path),
            agent_name=self.name,
            source_path=str(path),
            source_kind=source_kind,
            metadata={"ignored": source_kind not in self.ingestible_source_kinds},
        )
        if source_kind is SourceKind.ROOT_SESSION:
            session_dir = path.parent / path.stem
            related_sources = [source]
            if session_dir.is_dir():
                related_sources.extend(
                    self._source_for_path(candidate, path.parent)
                    for candidate in sorted(session_dir.rglob("*"))
                    if candidate.is_file()
                )
            enrich_claude_sources(related_sources)
        return source

    def _source_for_path(self, path: Path, root: Path) -> SessionSource:
        source_kind = classify_claude_path(path, root)
        return SessionSource(
            source_id=source_id_for_path(self.name, path),
            agent_name=self.name,
            source_path=str(path),
            source_kind=source_kind,
            metadata={
                "relative_path": str(path.relative_to(root)),
                "ignored": source_kind not in self.ingestible_source_kinds,
            },
        )

    def _topology_context(
        self,
        sources: list[SessionSource],
        discovery_root: Path,
    ) -> list[SessionSource]:
        if discovery_root.name == "subagents":
            session_dir = discovery_root.parent
        elif (discovery_root / "subagents").is_dir():
            session_dir = discovery_root
        else:
            return sources

        root_path = session_dir.parent / f"{session_dir.name}.jsonl"
        if not root_path.is_file() or any(
            Path(source.source_path) == root_path for source in sources
        ):
            return sources
        return [*sources, self._source_for_path(root_path, session_dir.parent)]


def parse_message_record(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    session_metadata: ClaudeSessionMetadata,
    record_index: int,
    record: dict[str, Any],
    event: RawEvent,
    metadata_only_counts: dict[str, int],
    tool_uses: list[ClaudeToolUse],
    tool_results: list[ClaudeToolResult],
) -> None:
    record_type = string_value(record.get("type")) or "missing"
    message = message_from_record(session_metadata.session_id, event, record)
    if message is not None:
        bundle.messages.append(message)
    elif record_type == "system":
        system_subtype = string_value(record.get("subtype")) or "metadata"
        increment_count(metadata_only_counts, f"system.{system_subtype}")
    else:
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "unsupported_message_shape",
                f"Claude Code {record_type} record has no supported message content",
                {"record_type": record_type},
            )
        )

    for block_index, shape in unsupported_content_shapes(record):
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "unsupported_content_shape",
                "Unsupported Claude Code message content shape",
                {"block_index": block_index, "shape": shape},
                identity=block_index,
            )
        )

    if record_type == "assistant":
        parse_assistant_record(
            bundle,
            source,
            session_metadata,
            record_index,
            record,
            event,
            tool_uses,
        )
    elif record_type == "user":
        parse_user_tool_results(
            bundle,
            source,
            session_metadata,
            record_index,
            record,
            event,
            tool_results,
        )
    elif record_type == "system" and string_value(record.get("subtype")) == "api_error":
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "claude_api_error",
                "Claude Code recorded an API error",
                safe_error_metadata(record),
            )
        )


def parse_assistant_record(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    session_metadata: ClaudeSessionMetadata,
    record_index: int,
    record: dict[str, Any],
    event: RawEvent,
    tool_uses: list[ClaudeToolUse],
) -> None:
    record_cwd = string_value(record.get("cwd")) or session_metadata.session.cwd
    for block_index, block in assistant_tool_use_blocks(record):
        if string_value(block.get("id")) is None:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "missing_tool_use_id",
                    "Claude Code tool_use block has no native ID",
                    {"tool_name": string_value(block.get("name"))},
                    identity=block_index,
                )
            )
        tool_use = ClaudeToolUse(
            event=event,
            block=block,
            block_index=block_index,
            cwd=record_cwd,
        )
        tool_uses.append(tool_use)
        bundle.tool_calls.append(
            tool_call_from_block(
                session_metadata.session_id,
                event,
                block,
                block_index,
            )
        )
        file_activity = file_activity_from_tool_use(
            session_metadata.session_id,
            event,
            block,
            block_index,
            cwd=record_cwd,
            project_path=session_metadata.session.project_path,
        )
        if file_activity is not None:
            bundle.file_activities.append(file_activity)
        elif (string_value(block.get("name")) or "").lower() in FILE_TOOL_OPERATIONS:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "missing_file_path",
                    "Claude Code file tool call has no supported path",
                    {"tool_name": string_value(block.get("name"))},
                    identity=block_index,
                )
            )

    usage = model_usage_from_record(session_metadata.session_id, event, record)
    if usage is not None:
        bundle.model_usage.append(usage)

    if record.get("error") is not None or bool_value(record.get("isApiErrorMessage")) is True:
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "claude_assistant_error",
                "Claude Code recorded an assistant error",
                safe_error_metadata(record),
            )
        )
    message = dict_value(record.get("message"))
    stop_reason = string_value(message.get("stop_reason"))
    if stop_reason == "max_tokens":
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "claude_assistant_truncated",
                "Claude Code assistant response ended with a truncation signal",
                {
                    "stop_reason": stop_reason,
                    "stop_sequence_present": message.get("stop_sequence") is not None,
                },
            )
        )


def parse_user_tool_results(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    session_metadata: ClaudeSessionMetadata,
    record_index: int,
    record: dict[str, Any],
    event: RawEvent,
    tool_results: list[ClaudeToolResult],
) -> None:
    for block_index, block in user_tool_result_blocks(record):
        if string_value(block.get("tool_use_id")) is None:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "missing_tool_result_id",
                    "Claude Code tool_result block has no tool_use_id",
                    {"block_index": block_index},
                    identity=block_index,
                )
            )
        tool_result = ClaudeToolResult(event=event, record=record, block=block)
        tool_results.append(tool_result)
        normalized_result = tool_result_from_block(
            session_metadata.session_id,
            event,
            record,
            block,
            block_index,
        )
        bundle.tool_results.append(
            enrich_tool_result_from_sidecar(
                bundle,
                source,
                record_index,
                record,
                normalized_result,
            )
        )


def add_session_identity_warnings(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    session_metadata: ClaudeSessionMetadata,
) -> None:
    if not session_metadata.native_session_ids:
        bundle.parse_warnings.append(
            warning_for_source(
                source,
                "missing_session_id",
                "Claude Code source has no native sessionId; using filename identity",
            )
        )
    elif len(session_metadata.native_session_ids) > 1:
        bundle.parse_warnings.append(
            warning_for_source(
                source,
                "inconsistent_session_id",
                "Claude Code source contains multiple native sessionId values",
                {
                    "native_session_ids": list(session_metadata.native_session_ids),
                    "count": len(session_metadata.native_session_ids),
                },
            )
        )


def safe_error_metadata(record: dict[str, Any]) -> dict[str, Any]:
    error = record.get("error")
    if error is None:
        message = dict_value(record.get("message"))
        error = message.get("error")
    serialized_error = serialized_value(error) if error is not None else None
    return {
        "subtype": string_value(record.get("subtype")),
        "is_api_error_message": bool_value(record.get("isApiErrorMessage")),
        "error_hash": hash_text(serialized_error) if serialized_error is not None else None,
        "error_length": len(serialized_error) if serialized_error is not None else None,
        "record_hash": hash_json(record),
    }


def classify_claude_path(path: Path, root: Path | None = None) -> SourceKind:
    relative_path = path.relative_to(root) if root and path.is_relative_to(root) else path
    parent_name = claude_source_parent_name(path, relative_path, root)
    is_subagent_file = parent_name == "subagents" and path.name.startswith("agent-")
    if is_claude_tool_result_path(path, relative_path, root, parent_name):
        return SourceKind.TOOL_RESULT
    if is_subagent_file and path.name.endswith(".meta.json"):
        return SourceKind.SUBAGENT_METADATA
    if is_subagent_file and path.suffix == ".jsonl":
        return SourceKind.SUBSESSION
    if path.suffix == ".jsonl":
        return SourceKind.ROOT_SESSION
    if path.suffix in {".md", ".txt"}:
        return SourceKind.MEMORY
    return SourceKind.AUXILIARY


def claude_source_parent_name(
    path: Path,
    relative_path: Path,
    root: Path | None,
) -> str:
    if root is not None and path.parent == root and root.name in {"subagents", "tool-results"}:
        return root.name
    return relative_path.parent.name


def is_claude_tool_result_path(
    path: Path,
    relative_path: Path,
    root: Path | None,
    parent_name: str,
) -> bool:
    if parent_name != "tool-results":
        return False
    if root is not None and (path.parent == root or len(relative_path.parts) >= 3):
        return True
    session_dir = path.parent.parent
    root_transcript = session_dir.parent / f"{session_dir.name}.jsonl"
    return root_transcript.is_file()


__all__ = [
    "CLAUDE_MESSAGE_TYPES",
    "CLAUDE_METADATA_ONLY_TYPES",
    "ClaudeCodeAdapter",
    "ClaudeSessionMetadata",
    "classify_claude_path",
    "extract_session_metadata",
    "message_from_record",
    "raw_event_for_record",
    "read_claude_jsonl",
]
