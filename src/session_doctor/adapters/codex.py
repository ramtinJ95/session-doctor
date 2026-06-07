from __future__ import annotations

from pathlib import Path
from typing import Any

from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, NormalizedRole, RawEvent, SessionSource, SourceKind

from .base import BaseAdapter, ParsedSessionBundle
from .codex_commands import command_output_parts, command_run_from_event_msg, command_text
from .codex_files import file_activities_from_patch_event
from .codex_messages import (
    CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK,
    CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
    has_nearby_response_message,
    message_from_event_msg_fallback,
    message_from_response_item,
    message_identity,
    normalize_codex_role,
)
from .codex_metadata import CodexSessionMetadata, extract_session_metadata, session_id_from_filename
from .codex_records import raw_event_for_record, read_codex_jsonl
from .codex_tools import (
    argument_keys,
    model_usage_from_token_count,
    tool_call_from_response_item,
    tool_call_from_web_search_call,
    tool_result_from_response_item,
    tool_result_from_web_search_end,
)
from .common import dict_value, increment_count, string_value, warning_for_record


class CodexAdapter(BaseAdapter):
    name = AgentName.CODEX
    display_name = "Codex"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".codex" / "sessions",)

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

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        valid_records, malformed_warnings = read_codex_jsonl(source, source_path)
        session_metadata = extract_session_metadata(source, source_path, valid_records)
        bundle = ParsedSessionBundle(
            session=session_metadata.session,
            parse_warnings=malformed_warnings,
        )

        response_messages: list[tuple[int, tuple[NormalizedRole, str | None]]] = []
        event_message_candidates: list[tuple[int, RawEvent, dict[str, Any]]] = []
        response_item_message_count = 0
        event_msg_fallback_count = 0
        compacted_record_count = 0
        expected_ignored_counts: dict[str, int] = {}

        for record_index, record in valid_records:
            record_type = string_value(record.get("type"))
            payload = dict_value(record.get("payload"))
            timestamp = string_value(record.get("timestamp"))
            event = raw_event_for_record(source, session_metadata.session_id, record_index, record)
            bundle.raw_events.append(event)

            if record_type == "response_item":
                payload_type = string_value(payload.get("type"))
                if payload_type == "message":
                    message = message_from_response_item(
                        session_metadata.session_id,
                        event,
                        payload,
                        timestamp,
                    )
                    bundle.messages.append(message)
                    response_messages.append((record_index, message_identity(message)))
                    response_item_message_count += 1
                elif payload_type in {"function_call", "custom_tool_call"}:
                    bundle.tool_calls.append(
                        tool_call_from_response_item(session_metadata.session_id, event, payload)
                    )
                elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                    bundle.tool_results.append(
                        tool_result_from_response_item(session_metadata.session_id, event, payload)
                    )
                elif payload_type == "web_search_call":
                    bundle.tool_calls.append(
                        tool_call_from_web_search_call(session_metadata.session_id, event, payload)
                    )
                elif payload_type == "reasoning":
                    increment_count(expected_ignored_counts, "response_item.reasoning")
                else:
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "unsupported_response_item",
                            f"Unsupported Codex response_item payload type: {payload_type}",
                            {"payload_type": payload_type},
                        )
                    )
            elif record_type == "event_msg":
                payload_type = string_value(payload.get("type"))
                if payload_type == "exec_command_end":
                    bundle.command_runs.append(
                        command_run_from_event_msg(session_metadata.session_id, event, payload)
                    )
                elif payload_type == "patch_apply_end":
                    bundle.file_activities.extend(
                        file_activities_from_patch_event(
                            session_metadata.session_id,
                            event,
                            payload,
                        )
                    )
                elif payload_type in {"user_message", "agent_message"}:
                    event_message_candidates.append((record_index, event, payload))
                elif payload_type == "token_count":
                    bundle.model_usage.append(
                        model_usage_from_token_count(session_metadata.session_id, event, payload)
                    )
                elif payload_type == "web_search_end":
                    bundle.tool_results.append(
                        tool_result_from_web_search_end(session_metadata.session_id, event, payload)
                    )
                elif payload_type in {"task_started", "task_complete"}:
                    increment_count(expected_ignored_counts, f"event_msg.{payload_type}")
                elif payload_type == "error":
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "codex_error",
                            string_value(payload.get("message")) or "Codex error event",
                            {"payload_type": payload_type},
                        )
                    )
                else:
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "unsupported_event_msg",
                            f"Unsupported Codex event_msg payload type: {payload_type}",
                            {"payload_type": payload_type},
                        )
                    )
            elif record_type in {"session_meta", "turn_context"}:
                continue
            elif record_type == "compacted":
                compacted_record_count += 1
            else:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unsupported_record_type",
                        f"Unsupported Codex record type: {record_type}",
                        {"record_type": record_type},
                    )
                )

        for record_index, event, payload in event_message_candidates:
            message = message_from_event_msg_fallback(session_metadata.session_id, event, payload)
            if has_nearby_response_message(
                record_index,
                message_identity(message),
                response_messages,
            ):
                continue
            bundle.messages.append(message)
            event_msg_fallback_count += 1

        if bundle.session:
            bundle.session.metadata["codex_message_source_counts"] = {
                CODEX_MESSAGE_SOURCE_RESPONSE_ITEM: response_item_message_count,
                CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK: event_msg_fallback_count,
            }
            bundle.session.metadata["compacted_record_count"] = compacted_record_count
            bundle.session.metadata["codex_expected_ignored_counts"] = expected_ignored_counts

        return bundle


__all__ = [
    "CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK",
    "CODEX_MESSAGE_SOURCE_RESPONSE_ITEM",
    "CodexAdapter",
    "CodexSessionMetadata",
    "argument_keys",
    "command_output_parts",
    "command_run_from_event_msg",
    "command_text",
    "extract_session_metadata",
    "file_activities_from_patch_event",
    "has_nearby_response_message",
    "message_from_event_msg_fallback",
    "message_from_response_item",
    "message_identity",
    "model_usage_from_token_count",
    "normalize_codex_role",
    "raw_event_for_record",
    "read_codex_jsonl",
    "session_id_from_filename",
    "tool_call_from_response_item",
    "tool_call_from_web_search_call",
    "tool_result_from_response_item",
    "tool_result_from_web_search_end",
]
