from __future__ import annotations

from pathlib import Path

from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, NormalizedRole, SessionSource, SourceKind

from .base import BaseAdapter, ParsedSessionBundle
from .common import (
    content_blocks,
    dict_value,
    increment_count,
    string_value,
    warning_for_record,
    warning_for_source,
)
from .pi_commands import (
    bash_execution_parent_record_ids,
    command_run_from_bash_execution,
    command_run_from_tool_result,
)
from .pi_correlation import PiCommandCorrelation
from .pi_files import file_activities_from_tool_call
from .pi_messages import (
    message_from_record,
    normalize_pi_role,
    phase_from_content,
    phase_from_metadata,
)
from .pi_metadata import (
    PiSessionMetadata,
    extract_session_metadata,
    has_usable_session_record,
    project_hint_from_source_path,
    session_id_from_filename,
)
from .pi_records import raw_event_for_record, read_pi_jsonl
from .pi_tool_calls import arguments_from_tool_call_block, tool_call_from_block
from .pi_tool_results import tool_result_from_message
from .pi_usage import model_usage_from_message

PI_METADATA_ONLY_TYPES = {
    "branch_summary",
    "compaction",
    "custom",
    "custom_message",
    "label",
    "model_change",
    "session_info",
    "thinking_level_change",
}


class PiAdapter(BaseAdapter):
    name = AgentName.PI
    display_name = "Pi"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".pi" / "agent" / "sessions",)

    def discover(self, root: Path | None = None) -> list[SessionSource]:
        discovery_root = self.root_for_discovery(root)
        if not discovery_root.exists():
            return []

        return [
            SessionSource(
                source_id=source_id_for_path(self.name, path),
                agent_name=self.name,
                source_path=str(path),
                source_kind=SourceKind.ROOT_SESSION,
                metadata={"relative_path": str(path.relative_to(discovery_root))},
            )
            for path in sorted(discovery_root.rglob("*.jsonl"))
            if path.is_file()
        ]

    def parse_source(self, source: SessionSource, source_bytes: bytes) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        valid_records, malformed_warnings = read_pi_jsonl(source, source_path, source_bytes)
        session_metadata = extract_session_metadata(source, source_path, valid_records)
        bundle = ParsedSessionBundle(
            session=session_metadata.session,
            parse_warnings=malformed_warnings,
        )
        if not has_usable_session_record(valid_records):
            bundle.parse_warnings.append(
                warning_for_source(
                    source,
                    "missing_session_record",
                    "Pi source is missing a usable session record",
                    {"source_path": str(source_path)},
                )
            )
        metadata_only_counts: dict[str, int] = {}
        command_correlation = PiCommandCorrelation.from_records(valid_records)

        for record_index, record in valid_records:
            record_type = string_value(record.get("type"))
            event = raw_event_for_record(source, session_metadata.session_id, record_index, record)
            bundle.raw_events.append(event)

            if record_type == "message":
                message_payload = dict_value(record.get("message"))
                role = normalize_pi_role(string_value(message_payload.get("role")))
                if role is NormalizedRole.UNKNOWN:
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "unsupported_message_role",
                            "Unsupported Pi message role",
                            {"role": string_value(message_payload.get("role"))},
                        )
                    )
                    continue
                if role is NormalizedRole.ASSISTANT:
                    for block_index, block in enumerate(
                        content_blocks(message_payload.get("content"))
                    ):
                        block_type = string_value(block.get("type"))
                        if block_type != "toolCall":
                            continue
                        tool_call = tool_call_from_block(
                            session_metadata.session_id,
                            event,
                            block,
                            block_index,
                        )
                        bundle.tool_calls.append(tool_call)
                        command_correlation.remember_tool_call_arguments(
                            tool_call.native_tool_call_id,
                            block,
                        )
                        bundle.file_activities.extend(
                            file_activities_from_tool_call(
                                session_metadata.session_id,
                                event,
                                block,
                                block_index,
                                cwd=session_metadata.session.cwd,
                                project_path=session_metadata.session.project_path,
                            )
                        )
                    usage = model_usage_from_message(session_metadata.session_id, event, record)
                    if usage:
                        bundle.model_usage.append(usage)
                elif string_value(message_payload.get("role")) == "toolResult":
                    command_correlation.remember_tool_result_link(record)
                    bundle.tool_results.append(
                        tool_result_from_message(session_metadata.session_id, event, record)
                    )
                    if not command_correlation.has_bash_execution_result(record):
                        command_run = command_run_from_tool_result(
                            session_metadata.session_id,
                            event,
                            record,
                            command_correlation.tool_call_arguments_by_id,
                        )
                        if command_run:
                            bundle.command_runs.append(command_run)
                elif string_value(message_payload.get("role")) == "bashExecution":
                    bundle.command_runs.append(
                        command_run_from_bash_execution(
                            session_metadata.session_id,
                            event,
                            record,
                            command_correlation.tool_call_id_by_tool_result_id,
                        )
                    )
                bundle.messages.append(
                    message_from_record(session_metadata.session_id, event, record)
                )
            elif record_type == "session":
                continue
            elif record_type in PI_METADATA_ONLY_TYPES:
                increment_count(metadata_only_counts, record_type or "missing")
            else:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unsupported_record_type",
                        f"Unsupported Pi record type: {record_type}",
                        {"record_type": record_type},
                    )
                )

        if bundle.session:
            bundle.session.metadata["pi_metadata_only_counts"] = metadata_only_counts

        return bundle


__all__ = [
    "PI_METADATA_ONLY_TYPES",
    "PiAdapter",
    "PiCommandCorrelation",
    "PiSessionMetadata",
    "arguments_from_tool_call_block",
    "bash_execution_parent_record_ids",
    "command_run_from_bash_execution",
    "command_run_from_tool_result",
    "extract_session_metadata",
    "file_activities_from_tool_call",
    "has_usable_session_record",
    "message_from_record",
    "model_usage_from_message",
    "normalize_pi_role",
    "phase_from_content",
    "phase_from_metadata",
    "project_hint_from_source_path",
    "raw_event_for_record",
    "read_pi_jsonl",
    "session_id_from_filename",
    "tool_call_from_block",
    "tool_result_from_message",
]
