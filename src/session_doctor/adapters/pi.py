from __future__ import annotations

import json
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

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        valid_records, malformed_warnings = read_pi_jsonl(source, source_path)
        session_metadata = extract_session_metadata(source, source_path, valid_records)
        bundle = ParsedSessionBundle(
            session=session_metadata.session,
            parse_warnings=malformed_warnings,
        )
        metadata_only_counts: dict[str, int] = {}
        tool_call_arguments_by_id: dict[str, dict[str, Any]] = {}

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
                        )
                        bundle.tool_calls.append(tool_call)
                        if tool_call.native_tool_call_id:
                            tool_call_arguments_by_id[tool_call.native_tool_call_id] = dict_value(
                                block.get("arguments")
                            )
                        bundle.file_activities.extend(
                            file_activities_from_tool_call(
                                session_metadata.session_id,
                                event,
                                block,
                                block_index,
                            )
                        )
                    usage = model_usage_from_message(session_metadata.session_id, event, record)
                    if usage:
                        bundle.model_usage.append(usage)
                elif string_value(message_payload.get("role")) == "toolResult":
                    bundle.tool_results.append(
                        tool_result_from_message(session_metadata.session_id, event, record)
                    )
                    command_run = command_run_from_tool_result(
                        session_metadata.session_id,
                        event,
                        record,
                        tool_call_arguments_by_id,
                    )
                    if command_run:
                        bundle.command_runs.append(command_run)
                elif string_value(message_payload.get("role")) == "bashExecution":
                    bundle.command_runs.append(
                        command_run_from_bash_execution(session_metadata.session_id, event, record)
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


class PiSessionMetadata:
    def __init__(self, session: Session, session_id: str) -> None:
        self.session = session
        self.session_id = session_id


def read_pi_jsonl(
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
                            "Pi record is not a JSON object",
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
                message=f"Unable to read Pi source: {exc}",
                metadata={"source_path": str(source_path)},
            )
        )
    return records, warnings


def extract_session_metadata(
    source: SessionSource,
    source_path: Path,
    records: list[tuple[int, dict[str, Any]]],
) -> PiSessionMetadata:
    session_record: dict[str, Any] = {}
    model_changes: list[dict[str, Any]] = []
    timestamps: list[datetime] = []

    for _, record in records:
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp:
            timestamps.append(timestamp)
        record_type = string_value(record.get("type"))
        if record_type == "session":
            session_record = record
        elif record_type == "model_change":
            model_changes.append(record)

    latest_model_change = model_changes[-1] if model_changes else {}
    native_session_id = string_value(session_record.get("id")) or session_id_from_filename(
        source_path
    )
    session_id = stable_id("session", AgentName.PI.value, source.source_path, native_session_id)
    cwd = string_value(session_record.get("cwd")) or cwd_from_source_path(source_path)
    model = string_value(latest_model_change.get("modelId"))

    session = Session(
        session_id=session_id,
        source_id=source.source_id,
        agent_name=AgentName.PI,
        native_session_id=native_session_id,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        cwd=cwd,
        project_path=cwd,
        agent_version=string_value(session_record.get("version")),
        model_provider=string_value(latest_model_change.get("provider")),
        model=model,
        metadata={
            "source_path": source.source_path,
            "model_changes": [
                {
                    "provider": string_value(record.get("provider")),
                    "model": string_value(record.get("modelId")),
                    "timestamp": string_value(record.get("timestamp")),
                }
                for record in model_changes
            ],
        },
    )
    return PiSessionMetadata(session=session, session_id=session_id)


def raw_event_for_record(
    source: SessionSource,
    session_id: str,
    record_index: int,
    record: dict[str, Any],
) -> RawEvent:
    message_payload = dict_value(record.get("message"))
    return RawEvent(
        event_id=stable_id("event", session_id, source.source_path, record_index),
        source_id=source.source_id,
        agent_name=AgentName.PI,
        record_index=record_index,
        native_event_type=string_value(record.get("type")),
        native_event_id=string_value(record.get("id")),
        native_parent_id=string_value(record.get("parentId")),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        payload_hash=hash_json(record),
        metadata={
            "payload_keys": sorted(record.keys()),
            "message_role": string_value(message_payload.get("role")),
        },
    )


