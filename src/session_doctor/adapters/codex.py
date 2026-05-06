from __future__ import annotations

import json
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from session_doctor.ids import source_id_for_path, stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    ModelUsage,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    SessionSource,
    SourceKind,
    ToolCall,
    ToolResult,
)

from .base import BaseAdapter, ParsedSessionBundle

CODEX_MESSAGE_SOURCE_RESPONSE_ITEM = "response_item"
CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK = "event_msg_fallback"


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


class CodexSessionMetadata:
    def __init__(self, session: Session, session_id: str) -> None:
        self.session = session
        self.session_id = session_id


def read_codex_jsonl(
    source: SessionSource,
    source_path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], list[ParseWarning]]:
    records: list[tuple[int, dict[str, Any]]] = []
    warnings: list[ParseWarning] = []
    try:
        with source_path.open(encoding="utf-8") as file:
            for record_index, line in enumerate(file):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "malformed_json",
                            f"Malformed JSONL record: {exc.msg}",
                            {"line": exc.lineno, "column": exc.colno},
                        )
                    )
                    continue
                if not isinstance(parsed, dict):
                    warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "non_object_record",
                            "Codex record is not a JSON object",
                            {"json_type": type(parsed).__name__},
                        )
                    )
                    continue
                records.append((record_index, parsed))
    except OSError as exc:
        warnings.append(
            ParseWarning(
                warning_id=stable_id("warning", source.source_id, "source_open_error"),
                source_id=source.source_id,
                message=f"Unable to read Codex source: {exc}",
                metadata={"source_path": str(source_path)},
            )
        )
    return records, warnings


def extract_session_metadata(
    source: SessionSource,
    source_path: Path,
    records: list[tuple[int, dict[str, Any]]],
) -> CodexSessionMetadata:
    session_meta: dict[str, Any] = {}
    turn_contexts: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    has_compaction = False

    for _, record in records:
        record_type = string_value(record.get("type"))
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp:
            timestamps.append(timestamp)
        payload = dict_value(record.get("payload"))
        if record_type == "session_meta":
            session_meta = payload
        elif record_type == "turn_context":
            turn_contexts.append(payload)
        elif record_type == "compacted":
            has_compaction = True

    latest_turn = turn_contexts[-1] if turn_contexts else {}
    native_session_id = string_value(session_meta.get("id")) or session_id_from_filename(
        source_path
    )
    session_id = stable_id("session", AgentName.CODEX.value, source.source_path, native_session_id)
    cwd = string_value(latest_turn.get("cwd")) or string_value(session_meta.get("cwd"))
    model = string_value(latest_turn.get("model")) or string_value(session_meta.get("model"))

    session = Session(
        session_id=session_id,
        source_id=source.source_id,
        agent_name=AgentName.CODEX,
        native_session_id=native_session_id,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        cwd=cwd,
        project_path=cwd,
        agent_version=string_value(session_meta.get("cli_version")),
        model_provider=string_value(session_meta.get("model_provider")),
        model=model,
        metadata={
            "source_path": source.source_path,
            "originator": string_value(session_meta.get("originator")),
            "source": string_value(session_meta.get("source")),
            "has_compaction": has_compaction,
        },
    )
    return CodexSessionMetadata(session=session, session_id=session_id)


def raw_event_for_record(
    source: SessionSource,
    session_id: str,
    record_index: int,
    record: dict[str, Any],
) -> RawEvent:
    payload = dict_value(record.get("payload"))
    payload_type = string_value(payload.get("type"))
    return RawEvent(
        event_id=stable_id("event", session_id, source.source_path, record_index),
        source_id=source.source_id,
        agent_name=AgentName.CODEX,
        record_index=record_index,
        native_event_type=string_value(record.get("type")),
        native_event_id=string_value(payload.get("id")) or string_value(payload.get("call_id")),
        native_parent_id=(
            string_value(payload.get("parent_id")) or string_value(payload.get("turn_id"))
        ),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        payload_hash=hash_json(record),
        metadata={
            "payload_type": payload_type,
            "payload_keys": sorted(payload.keys()),
        },
    )


