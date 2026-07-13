from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from session_doctor.ids import source_id_for_path
from session_doctor.schemas import (
    AgentName,
    CapabilitySupport,
    NormalizedRole,
    RawEvent,
    SessionSource,
    SourceKind,
)

from .base import BaseAdapter, ParsedSessionBundle, adapter_capability
from .codex_commands import (
    command_output_parts,
    command_run_from_event_msg,
    command_run_from_response_item,
    command_text,
    response_command_output,
    response_command_spec,
)
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

CODEX_METADATA_EVENT_TYPES = {
    "context_compacted",
    "entered_review_mode",
    "exited_review_mode",
    "mcp_tool_call_end",
    "thread_settings_applied",
    "sub_agent_activity",
}
CODEX_METADATA_RECORD_TYPES = {"inter_agent_communication_metadata", "world_state"}
CODEX_EXPECTED_RESPONSE_ITEM_TYPES = {"agent_message"}


class CodexAdapter(BaseAdapter):
    name = AgentName.CODEX
    display_name = "Codex"
    capabilities = (
        adapter_capability("native_causal_links", CapabilitySupport.SUPPORTED, "native"),
        adapter_capability("terminal_evidence", CapabilitySupport.SUPPORTED, "native"),
        adapter_capability("delegation_topology", CapabilitySupport.UNKNOWN, "unavailable"),
        adapter_capability("model_usage", CapabilitySupport.SUPPORTED, "native"),
        adapter_capability("native_project_metadata", CapabilitySupport.UNKNOWN, "unavailable"),
        adapter_capability("native_cost", CapabilitySupport.UNSUPPORTED, "unavailable"),
    )

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

    def terminal_observed(self, source: SessionSource, source_bytes: bytes) -> bool:
        latest_task_event: str | None = None
        for line in source_bytes.decode("utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "event_msg":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            payload_type = payload.get("type")
            if payload_type in {"task_started", "task_complete"}:
                latest_task_event = str(payload_type)
        return latest_task_event == "task_complete"

    def parse_source(self, source: SessionSource, source_bytes: bytes) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        valid_records, malformed_warnings = read_codex_jsonl(source, source_path, source_bytes)
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
        current_cwd = session_metadata.session.cwd
        response_calls: list[tuple[int, RawEvent, dict[str, Any]]] = []
        response_outputs: list[tuple[int, RawEvent, dict[str, Any]]] = []
        legacy_commands: list[tuple[int, RawEvent, dict[str, Any]]] = []

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
                elif payload_type in {
                    "function_call",
                    "custom_tool_call",
                    "tool_search_call",
                }:
                    response_calls.append((record_index, event, payload))
                elif payload_type in {
                    "function_call_output",
                    "custom_tool_call_output",
                    "tool_search_output",
                }:
                    response_outputs.append((record_index, event, payload))
                elif payload_type == "web_search_call":
                    bundle.tool_calls.append(
                        tool_call_from_web_search_call(session_metadata.session_id, event, payload)
                    )
                elif payload_type == "reasoning":
                    increment_count(expected_ignored_counts, "response_item.reasoning")
                elif payload_type in CODEX_EXPECTED_RESPONSE_ITEM_TYPES:
                    increment_count(expected_ignored_counts, f"response_item.{payload_type}")
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
                    legacy_commands.append((record_index, event, payload))
                elif payload_type == "patch_apply_end":
                    bundle.file_activities.extend(
                        file_activities_from_patch_event(
                            session_metadata.session_id,
                            event,
                            payload,
                            cwd=current_cwd,
                            project_path=session_metadata.session.project_path,
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
                elif payload_type in CODEX_METADATA_EVENT_TYPES:
                    increment_count(expected_ignored_counts, f"event_msg.{payload_type}")
                    compacted_record_count += payload_type == "context_compacted"
                elif payload_type == "turn_aborted":
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "codex_turn_aborted",
                            "Codex turn ended with an abort signal",
                            {"payload_type": payload_type},
                        )
                    )
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
            elif record_type == "turn_context":
                current_cwd = string_value(payload.get("cwd")) or current_cwd
                continue
            elif record_type == "session_meta":
                continue
            elif record_type == "compacted":
                compacted_record_count += 1
            elif record_type in CODEX_METADATA_RECORD_TYPES:
                increment_count(expected_ignored_counts, f"record.{record_type}")
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

        normalize_response_tools_and_commands(
            bundle,
            source,
            session_metadata.session_id,
            response_calls,
            response_outputs,
            legacy_commands,
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
    "command_run_from_response_item",
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


def normalize_response_tools_and_commands(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    session_id: str,
    response_calls: list[tuple[int, RawEvent, dict[str, Any]]],
    response_outputs: list[tuple[int, RawEvent, dict[str, Any]]],
    legacy_commands: list[tuple[int, RawEvent, dict[str, Any]]],
) -> None:
    calls_by_id: dict[str, list[tuple[int, RawEvent, dict[str, Any]]]] = {}
    outputs_by_id: dict[str, list[tuple[int, RawEvent, dict[str, Any]]]] = {}
    calls_without_id: list[tuple[int, RawEvent, dict[str, Any]]] = []
    outputs_without_id: list[tuple[int, RawEvent, dict[str, Any]]] = []
    for record in response_calls:
        native_call_id = string_value(record[2].get("call_id"))
        if native_call_id:
            calls_by_id.setdefault(native_call_id, []).append(record)
        else:
            calls_without_id.append(record)
    for record in response_outputs:
        native_call_id = string_value(record[2].get("call_id"))
        if native_call_id:
            outputs_by_id.setdefault(native_call_id, []).append(record)
        else:
            outputs_without_id.append(record)

    legacy_call_ids = normalize_legacy_commands(bundle, source, session_id, legacy_commands)

    for record_index, event, payload in calls_without_id:
        bundle.tool_calls.append(tool_call_from_response_item(session_id, event, payload))
        if response_command_spec(payload) is not None:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "invalid_codex_response_command",
                    "Recognized Codex response command is missing a call ID",
                    {"reason": "missing_call_id"},
                )
            )

    for record_index, event, payload in outputs_without_id:
        bundle.tool_results.append(
            tool_result_from_response_item(
                session_id,
                event,
                payload,
                link_tool_call=False,
            )
        )
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "orphan_codex_tool_result",
                "Codex response-item tool result has no call ID",
                {"result_count": 1},
            )
        )

    for native_call_id in sorted(set(calls_by_id) | set(outputs_by_id)):
        calls = calls_by_id.get(native_call_id, [])
        outputs = outputs_by_id.get(native_call_id, [])
        if len(calls) > 1:
            record_index = calls[0][0]
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "ambiguous_codex_tool_call",
                    "Codex native call ID is used by multiple tool calls",
                    {"call_count": len(calls), "result_count": len(outputs)},
                )
            )
            continue
        if not calls:
            for record_index, event, payload in outputs:
                bundle.tool_results.append(
                    tool_result_from_response_item(
                        session_id,
                        event,
                        payload,
                        link_tool_call=False,
                    )
                )
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "orphan_codex_tool_result",
                        "Codex response-item tool result has no matching call",
                        {"result_count": len(outputs)},
                    )
                )
            continue

        record_index, event, payload = calls[0]
        spec = response_command_spec(payload)
        bundle.tool_calls.append(tool_call_from_response_item(session_id, event, payload))
        for _, output_event, output_payload in outputs:
            result = tool_result_from_response_item(session_id, output_event, output_payload)
            if spec is not None and spec.kind == "exec_command" and len(outputs) == 1:
                command_output = response_command_output(spec.kind, output_payload)
                if command_output.outcome == "exited":
                    result = result.model_copy(update={"is_error": command_output.exit_code != 0})
            bundle.tool_results.append(result)
        if not outputs:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "missing_codex_tool_result",
                    "Codex response-item tool call has no matching result",
                    {"call_count": 1, "result_count": 0},
                )
            )
        elif len(outputs) > 1:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "ambiguous_codex_tool_results",
                    "Codex response-item tool call has multiple matching results",
                    {"call_count": 1, "result_count": len(outputs)},
                )
            )

        if spec is None or native_call_id in legacy_call_ids:
            continue
        if spec.command is None:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    record_index,
                    "invalid_codex_response_command",
                    "Codex response command arguments do not satisfy the native contract",
                    {"reason": spec.invalid_reason, "execution_kind": spec.kind},
                )
            )
            continue
        output_record = (outputs[0][1], outputs[0][2]) if len(outputs) == 1 else None
        bundle.command_runs.append(
            command_run_from_response_item(
                session_id,
                event,
                native_call_id,
                spec,
                output_record,
                unavailable_outcome=("ambiguous" if len(outputs) > 1 else "missing"),
            )
        )


def normalize_legacy_commands(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    session_id: str,
    legacy_commands: list[tuple[int, RawEvent, dict[str, Any]]],
) -> set[str]:
    by_call_id: dict[str, list[tuple[int, RawEvent, dict[str, Any]]]] = {}
    without_call_id: list[tuple[int, RawEvent, dict[str, Any]]] = []
    for record in legacy_commands:
        native_call_id = string_value(record[2].get("call_id"))
        if native_call_id:
            by_call_id.setdefault(native_call_id, []).append(record)
        else:
            without_call_id.append(record)
    for _, event, payload in without_call_id:
        bundle.command_runs.append(command_run_from_event_msg(session_id, event, payload))
    observed_call_ids = set(by_call_id)
    for _native_call_id, records in sorted(by_call_id.items()):
        if len(records) > 1:
            bundle.parse_warnings.append(
                warning_for_record(
                    source,
                    records[0][0],
                    "ambiguous_codex_legacy_commands",
                    "Codex native call ID is used by multiple legacy command records",
                    {"command_count": len(records)},
                )
            )
            continue
        bundle.command_runs.append(
            command_run_from_event_msg(session_id, records[0][1], records[0][2])
        )
    return observed_call_ids