def message_from_record(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> Message:
    message_payload = dict_value(record.get("message"))
    text, content_block_types = text_and_block_types(message_payload.get("content"))
    role = normalize_pi_role(string_value(message_payload.get("role")))
    timestamp = string_value(message_payload.get("timestamp")) or string_value(
        record.get("timestamp")
    )
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(record.get("id")),
        parent_message_id=string_value(record.get("parentId")),
        timestamp=parse_timestamp(timestamp),
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=content_block_types,
        metadata={
            "pi_message_role": string_value(message_payload.get("role")),
            "stop_reason": string_value(message_payload.get("stopReason")),
            "model": string_value(message_payload.get("model")),
            "provider": string_value(message_payload.get("provider")),
        },
    )


def tool_call_from_block(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
) -> ToolCall:
    call_id = string_value(block.get("id"))
    arguments = dict_value(block.get("arguments"))
    arguments_json = json.dumps(arguments, sort_keys=True, default=str) if arguments else None
    return ToolCall(
        tool_call_id=stable_id("tool_call", session_id, call_id or event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        name=string_value(block.get("name")) or "unknown",
        timestamp=event.timestamp,
        arguments_hash=hash_text(arguments_json) if arguments_json else None,
        metadata={
            "partial_json": string_value(block.get("partialJson")) is not None,
            "argument_keys": sorted(arguments.keys()),
            "path": string_value(arguments.get("path")),
            "timeout": arguments.get("timeout"),
        },
    )


def tool_result_from_message(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> ToolResult:
    message_payload = dict_value(record.get("message"))
    call_id = string_value(message_payload.get("toolCallId"))
    output = text_from_content(message_payload.get("content"))
    details = dict_value(message_payload.get("details"))
    return ToolResult(
        tool_result_id=stable_id("tool_result", session_id, call_id or event.event_id),
        session_id=session_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        timestamp=event.timestamp,
        is_error=bool_value(message_payload.get("isError")),
        output_hash=hash_text(output) if output is not None else None,
        output_length=text_length(output),
        metadata={
            "tool_name": string_value(message_payload.get("toolName")),
            "details_keys": sorted(details.keys()),
            "truncation": details.get("truncation"),
        },
    )


def command_run_from_tool_result(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
    tool_call_arguments_by_id: dict[str, dict[str, Any]],
) -> CommandRun | None:
    message_payload = dict_value(record.get("message"))
    if string_value(message_payload.get("toolName")) != "bash":
        return None
    call_id = string_value(message_payload.get("toolCallId"))
    arguments = tool_call_arguments_by_id.get(call_id or "", {})
    command = string_value(arguments.get("command"))
    if command is None:
        return None
    output = text_from_content(message_payload.get("content")) or ""
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=command,
        ended_at=event.timestamp,
        exit_code=1 if bool_value(message_payload.get("isError")) else 0,
        stdout_hash=hash_text(output) if output else None,
        output_length=len(output),
        metadata={
            "source": "toolResult",
            "tool_name": "bash",
            "is_error": bool_value(message_payload.get("isError")),
        },
    )


def command_run_from_bash_execution(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> CommandRun:
    message_payload = dict_value(record.get("message"))
    output = string_value(message_payload.get("output")) or ""
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        command=string_value(message_payload.get("command")) or "",
        ended_at=event.timestamp,
        exit_code=int_value(message_payload.get("exitCode")),
        stdout_hash=hash_text(output) if output else None,
        output_length=len(output),
        metadata={
            "source": "bashExecution",
            "cancelled": bool_value(message_payload.get("cancelled")),
            "truncated": bool_value(message_payload.get("truncated")),
            "exclude_from_context": bool_value(message_payload.get("excludeFromContext")),
        },
    )


def file_activities_from_tool_call(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
) -> list[FileActivity]:
    tool_name = string_value(block.get("name"))
    if tool_name not in {"edit", "read", "write"}:
        return []
    arguments = dict_value(block.get("arguments"))
    path = string_value(arguments.get("path"))
    if path is None:
        return []
    content_payload = file_content_payload(tool_name, arguments)
    return [
        FileActivity(
            file_activity_id=stable_id(
                "file_activity",
                session_id,
                event.event_id,
                string_value(block.get("id")) or block_index,
                tool_name,
                path,
            ),
            session_id=session_id,
            source_event_id=event.event_id,
            path=path,
            operation=tool_name,
            timestamp=event.timestamp,
            content_hash=hash_text(content_payload) if content_payload else None,
            metadata={
                "tool_call_id": string_value(block.get("id")),
                "argument_keys": sorted(arguments.keys()),
                "content_length": text_length(content_payload),
            },
        )
    ]


def model_usage_from_message(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> ModelUsage | None:
    message_payload = dict_value(record.get("message"))
    usage = dict_value(message_payload.get("usage"))
    if not usage:
        return None
    return ModelUsage(
        model_usage_id=stable_id("model_usage", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        timestamp=event.timestamp,
        provider=string_value(message_payload.get("provider")),
        model=string_value(message_payload.get("model")),
        input_tokens=int_value(usage.get("input")),
        output_tokens=int_value(usage.get("output")),
        cache_read_tokens=int_value(usage.get("cacheRead")),
        cache_write_tokens=int_value(usage.get("cacheWrite")),
        total_tokens=int_value(usage.get("totalTokens")),
        metadata={
            "cost": usage.get("cost"),
            "stop_reason": string_value(message_payload.get("stopReason")),
        },
    )


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
        if block_type == "text":
            block_text = string_value(typed_block.get("text"))
            if block_text is not None:
                texts.append(block_text)

    return "\n".join(texts) if texts else None, block_types


def text_from_content(content: object) -> str | None:
    text, _ = text_and_block_types(content)
    return text


def content_blocks(content: object) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [dict_value(block) for block in content if isinstance(block, dict)]


def file_content_payload(tool_name: str | None, arguments: dict[str, Any]) -> str | None:
    if tool_name == "write":
        return string_value(arguments.get("content"))
    if tool_name != "edit":
        return None
    edits = arguments.get("edits")
    if not isinstance(edits, list):
        return None
    safe_edits = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        edit_payload = dict_value(edit)
        safe_edits.append(
            {
                "old_length": text_length(string_value(edit_payload.get("old_string"))),
                "new_length": text_length(string_value(edit_payload.get("new_string"))),
            }
        )
    return json.dumps(safe_edits, sort_keys=True) if safe_edits else None


def normalize_pi_role(role: str | None) -> NormalizedRole:
    if role == "user":
        return NormalizedRole.USER
    if role == "assistant":
        return NormalizedRole.ASSISTANT
    if role == "toolResult":
        return NormalizedRole.TOOL
    if role == "bashExecution":
        return NormalizedRole.TOOL
    return NormalizedRole.UNKNOWN


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


def increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def hash_json(value: object) -> str:
    return hash_text(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def session_id_from_filename(path: Path) -> str | None:
    stem = path.stem
    if "_" not in stem:
        return stem or None
    return stem.rsplit("_", maxsplit=1)[-1]


def cwd_from_source_path(path: Path) -> str | None:
    parent_name = path.parent.name
    if not parent_name.startswith("--") or not parent_name.endswith("--"):
        return None
    candidate = parent_name.removeprefix("--").removesuffix("--").replace("-", "/")
    return f"/{candidate.strip('/')}" if candidate else None


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def int_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def bool_value(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def dict_value(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