def message_from_response_item(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
    timestamp: str | None,
) -> Message:
    text, content_block_types = text_and_block_types(payload.get("content"))
    role = normalize_codex_role(string_value(payload.get("role")))
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(payload.get("id")),
        timestamp=parse_timestamp(timestamp),
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=content_block_types,
        metadata={
            "phase": string_value(payload.get("phase")),
            "codex_message_source": CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
        },
    )


def message_from_event_msg_fallback(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> Message:
    payload_type = string_value(payload.get("type"))
    text = string_value(payload.get("message")) or string_value(payload.get("text"))
    role = NormalizedRole.USER if payload_type == "user_message" else NormalizedRole.ASSISTANT
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(payload.get("id")),
        timestamp=event.timestamp,
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=["event_msg_text"] if text is not None else [],
        metadata={"codex_message_source": CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK},
    )


def tool_call_from_response_item(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolCall:
    call_id = string_value(payload.get("call_id"))
    arguments = string_value(payload.get("arguments")) or string_value(payload.get("input"))
    return ToolCall(
        tool_call_id=stable_id("tool_call", session_id, call_id or event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        name=string_value(payload.get("name")) or "unknown",
        timestamp=event.timestamp,
        arguments_hash=hash_text(arguments) if arguments is not None else None,
        metadata={
            "payload_type": string_value(payload.get("type")),
            "status": string_value(payload.get("status")),
            "argument_keys": argument_keys(arguments),
        },
    )


def tool_call_from_web_search_call(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolCall:
    action = dict_value(payload.get("action"))
    action_json = json.dumps(action, sort_keys=True, default=str)
    return ToolCall(
        tool_call_id=stable_id("tool_call", session_id, "web_search", event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        name="web_search",
        timestamp=event.timestamp,
        arguments_hash=hash_text(action_json) if action else None,
        metadata={
            "payload_type": string_value(payload.get("type")),
            "status": string_value(payload.get("status")),
            "query": string_value(action.get("query")),
            "action_type": string_value(action.get("type")),
        },
    )


def tool_result_from_response_item(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolResult:
    call_id = string_value(payload.get("call_id"))
    output = string_value(payload.get("output"))
    return ToolResult(
        tool_result_id=stable_id(
            "tool_result",
            session_id,
            call_id or event.event_id,
            event.event_id,
        ),
        session_id=session_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        timestamp=event.timestamp,
        is_error=string_value(payload.get("status")) == "failed",
        output_hash=hash_text(output) if output is not None else None,
        output_length=text_length(output),
        metadata={
            "payload_type": string_value(payload.get("type")),
            "status": string_value(payload.get("status")),
        },
    )


def tool_result_from_web_search_end(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolResult:
    call_id = string_value(payload.get("call_id"))
    query = string_value(payload.get("query"))
    return ToolResult(
        tool_result_id=stable_id("tool_result", session_id, call_id or event.event_id),
        session_id=session_id,
        tool_call_id=None,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        timestamp=event.timestamp,
        is_error=False,
        output_hash=hash_text(query) if query else None,
        output_length=text_length(query),
        metadata={
            "payload_type": string_value(payload.get("type")),
            "tool_name": "web_search",
            "query": query,
            "action_type": string_value(dict_value(payload.get("action")).get("type")),
        },
    )


def command_run_from_event_msg(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> CommandRun:
    stdout, stderr, output_source = command_output_parts(payload)
    call_id = string_value(payload.get("call_id"))
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=command_text(payload.get("command")),
        cwd=string_value(payload.get("cwd")),
        ended_at=event.timestamp,
        exit_code=int_value(payload.get("exit_code")),
        stdout_hash=hash_text(stdout) if stdout else None,
        stderr_hash=hash_text(stderr) if stderr else None,
        output_length=len(stdout) + len(stderr),
        metadata={
            "status": string_value(payload.get("status")),
            "duration": payload.get("duration"),
            "process_id": payload.get("process_id"),
            "output_source": output_source,
        },
    )


def file_activities_from_patch_event(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> list[FileActivity]:
    changes = dict_value(payload.get("changes"))
    activities: list[FileActivity] = []
    for path, change_payload in changes.items():
        change = dict_value(change_payload)
        diff = string_value(change.get("unified_diff"))
        activities.append(
            FileActivity(
                file_activity_id=stable_id("file_activity", session_id, event.event_id, path),
                session_id=session_id,
                source_event_id=event.event_id,
                path=path,
                operation=string_value(change.get("type")) or "patch",
                timestamp=event.timestamp,
                content_hash=hash_text(diff) if diff is not None else None,
                metadata={
                    "success": bool_value(payload.get("success")),
                    "status": string_value(payload.get("status")),
                    "diff_length": text_length(diff),
                    "move_path": string_value(change.get("move_path")),
                },
            )
        )
    if not activities:
        activities.append(
            FileActivity(
                file_activity_id=stable_id("file_activity", session_id, event.event_id, "unknown"),
                session_id=session_id,
                source_event_id=event.event_id,
                path="unknown",
                operation="patch",
                timestamp=event.timestamp,
                metadata={
                    "success": bool_value(payload.get("success")),
                    "status": string_value(payload.get("status")),
                },
            )
        )
    return activities


def model_usage_from_token_count(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ModelUsage:
    info = dict_value(payload.get("info"))
    last_usage = dict_value(info.get("last_token_usage"))
    total_usage = dict_value(info.get("total_token_usage"))
    usage = last_usage or total_usage
    return ModelUsage(
        model_usage_id=stable_id("model_usage", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        timestamp=event.timestamp,
        input_tokens=int_value(usage.get("input_tokens")),
        output_tokens=int_value(usage.get("output_tokens")),
        cache_read_tokens=int_value(usage.get("cached_input_tokens")),
        total_tokens=int_value(usage.get("total_tokens")),
        metadata={
            "payload_type": string_value(payload.get("type")),
            "model_context_window": info.get("model_context_window"),
            "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
            "total_token_usage": total_usage,
            "rate_limits": payload.get("rate_limits"),
        },
    )


def warning_for_record(
    source: SessionSource,
    record_index: int,
    code: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ParseWarning:
    return ParseWarning(
        warning_id=stable_id("warning", source.source_id, record_index, code),
        source_id=source.source_id,
        record_index=record_index,
        message=message,
        metadata={"code": code, **(metadata or {})},
    )


def message_identity(message: Message) -> tuple[NormalizedRole, str | None]:
    return (message.role, message.text_hash)


def has_nearby_response_message(
    record_index: int,
    identity: tuple[NormalizedRole, str | None],
    response_messages: list[tuple[int, tuple[NormalizedRole, str | None]]],
) -> bool:
    return any(
        response_identity == identity and abs(response_record_index - record_index) <= 1
        for response_record_index, response_identity in response_messages
    )


def increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def text_and_block_types(content: object) -> tuple[str | None, list[str]]:
    if isinstance(content, str):
        return content, ["text"]
    if not isinstance(content, list):
        return None, []

    texts: list[str] = []
    block_types: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        typed_block = dict_value(block)
        block_type = string_value(typed_block.get("type"))
        if block_type:
            block_types.append(block_type)
        block_text = string_value(typed_block.get("text"))
        if block_text is not None:
            texts.append(block_text)

    return "\n".join(texts) if texts else None, block_types


def normalize_codex_role(role: str | None) -> NormalizedRole:
    if role == "user":
        return NormalizedRole.USER
    if role == "assistant":
        return NormalizedRole.ASSISTANT
    if role == "developer":
        return NormalizedRole.DEVELOPER
    return NormalizedRole.UNKNOWN


def argument_keys(arguments: str | None) -> list[str]:
    if arguments is None:
        return []
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return sorted(str(key) for key in parsed)
    return []


def command_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return shlex.join(str(part) for part in value)
    return ""


def command_output_parts(payload: dict[str, Any]) -> tuple[str, str, str]:
    stdout = string_value(payload.get("stdout")) or ""
    stderr = string_value(payload.get("stderr")) or ""
    if stdout or stderr:
        return stdout, stderr, "stdout_stderr"

    aggregated_output = string_value(payload.get("aggregated_output")) or ""
    if aggregated_output:
        return aggregated_output, "", "aggregated_output"

    formatted_output = string_value(payload.get("formatted_output")) or ""
    if formatted_output:
        return formatted_output, "", "formatted_output"

    return "", "", "empty"


def hash_json(value: object) -> str:
    return hash_text(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def session_id_from_filename(path: Path) -> str | None:
    stem = path.stem
    if "-" not in stem:
        return stem or None
    return stem.rsplit("-", maxsplit=1)[-1]


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def dict_value(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def int_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def bool_value(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
